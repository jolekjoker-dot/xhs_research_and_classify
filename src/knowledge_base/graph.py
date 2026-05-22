"""Knowledge graph builder — Neo4j graph from classified posts with Cypher queries

Neo4j is optional. Without it, graph export falls back to parsing
knowledge base Markdown files directly.
"""

import json
from pathlib import Path

try:
    from neo4j import GraphDatabase
    from neo4j.exceptions import ServiceUnavailable
    _NEO4J_AVAILABLE = True
except ImportError:
    GraphDatabase = None  # type: ignore
    ServiceUnavailable = Exception  # type: ignore
    _NEO4J_AVAILABLE = False

from src.config import get_config
from src.logger import get_logger
from src.models import ClassifiedPost

log = get_logger(__name__)

GRAPH_JSON_PATH = Path("output/knowledge_base/graph_viz.json")

CATEGORY_COLORS = [
    "#5470c6", "#91cc75", "#fac858", "#ee6666", "#73c0de", "#3ba272",
    "#fc8452", "#9a60b4", "#ea7ccc", "#48b8d0",
]


def _sanitize(s: str) -> str:
    """strip special chars that break Cypher identifiers"""
    return s.replace("'", "").replace('"', "")


class KnowledgeGraph:
    """Neo4j-backed knowledge graph for classified XHS posts

    When Neo4j is unavailable (not installed or not running), all read
    queries return empty results and export_for_viz() falls back to
    file-based graph data generation.
    """

    def __init__(self, uri: str | None = None, user: str | None = None, password: str | None = None):
        config = get_config()
        self.uri = uri or config.neo4j_uri
        self.user = user or config.neo4j_user
        self.password = password or config.neo4j_password
        self.driver = None
        self._available = None
        if _NEO4J_AVAILABLE:
            self.driver = GraphDatabase.driver(self.uri, auth=(self.user, self.password))

    def is_available(self) -> bool:
        if self._available is None:
            if not _NEO4J_AVAILABLE or self.driver is None:
                self._available = False
                return False
            try:
                with self.driver.session() as session:
                    session.run("RETURN 1")
                self._available = True
            except (ServiceUnavailable, OSError):
                log.warning("Neo4j not available at %s — graph features disabled", self.uri)
                self._available = False
        return self._available

    # ── build ─────────────────────────────────────────────────

    def build(self, posts: list[ClassifiedPost]) -> dict:
        """build full graph from classified posts, returns {nodes, edges} count"""
        if not self.is_available():
            return {"nodes": 0, "edges": 0}

        with self.driver.session() as session:
            result = session.execute_write(self._build_tx, posts)
        log.info("Graph built: %d nodes, %d edges", result["nodes"], result["edges"])
        return result

    @staticmethod
    def _build_tx(tx, posts: list[ClassifiedPost]) -> dict:
        # clear previous data
        tx.run("MATCH (n) DETACH DELETE n")

        nodes_created = 0
        edges_created = 0

        for p in posts:
            pid = p.post.post_id
            title = _sanitize(p.post.title or "")
            category = _sanitize(p.category or "未分类")
            sub = _sanitize(p.sub_category or "")
            ts = p.post.publish_time.isoformat() if p.post.publish_time else ""

            # Post node
            tx.run(
                "MERGE (n:Post {id: $id}) "
                "SET n.title = $title, n.category = $cat, n.sub_category = $sub, "
                "n.quality_score = $qs, n.sentiment = $sent, n.publish_date = $ts, "
                "n.likes = $likes, n.collects = $collects, n.comments = $comments, "
                "n.url = $url",
                id=pid, title=title, cat=category, sub=sub,
                qs=p.quality_score, sent=p.sentiment, ts=ts,
                likes=p.post.like_count, collects=p.post.collect_count,
                comments=p.post.comment_count, url=p.post.url,
            )
            nodes_created += 1

            # Category node + edge
            if category:
                tx.run(
                    "MERGE (c:Category {name: $name})", name=category)
                tx.run(
                    "MATCH (p:Post {id: $pid}), (c:Category {name: $name}) "
                    "MERGE (p)-[:BELONGS_TO]->(c)",
                    pid=pid, name=category,
                )
                edges_created += 1

            # Keyword nodes + edges
            for kw in p.keywords[:8]:
                kw_clean = _sanitize(kw)
                if not kw_clean:
                    continue
                tx.run("MERGE (k:Keyword {name: $name})", name=kw_clean)
                tx.run(
                    "MATCH (p:Post {id: $pid}), (k:Keyword {name: $name}) "
                    "MERGE (p)-[:HAS_KEYWORD]->(k)",
                    pid=pid, name=kw_clean,
                )
                edges_created += 1
                nodes_created += 1  # counted once per unique keyword (MERGE)

            # Entity nodes + edges
            for ent in p.entities:
                ent_name = _sanitize(ent.get("name", ent) if isinstance(ent, dict) else str(ent))
                ent_type = _sanitize(ent.get("type", "unknown") if isinstance(ent, dict) else "unknown")
                if not ent_name:
                    continue
                tx.run(
                    "MERGE (e:Entity {name: $name}) SET e.type = $type",
                    name=ent_name, type=ent_type,
                )
                tx.run(
                    "MATCH (p:Post {id: $pid}), (e:Entity {name: $name}) "
                    "MERGE (p)-[:MENTIONS]->(e)",
                    pid=pid, name=ent_name,
                )
                edges_created += 1
                nodes_created += 1

        # Similarity edges: posts sharing >= 2 keywords
        tx.run("""
            MATCH (p1:Post)-[:HAS_KEYWORD]->(k:Keyword)<-[:HAS_KEYWORD]-(p2:Post)
            WHERE id(p1) < id(p2)
            WITH p1, p2, count(k) AS shared
            WHERE shared >= 2
            MERGE (p1)-[:SIMILAR_TO {weight: shared}]->(p2)
        """)

        return {"nodes": nodes_created, "edges": edges_created}

    # ── queries ───────────────────────────────────────────────

    def find_related(self, post_id: str, top_k: int = 5) -> list[dict]:
        """find posts related to the given one via SIMILAR_TO + shared entities"""
        if not self.is_available():
            return []

        with self.driver.session() as session:
            result = session.run(
                """
                MATCH (target:Post {id: $id})
                MATCH (target)-[:SIMILAR_TO|HAS_KEYWORD|MENTIONS]-(other:Post)
                WHERE other.id <> $id
                RETURN other.title AS title, other.id AS id, other.category AS category,
                       other.quality_score AS score, other.url AS url
                ORDER BY score DESC LIMIT $k
                """,
                id=post_id, k=top_k,
            )
            return [r.data() for r in result]

    def get_entity_network(self, entity_name: str, depth: int = 2) -> dict:
        """get subgraph around an entity as {nodes, edges} for visualization"""
        if not self.is_available():
            return {"nodes": [], "edges": []}

        with self.driver.session() as session:
            result = session.run(
                """
                MATCH (e:Entity {name: $name})
                MATCH path = (e)-[*1..""" + str(depth) + """]-(n)
                RETURN path LIMIT 200
                """,
                name=entity_name,
            )
            nodes, edges = _paths_to_json(result)
        return {"nodes": nodes, "edges": edges}

    def search(self, query: str, top_k: int = 20) -> list[dict]:
        """fuzzy search nodes by name, return matching posts"""
        if not self.is_available():
            return []

        with self.driver.session() as session:
            result = session.run(
                """
                MATCH (p:Post)
                WHERE p.title CONTAINS $q OR p.category CONTAINS $q
                OPTIONAL MATCH (p)-[:HAS_KEYWORD]->(k:Keyword)
                OPTIONAL MATCH (p)-[:MENTIONS]->(e:Entity)
                RETURN p.title AS title, p.id AS id, p.category AS category,
                       p.quality_score AS score, p.url AS url,
                       collect(DISTINCT k.name) AS keywords,
                       collect(DISTINCT e.name) AS entities
                ORDER BY p.quality_score DESC LIMIT $k
                """,
                q=query, k=top_k,
            )
            return [r.data() for r in result]

    # ── export ────────────────────────────────────────────────

    def export_for_viz(self, path: Path | None = None) -> str:
        """export all posts and their relations as JSON for ECharts visualization

        Falls back to a file-based export (no Neo4j required) when database
        is unavailable, reading directly from knowledge base MD metadata.
        """
        out = path or GRAPH_JSON_PATH

        if self.is_available():
            data = self._export_from_neo4j()
        else:
            data = self._export_from_files()

        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        log.info("Graph viz data exported → %s (%d nodes, %d edges)",
                 out, len(data["nodes"]), len(data["edges"]))
        return str(out)

    def _export_from_neo4j(self) -> dict:
        with self.driver.session() as session:
            nodes_result = session.run(
                "MATCH (p:Post) "
                "OPTIONAL MATCH (p)-[:HAS_KEYWORD]->(k:Keyword) "
                "OPTIONAL MATCH (p)-[:MENTIONS]->(e:Entity) "
                "RETURN p, collect(DISTINCT k.name) AS keywords, "
                "collect(DISTINCT e.name) AS entities"
            )
            nodes = []
            edges = []
            cat_color_idx: dict[str, int] = {}
            for r in nodes_result:
                p = r["p"]
                cat = p.get("category", "未分类")
                if cat not in cat_color_idx:
                    cat_color_idx[cat] = len(cat_color_idx)
                color = CATEGORY_COLORS[cat_color_idx[cat] % len(CATEGORY_COLORS)]
                nodes.append({
                    "id": p["id"],
                    "name": (p.get("title") or p["id"])[:40],
                    "category": cat,
                    "score": p.get("quality_score", 0),
                    "itemStyle": {"color": color},
                    "symbolSize": max(8, (p.get("quality_score", 0) or 0) * 3 + 8),
                })
                for kw in (r.get("keywords") or []):
                    edges.append({"source": p["id"], "target": f"kw:{kw}", "type": "keyword"})
                for ent in (r.get("entities") or []):
                    edges.append({"source": p["id"], "target": f"ent:{ent}", "type": "entity"})

            # similarity edges
            sim_result = session.run(
                "MATCH (p1:Post)-[r:SIMILAR_TO]->(p2:Post) "
                "RETURN p1.id AS source, p2.id AS target, r.weight AS weight"
            )
            for s in sim_result:
                edges.append({
                    "source": s["source"], "target": s["target"],
                    "type": "similar", "weight": s.get("weight", 1),
                })

        return {"nodes": nodes, "edges": edges}

    def _export_from_files(self) -> dict:
        """fallback: build graph data by parsing knowledge base MD files"""
        kb_dir = Path("output/knowledge_base")
        nodes = []
        edges = []
        cat_color_idx: dict[str, int] = {}
        seen_ids: set[str] = set()

        for md_file in kb_dir.rglob("*.md"):
            if md_file.name.startswith("_") or md_file.name == "INDEX.md":
                continue
            text = md_file.read_text(encoding="utf-8")
            meta = _parse_frontmatter(text)
            pid = md_file.stem.rsplit("_", 1)[-1] if "_" in md_file.stem else md_file.stem
            title = meta.get("title", md_file.stem)[:40]
            cat = meta.get("category", "未分类")

            if cat not in cat_color_idx:
                cat_color_idx[cat] = len(cat_color_idx)
            color = CATEGORY_COLORS[cat_color_idx[cat] % len(CATEGORY_COLORS)]

            score = float(meta.get("quality_score", 0) or 0)
            if pid not in seen_ids:
                seen_ids.add(pid)
                nodes.append({
                    "id": pid,
                    "name": title,
                    "category": cat,
                    "score": score,
                    "itemStyle": {"color": color},
                    "symbolSize": max(30, score * 5 + 30),
                })

            # keyword edges — parse JSON array or comma-separated string
            kws = _parse_list_field(meta.get("keywords", ""))
            for kw in kws:
                if kw:
                    kw_id = f"kw:{kw}"
                    edges.append({"source": pid, "target": kw_id, "type": "keyword"})
                    if kw_id not in seen_ids:
                        seen_ids.add(kw_id)
                        nodes.append({
                            "id": kw_id, "name": kw, "category": "关键词",
                            "score": 0, "itemStyle": {"color": "#fac858"},
                            "symbolSize": 16, "label": {"show": True, "fontSize": 10},
                        })

            # entity edges
            ents = _parse_list_field(meta.get("entities", ""))
            for ent in ents:
                if ent:
                    ent_id = f"ent:{ent}"
                    edges.append({"source": pid, "target": ent_id, "type": "entity"})
                    if ent_id not in seen_ids:
                        seen_ids.add(ent_id)
                        nodes.append({
                            "id": ent_id, "name": ent, "category": "实体",
                            "score": 0, "itemStyle": {"color": "#73c0de"},
                            "symbolSize": 16, "label": {"show": True, "fontSize": 10},
                        })

        return {"nodes": nodes, "edges": edges}

    def close(self):
        if self.driver is not None:
            self.driver.close()


