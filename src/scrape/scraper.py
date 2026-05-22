import json
import random
import re
import shutil
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import httpx
from urllib.parse import quote
from playwright.sync_api import Page, sync_playwright, TimeoutError as PwTimeout

from src.config import get_config
from src.logger import get_logger
from src.models import SearchResult, XHSPost

log = get_logger(__name__)

USER_DATA_DIR = Path.home() / ".xhs_browser_profile"
CHECKPOINT_FILE = Path("output/checkpoint_scrape.json")
IMAGE_DIR = Path("output/knowledge_base/images")
STORAGE_STATE_FILE = Path("output/xhs_storage.json")


class XHSScrapeError(Exception):
    pass


class XHSPostNotFound(XHSScrapeError):
    pass


# ── checkpoint helpers ───────────────────────────────────────────


def _load_checkpoint() -> set[str]:
    """load set of already-scraped post IDs"""
    if CHECKPOINT_FILE.exists():
        data = json.loads(CHECKPOINT_FILE.read_text(encoding="utf-8"))
        return set(data.get("scraped_ids", []))
    return set()


def _save_checkpoint(post_id: str) -> None:
    """mark a post ID as scraped"""
    CHECKPOINT_FILE.parent.mkdir(parents=True, exist_ok=True)
    scraped = _load_checkpoint()
    scraped.add(post_id)
    CHECKPOINT_FILE.write_text(
        json.dumps(
            {"scraped_ids": list(scraped), "updated": datetime.now().isoformat()},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


# ── content extraction ───────────────────────────────────────────


def _wait_for_content(page: Page, timeout: int = 15000) -> bool:
    """wait for post content to render"""
    selectors = [
        "#detail-desc",
        ".note-text",
        "[class*='desc']",
        ".content",
        "article",
    ]
    for sel in selectors:
        try:
            page.wait_for_selector(sel, timeout=timeout)
            log.debug("Content loaded (selector: %s)", sel)
            return True
        except PwTimeout:
            continue
    return False


def _extract_note_detail(page: Page, timeout: int = 15000) -> str:
    """extract full note detail text from the side panel after clicking a card"""
    selectors = [
        "#noteContainer",
        "[class*='note-detail']",
        "[class*='note-container']",
    ]
    # wait for note panel to render
    for sel in selectors:
        try:
            page.wait_for_selector(sel, timeout=timeout)
            break
        except Exception:
            continue

    for sel in selectors:
        el = page.query_selector(sel)
        if el:
            text = el.inner_text().strip()
            if text and len(text) > 20:
                return text
    return ""


def _find_tag_start(lines: list[str], start: int) -> int:
    """find the first line that starts with # (tag section), returns len(lines) if not found"""
    for i in range(start, len(lines)):
        if lines[i].startswith("#"):
            return i
    return len(lines)


def _find_comment_start(lines: list[str], start: int) -> int:
    """find '共 N 条评论' or standalone '评论' line"""
    for i in range(start, len(lines)):
        if re.match(r"共\s*\d+\s*条评论", lines[i]) or (lines[i] == "评论" and i > start):
            return i
    return len(lines)


def _extract_bottom_stats(lines: list[str]) -> tuple[int, int, int]:
    """extract likes/collects/comments from bottom 6 lines"""
    like = collect = comment = 0
    nums = []
    for line in reversed(lines):
        line = line.strip()
        if any(w in line for w in ["说点什么", "发送", "取消"]):
            continue
        try:
            nums.append(int(line))
        except ValueError:
            if nums:
                break
    if len(nums) >= 3:
        # typical order from bottom: (发送) comment, collect, like
        like, collect = nums[-1], nums[-2]
    return like, collect, comment


def _parse_note_text(text: str, url: str, post_id: str) -> XHSPost:
    """parse note detail text into XHSPost

    Three known structures:
      Type A (followed):    counter → author → 已关注 → title → [body?] → #tags → date → comments
      Type B (not followed): counter → author → 关注 → title → body → #tags → date → comments
      Type C (no follow):   counter → author → title → body → #tags → date → comments
    """
    lines = [l.strip() for l in text.split("\n") if l.strip()]
    if not lines:
        return XHSPost(url=url, post_id=post_id, title="", content="", author_name="")

    # skip image counter like "1/14"
    i = 0
    if re.match(r"\d+/\d+", lines[i]):
        i += 1

    # extract author
    author_name = ""
    if i < len(lines):
        author_name = lines[i]
        i += 1

    # skip "已关注" or "关注"
    if i < len(lines) and lines[i] in ("已关注", "关注"):
        i += 1

    # extract title
    title = ""
    if i < len(lines) and not lines[i].startswith("#"):
        title = lines[i]
        i += 1

    # extract subtitle if present (line after title that isn't a tag/date)
    subtitle = ""
    if i < len(lines) and not lines[i].startswith("#") and not re.match(r"\d{2}-\d{2}", lines[i]) and "评论" not in lines[i]:
        subtitle = lines[i]
        i += 1

    # find tag section
    tag_start = _find_tag_start(lines, i)
    comment_start = _find_comment_start(lines, i)

    # body text: subtitle + everything between current position and tags/comments
    body_end = tag_start if tag_start < comment_start else comment_start
    body_lines = []
    if subtitle:
        body_lines.append(subtitle)
    if body_end > i:
        body_lines.extend(lines[i:body_end])
    has_body_text = bool(body_lines)  # track if actual body text exists

    # tags
    tags: list[str] = []
    if tag_start < len(lines):
        tag_line = lines[tag_start]
        for word in tag_line.split():
            if word.startswith("#"):
                tag = word.replace("#", "").rstrip(".,;!()（）")
                if tag and tag not in tags:
                    tags.append(tag)

    # date (MM-DD pattern)
    publish_time = None
    for j in range(tag_start + 1 if tag_start < len(lines) else i,
                   min(tag_start + 5, len(lines))):
        m = re.match(r"(\d{2}-\d{2})(?:\s+\S+)?", lines[j])
        if m:
            try:
                publish_time = datetime.strptime(f"2026-{m.group(1)}", "%Y-%m-%d")
            except ValueError:
                pass
            break

    # comment count
    comment_count = 0
    for line in lines:
        m = re.match(r"共\s*(\d+)\s*条评论", line)
        if m:
            comment_count = int(m.group(1))
            break

    # likes/collects
    like_count, collect_count, _ = _extract_bottom_stats(lines)

    # build content
    content_parts = []

    # body text (description) — the most valuable part
    if body_lines:
        content_parts.append("\n".join(body_lines))

    # comments are supplementary
    comments_text = []
    if comment_start < len(lines):
        for line in lines[comment_start + 1:]:
            if line in ("说点什么...", "发送", "取消", ""):
                continue
            if line.isdigit() and len(line) <= 6:
                continue
            if line in ("赞", "回复", "作者"):
                continue
            if any(line.startswith(w) for w in ("展开",)):
                continue
            comments_text.append(line)
            if len(comments_text) >= 15:
                break

    if comments_text:
        if content_parts:
            content_parts.append("")
        content_parts.append("【评论】")
        content_parts.extend(comments_text[:10])

    content = "\n".join(content_parts)

    return XHSPost(
        url=url,
        post_id=post_id,
        title=title,
        content=content,
        author_name=author_name,
        like_count=like_count,
        collect_count=collect_count,
        comment_count=comment_count,
        tags=tags,
        publish_time=publish_time,
    )


def _extract_images(page: Page) -> list[str]:
    """extract content image URLs from the note's image carousel only

    Excludes: avatars, UI icons, decoration images.
    Only grabs: the note's main content images (screenshots/photos in the swiper).
    """
    urls = []
    try:
        container = page.query_selector("#noteContainer, [class*='note-detail'], [class*='note-container']")
        scope = container if container else page

        # only look inside the image carousel/swiper area
        carousel_selectors = [
            "[class*='swiper']",
            "[class*='carousel']",
            "[class*='image-container']",
            "[class*='note-image']",
            "[class*='slider']",
        ]
        carousel = None
        for sel in carousel_selectors:
            carousel = scope.query_selector(sel)
            if carousel:
                break

        search_scope = carousel if carousel else scope

        # find content images (exclude small/avatar URLs)
        img_els = search_scope.query_selector_all("img[src*='xhscdn']")
        if not img_els:
            img_els = search_scope.query_selector_all("img[src*='sns-webpic']")

        for img in img_els:
            src = img.get_attribute("src") or img.get_attribute("data-src") or ""
            if not src or not src.startswith("http"):
                continue
            if src in urls:
                continue

            # skip avatars: look for "avatar" in class or URL
            cls = (img.get_attribute("class") or "").lower()
            parent_cls = ""
            try:
                parent = img.evaluate("el => el.parentElement?.className || ''")
                parent_cls = parent.lower()
            except Exception:
                pass

            if any(w in cls + parent_cls for w in ["avatar", "author", "user-img"]):
                continue

            # skip tiny thumbnail URLs (XHS thumbnails have "!nc_n_webp_mw_1" pattern)
            if "_mw_1" in src or "avatar" in src.lower():
                continue

            urls.append(src)
            if len(urls) >= 20:
                break

    except Exception:
        pass
    return urls


# ── helpers ──────────────────────────────────────────────────────


def _parse_count(text: str) -> int:
    """parse count text like '1.2万', '1234', '1,234'"""
    if not text:
        return 0
    text = text.replace(",", "").replace(" ", "").strip()
    if "万" in text:
        num = float(text.replace("万", ""))
        return int(num * 10000)
    try:
        return int(text)
    except ValueError:
        return 0


def _parse_datetime(text: str) -> Optional[datetime]:
    """try to parse various datetime formats"""
    if not text:
        return None
    # common formats
    formats = [
        "%Y-%m-%d %H:%M",
        "%Y-%m-%d",
        "%Y/%m/%d %H:%M",
        "%Y/%m/%d",
        "%m-%d %H:%M",
    ]
    for fmt in formats:
        try:
            return datetime.strptime(text.strip(), fmt)
        except ValueError:
            continue

    # relative time like "3天前", "2小时前", "刚刚"
    now = datetime.now()
    if "分钟前" in text:
        mins = int(re.search(r"(\d+)", text).group(1))  # type: ignore[union-attr]
        return now - timedelta(minutes=mins)
    if "小时前" in text:
        hours = int(re.search(r"(\d+)", text).group(1))  # type: ignore[union-attr]
        return now - timedelta(hours=hours)
    if "天前" in text:
        days = int(re.search(r"(\d+)", text).group(1))  # type: ignore[union-attr]
        return now - timedelta(days=days)
    if "刚刚" in text:
        return now

    return None


def _random_delay(min_s: float = 3.0, max_s: float = 8.0) -> None:
    time.sleep(random.uniform(min_s, max_s))


# ── image download ───────────────────────────────────────────────


def _download_images(post_id: str, image_urls: list[str]) -> list[str]:
    """download images and return local paths"""
    IMAGE_DIR.mkdir(parents=True, exist_ok=True)
    local_paths = []
    for i, url in enumerate(image_urls):
        try:
            ext = Path(url.split("?")[0]).suffix or ".jpg"
            filename = f"{post_id}_{i:02d}{ext}"
            filepath = IMAGE_DIR / filename

            if filepath.exists():
                local_paths.append(str(filepath))
                continue

            resp = httpx.get(url, timeout=15)
            if resp.status_code == 200:
                filepath.write_bytes(resp.content)
                local_paths.append(str(filepath))
                log.debug("Downloaded: %s", filename)
        except Exception:
            log.warning("Failed to download image: %s", url)
    return local_paths


# ── main scrape function ─────────────────────────────────────────


def scrape_post(
    url: str,
    keyword: str = "",
    headless: bool = True,
    user_data_dir: Optional[Path] = None,
) -> XHSPost:
    """scrape a single XHS post by clicking it from search results"""
    config = get_config()
    profile_dir = str(user_data_dir or USER_DATA_DIR)
    post_id = url.rstrip("/").split("/")[-1]
    search_kw = keyword or post_id

    log.info("Scraping: %s (keyword=%s)", post_id, search_kw)

    with sync_playwright() as pw:
        context = pw.chromium.launch_persistent_context(
            user_data_dir=profile_dir,
            channel="chrome",
            headless=headless,
            viewport={"width": 1280, "height": 900},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/125.0.0.0 Safari/537.36"
            ),
            args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
        )
        page = context.new_page()

        try:
            # navigate to search page first
            search_url = f"https://www.xiaohongshu.com/search_result?keyword={search_kw}&sort=general"
            page.goto(search_url, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(4000)

            if "404" in page.title() or "不存在" in page.content():
                raise XHSPostNotFound(f"Search failed for: {search_kw}")

            # find and click the matching card
            cards = page.query_selector_all("section.note-item a[href*='/explore/'], .note-item a[href*='/explore/']")
            clicked = False
            for card in cards:
                href = card.get_attribute("href") or ""
                if post_id in href:
                    # scroll card into view and click
                    try:
                        card.scroll_into_view_if_needed()
                        page.wait_for_timeout(500)
                        card.click(force=True, timeout=10000)
                        page.wait_for_timeout(8000)
                        clicked = True
                        break
                    except Exception:
                        continue

            if not clicked:
                # try clicking first card anyway
                first_card = page.query_selector("section.note-item, .note-item")
                if first_card:
                    first_card.click(force=True, timeout=10000)
                    page.wait_for_timeout(8000)
                    clicked = True

            if not clicked:
                raise XHSPostNotFound(f"Cannot find note card for: {post_id}")

            # extract note detail
            detail_text = _extract_note_detail(page)
            if not detail_text:
                raise XHSScrapeError(f"No content found for: {post_id}")

            post = _parse_note_text(detail_text, url, post_id)

            # download images if any
            image_urls = _extract_images(page)
            if image_urls:
                post.image_urls = _download_images(post_id, image_urls) or image_urls

            log.info("Scraped OK: %s (%d chars)", post_id, len(detail_text))
            return post

        except XHSScrapeError:
            raise
        except Exception:
            log.exception("Scrape failed for: %s", url)
            raise XHSScrapeError(f"Failed to scrape: {url}")
        finally:
            context.close()


def scrape_posts(
    urls: list[str],
    keyword: str = "",
    headless: bool = True,
    resume: bool = True,
) -> list[XHSPost]:
    """scrape posts from a single search session via click-through"""
    config = get_config()
    profile_dir = str(USER_DATA_DIR)
    posts: list[XHSPost] = []
    scraped_ids = _load_checkpoint() if resume else set()

    remaining_urls = [u for u in urls if u.rstrip("/").split("/")[-1] not in scraped_ids]
    remaining_ids = set(u.rstrip("/").split("/")[-1] for u in remaining_urls)
    skipped = len(urls) - len(remaining_urls)
    if skipped > 0:
        log.info("Resuming: %d already scraped, %d remaining", skipped, len(remaining_urls))

    if not remaining_urls:
        return posts

    target_count = len(remaining_urls)
    search_kw = keyword or list(remaining_ids)[0]
    search_url = f"https://www.xiaohongshu.com/search_result?keyword={quote(search_kw)}&sort=general"

    with sync_playwright() as pw:
        context = pw.chromium.launch_persistent_context(
            user_data_dir=profile_dir,
            channel="chrome",
            headless=headless,
            viewport={"width": 1280, "height": 900},
            args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
        )
        page = context.new_page()

        # search once, then click cards in order
        page.goto(search_url, wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(4000)
        cards = page.query_selector_all("section.note-item, .note-item")
        log.info("Search page: %d cards, targeting %d posts", len(cards), target_count)

        for card_idx in range(min(target_count, len(cards))):
            try:
                # refresh search and get fresh cards
                if card_idx > 0:
                    page.goto(search_url, wait_until="domcontentloaded", timeout=30000)
                    page.wait_for_timeout(4000)
                    cards = page.query_selector_all("section.note-item, .note-item")

                if card_idx >= len(cards):
                    log.warning("Only %d cards, cannot click #%d", len(cards), card_idx)
                    break

                card = cards[card_idx]
                link = card.query_selector("a[href*='/explore/']")
                if not link:
                    continue
                href = link.get_attribute("href") or ""
                post_id = href.rstrip("/").split("/")[-1]

                if post_id in scraped_ids:
                    log.info("[%d/%d] Already scraped: %s", card_idx + 1, target_count, post_id)
                    continue

                # click the card
                box = card.bounding_box()
                if box:
                    page.mouse.click(box["x"] + box["width"] / 2,
                                     box["y"] + box["height"] / 2)
                else:
                    card.click(timeout=10000)

                # wait and extract
                page.wait_for_timeout(6000)
                detail_text = _extract_note_detail(page, timeout=10000)
                if not detail_text:
                    page.wait_for_timeout(4000)
                    detail_text = _extract_note_detail(page, timeout=5000)

                if detail_text:
                    full_url = f"https://www.xiaohongshu.com{href}"
                    post = _parse_note_text(detail_text, full_url, post_id)

                    # extract and download images
                    image_urls = _extract_images(page)
                    local_images = []
                    if image_urls:
                        local_images = _download_images(post_id, image_urls) or []
                        post.image_urls = local_images or image_urls

                        # Always OCR content images — formatter merges with body text
                        if local_images:
                            from src.scrape.ocr import ocr_images
                            ocr_text = ocr_images(local_images)
                            if ocr_text:
                                post.ocr_text = ocr_text
                                log.info("OCR: %d chars", len(ocr_text))

                    posts.append(post)
                    _save_checkpoint(post_id)
                    scraped_ids.add(post_id)
                    log.info("[%d/%d] OK: %s | %s", card_idx + 1, target_count,
                             post_id, post.title[:40])
                else:
                    log.warning("[%d/%d] No detail for: %s", card_idx + 1, target_count, post_id)

            except Exception as e:
                log.error("[%d/%d] Error: %s", card_idx, e)

            # rate limiting
            if len(posts) < target_count:
                delay = random.uniform(config.scrape_min_delay, config.scrape_max_delay)
                time.sleep(delay)

        context.close()

    log.info("Scrape batch complete: %d success / %d total", len(posts), len(urls))
    return posts


def scrape_from_results(
    results: list[SearchResult],
    keyword: str = "",
    headless: bool = True,
    resume: bool = True,
    max_concurrent: int = 1,
) -> list[XHSPost]:
    """convenience: scrape posts from search results"""
    urls = [r.url for r in results]
    if max_concurrent > 1:
        return scrape_posts_parallel(urls, keyword=keyword, headless=headless, resume=resume, max_concurrent=max_concurrent)
    return scrape_posts(urls, keyword=keyword, headless=headless, resume=resume)


def _export_storage_state(headless: bool = True) -> None:
    """export auth cookies/storage from persistent profile to a sharable file"""
    if STORAGE_STATE_FILE.exists():
        return
    log.info("Exporting storage state from %s ...", USER_DATA_DIR)
    try:
        with sync_playwright() as pw:
            context = pw.chromium.launch_persistent_context(
                user_data_dir=str(USER_DATA_DIR),
                channel="chrome",
                headless=headless,
                viewport={"width": 1280, "height": 900},
                args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
            )
            STORAGE_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
            context.storage_state(path=str(STORAGE_STATE_FILE))
            context.close()
        log.info("Storage state exported → %s", STORAGE_STATE_FILE)
    except Exception:
        log.exception("Failed to export storage state, parallel scrape may not work")


def scrape_posts_parallel(
    urls: list[str],
    keyword: str = "",
    headless: bool = True,
    resume: bool = True,
    max_concurrent: int = 2,
) -> list[XHSPost]:
    """scrape posts in parallel using independent browser instances

    Each worker loads the shared storage_state (cookies) instead of using
    the persistent profile, avoiding directory-lock conflicts.
    """
    if max_concurrent <= 1:
        return scrape_posts(urls, keyword=keyword, headless=headless, resume=resume)

    posts: list[XHSPost] = []
    scraped_ids = _load_checkpoint() if resume else set()

    remaining_urls = [u for u in urls if u.rstrip("/").split("/")[-1] not in scraped_ids]
    skipped = len(urls) - len(remaining_urls)
    if skipped > 0:
        log.info("Resuming: %d already scraped, %d remaining", skipped, len(remaining_urls))

    if not remaining_urls:
        return posts

    # ensure storage state is exported for workers
    _export_storage_state(headless)

    effective_workers = min(max_concurrent, len(remaining_urls))
    log.info("Parallel scrape: %d urls, %d workers", len(remaining_urls), effective_workers)

    def _scrape_one(url: str) -> XHSPost:
        """scrape a single post using its own browser with shared auth"""
        config = get_config()
        post_id = url.rstrip("/").split("/")[-1]
        search_kw = keyword or post_id

        with sync_playwright() as pw:
            browser = pw.chromium.launch(
                channel="chrome",
                headless=headless,
                args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
            )
            context = browser.new_context(
                storage_state=str(STORAGE_STATE_FILE) if STORAGE_STATE_FILE.exists() else None,
                viewport={"width": 1280, "height": 900},
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
                ),
            )
            page = context.new_page()

            try:
                search_url = f"https://www.xiaohongshu.com/search_result?keyword={quote(search_kw)}&sort=general"
                page.goto(search_url, wait_until="domcontentloaded", timeout=30000)
                page.wait_for_timeout(4000)

                # find and click matching card
                cards = page.query_selector_all(
                    "section.note-item a[href*='/explore/'], .note-item a[href*='/explore/']"
                )
                clicked = False
                for card in cards:
                    href = card.get_attribute("href") or ""
                    if post_id in href:
                        try:
                            card.scroll_into_view_if_needed()
                            page.wait_for_timeout(500)
                            card.click(force=True, timeout=10000)
                            page.wait_for_timeout(8000)
                            clicked = True
                            break
                        except Exception:
                            continue

                if not clicked:
                    raise XHSPostNotFound(f"Cannot find note card for: {post_id}")

                detail_text = _extract_note_detail(page)
                if not detail_text:
                    raise XHSScrapeError(f"No content found for: {post_id}")

                post = _parse_note_text(detail_text, url, post_id)

                image_urls = _extract_images(page)
                if image_urls:
                    local_images = _download_images(post_id, image_urls) or []
                    post.image_urls = local_images or image_urls

                    if local_images:
                        from src.scrape.ocr import ocr_images
                        ocr_text = ocr_images(local_images)
                        if ocr_text:
                            post.ocr_text = ocr_text

                log.info("Scraped OK: %s (%d chars)", post_id, len(detail_text))
                return post

            finally:
                context.close()
                browser.close()

    with ThreadPoolExecutor(max_workers=effective_workers) as executor:
        future_to_url = {executor.submit(_scrape_one, url): url for url in remaining_urls}
        for future in as_completed(future_to_url):
            url = future_to_url[future]
            try:
                post = future.result()
                posts.append(post)
                _save_checkpoint(post.post_id)
            except XHSScrapeError as e:
                log.error("Scrape failed: %s — %s", url, e)
            except Exception:
                log.exception("Unexpected error scraping: %s", url)

    log.info("Scrape parallel complete: %d success / %d total", len(posts), len(urls))
    return posts
