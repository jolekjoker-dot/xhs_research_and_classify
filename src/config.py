import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

# auto-load .env if present
try:
    from dotenv import load_dotenv
    _env_path = Path(__file__).resolve().parent.parent / ".env"
    if _env_path.exists():
        load_dotenv(_env_path, override=True)
except ImportError:
    pass


@dataclass
class Config:
    project_root: Path = field(
        default_factory=lambda: Path(__file__).resolve().parent.parent
    )
    output_dir: Path = field(default_factory=lambda: Path("output/knowledge_base"))
    config_dir: Path = field(default_factory=lambda: Path("config"))

    # ── LLM Provider ────────────────────────────────────────────
    # Set ANTHROPIC_PROVIDER to "xiaomi" or "deepseek" (default).
    # Each provider has its own env vars for key/model/base_url.

    api_provider: str = field(
        default_factory=lambda: os.getenv("ANTHROPIC_PROVIDER", "deepseek")
    )

    # Per-provider defaults
    _PROVIDER_DEFAULTS: dict = field(default_factory=lambda: {
        "deepseek": {
            "base_url": "https://api.deepseek.com/v1",
            "model": "deepseek-chat",
            "key_env": "DEEPSEEK_API_KEY",
        },
        "xiaomi": {
            "base_url": "https://token-plan-cn.xiaomimimo.com/anthropic",
            "model": "mimo-v2.5",
            "key_env": "XIAOMI_API_KEY",
        },
    })

    @property
    def api_base_url(self) -> str:
        provider = self._PROVIDER_DEFAULTS.get(self.api_provider, self._PROVIDER_DEFAULTS["deepseek"])
        return os.getenv("ANTHROPIC_BASE_URL", provider["base_url"])

    @property
    def api_key(self) -> str:
        provider = self._PROVIDER_DEFAULTS.get(self.api_provider, self._PROVIDER_DEFAULTS["deepseek"])
        # ANTHROPIC_AUTH_TOKEN explicitly set (even to empty) takes priority
        token = os.getenv("ANTHROPIC_AUTH_TOKEN")
        if token is not None:
            return token
        return os.getenv(provider["key_env"], "")

    @property
    def api_model(self) -> str:
        provider = self._PROVIDER_DEFAULTS.get(self.api_provider, self._PROVIDER_DEFAULTS["deepseek"])
        return os.getenv("ANTHROPIC_MODEL", provider["model"])

    # Search defaults
    search_max_results: int = 20
    search_sort: str = "general"  # general, time_descending, popularity

    # Scrape defaults
    scrape_min_delay: float = 3.0
    scrape_max_delay: float = 8.0
    scrape_max_concurrent: int = 1  # XHS requires persistent profile, cannot share across processes
    scrape_timeout: int = 30

    # Knowledge base
    kb_template: str = "default"

    # LLM concurrency
    llm_max_workers: int = 5

    # Neo4j
    neo4j_uri: str = field(
        default_factory=lambda: os.getenv("NEO4J_URI", "bolt://localhost:7687")
    )
    neo4j_user: str = field(
        default_factory=lambda: os.getenv("NEO4J_USER", "neo4j")
    )
    neo4j_password: str = field(
        default_factory=lambda: os.getenv("NEO4J_PASSWORD", "password")
    )

    def load_categories(self) -> dict[str, Any]:
        """加载分类体系配置"""
        path = self.config_dir / "categories.yaml"
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        return data.get("categories", {})

    def load_classification_prompt(self) -> str:
        """加载 LLM 分类提示词"""
        path = self.config_dir / "categories.yaml"
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        return data.get("classification_prompt", "")

    def get_categories_text(self) -> str:
        """生成分类体系的文本描述，用于 LLM prompt"""
        categories = self.load_categories()
        lines = []
        for cat_name, cat_info in categories.items():
            lines.append(f"## {cat_info['label']}")
            for sub in cat_info.get("subcategories", []):
                lines.append(f"  - {sub}")
        return "\n".join(lines)

    def ensure_output_dir(self) -> Path:
        """确保输出目录存在"""
        p = self.project_root / self.output_dir
        p.mkdir(parents=True, exist_ok=True)
        return p


_config_instance: Config | None = None


def get_config() -> Config:
    global _config_instance
    if _config_instance is None:
        _config_instance = Config()
    return _config_instance