def _parse_list_field(value: str) -> list[str]:
    """parse a frontmatter field that may be a JSON array or comma-separated string"""
    if not value:
        return []
    value = value.strip()
    if value.startswith("[") and value.endswith("]"):
        try:
            items = json.loads(value)
            return [str(item).strip() for item in items if str(item).strip()]
        except json.JSONDecodeError:
            pass
    return [x.strip() for x in value.replace("，", ",").split(",") if x.strip()]


def _parse_frontmatter(text: str) -> dict:
    meta = {}
    if text.startswith("---"):
        end = text.find("---", 3)
        if end > 0:
            for line in text[3:end].strip().split("\n"):
                if ":" in line:
                    key, _, val = line.partition(":")
                    meta[key.strip()] = val.strip().strip('"')
    return meta


def _paths_to_json(result) -> tuple[list, list]:
    nodes, edges = [], []
    seen_nodes: set[str] = set()
    seen_edges: set[str] = set()
    for record in result:
        path = record["path"]
        for node in path.nodes:
            nid = str(node.id)
            if nid not in seen_nodes:
                seen_nodes.add(nid)
                labels = list(node.labels)
                nodes.append({
                    "id": nid,
                    "name": node.get("title", node.get("name", nid)),
                    "type": labels[0] if labels else "",
                })
        for rel in path.relationships:
            eid = f"{rel.start_node.id}-{rel.type}-{rel.end_node.id}"
            if eid not in seen_edges:
                seen_edges.add(eid)
                edges.append({
                    "source": str(rel.start_node.id),
                    "target": str(rel.end_node.id),
                    "type": rel.type,
                })
    return nodes, edges
