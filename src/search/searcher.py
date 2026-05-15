import random
import time
from pathlib import Path
from typing import Optional
from urllib.parse import quote

from playwright.sync_api import Page, sync_playwright, TimeoutError as PwTimeout

from src.config import get_config
from src.logger import get_logger
from src.models import SearchResult

log = get_logger(__name__)

XHS_SEARCH_URL = "https://www.xiaohongshu.com/search_result?keyword={keyword}&sort={sort}"
XHS_BASE = "https://www.xiaohongshu.com"

USER_DATA_DIR = Path.home() / ".xhs_browser_profile"


class XHSSearchError(Exception):
    pass


class XHSRateLimitError(XHSSearchError):
    pass


class XHSLoginRequiredError(XHSSearchError):
    pass


def _random_delay(min_s: float = 0.5, max_s: float = 2.0) -> None:
    time.sleep(random.uniform(min_s, max_s))


def _build_search_url(keyword: str, sort: str = "general") -> str:
    return XHS_SEARCH_URL.format(keyword=quote(keyword), sort=sort)


def _wait_for_results(page: Page, timeout: int = 15000) -> bool:
    """wait for search result cards to appear"""
    selectors = [
        "section.note-item",
        ".note-item",
        "[class*='note'] a[href*='/explore/']",
        "a[href*='/explore/']",
    ]
    for sel in selectors:
        try:
            page.wait_for_selector(sel, timeout=timeout)
            log.info("Results loaded (selector: %s)", sel)
            return True
        except PwTimeout:
            continue
    return False


def _scroll_to_load_more(page: Page, target_count: int, max_scrolls: int = 30) -> int:
    """scroll down to load more search results, return current result count"""
    selectors = [
        "section.note-item",
        ".note-item",
        "a[href*='/explore/']",
    ]
    for _ in range(max_scrolls):
        page.evaluate("window.scrollBy(0, window.innerHeight * 2)")
        _random_delay(0.8, 1.5)

        for sel in selectors:
            items = page.query_selector_all(sel)
            if len(items) >= target_count:
                return len(items)

    for sel in selectors:
        items = page.query_selector_all(sel)
        if items:
            return len(items)
    return 0


def _parse_note_card(card) -> Optional[SearchResult]:
    """parse a single note card element"""
    try:
        # find the explore link
        link_el = card.query_selector("a[href*='/explore/']")
        if not link_el:
            link_el = card.query_selector("a[href*='/discovery/item/']")
        if not link_el:
            # the card itself might be the link
            if card.evaluate("el => el.tagName") == "A":
                link_el = card
        if not link_el:
            return None

        href = link_el.get_attribute("href") or ""
        if not href or "/explore/" not in href:
            return None

        url = href if href.startswith("http") else f"{XHS_BASE}{href}"
        post_id = href.rstrip("/").split("/")[-1]

        # extract title — check various elements within the card
        title = ""
        title_selectors = [
            ".title",
            "[class*='title']",
            "span",
            "[class*='desc']",
        ]
        for sel in title_selectors:
            el = card.query_selector(sel)
            if el:
                text = el.inner_text().strip()
                if text and len(text) > 2:
                    title = text
                    break
        if not title:
            # fallback: use first meaningful text from card
            full_text = card.inner_text().strip()
            lines = [l.strip() for l in full_text.split("\n") if l.strip() and len(l.strip()) > 2]
            title = lines[0] if lines else ""

        # extract author
        author_name = ""
        author_selectors = [
            ".author .name",
            "[class*='author'] [class*='name']",
            "[class*='name']",
            ".author",
            "[class*='author']",
        ]
        for sel in author_selectors:
            el = card.query_selector(sel)
            if el:
                text = el.inner_text().strip()
                if text and len(text) > 1 and len(text) < 30:
                    author_name = text
                    break

        # extract cover image
        img_el = card.query_selector("img")
        cover_url = img_el.get_attribute("src") or "" if img_el else ""

        return SearchResult(
            url=url,
            post_id=post_id,
            title=title,
            author_name=author_name,
            cover_url=cover_url,
        )
    except Exception:
        return None


def _detect_block(page: Page) -> Optional[str]:
    """detect if we're blocked or need verification"""
    url = page.url.lower()
    title = page.title().lower()
    html = page.content().lower()

    if "captcha" in url or "安全验证" in title or "安全验证" in html:
        return "captcha_detected"
    if "验证" in html and ("滑块" in html or "点击" in html):
        return "captcha_detected"
    if "访问频繁" in html or "请稍后再试" in html:
        return "rate_limited"
    if "登录" in title or "login" in url:
        return "login_required"
    return None


def search(
    keyword: str,
    count: int = 20,
    sort: str = "general",
    headless: bool = True,
    user_data_dir: Optional[Path] = None,
) -> list[SearchResult]:
    """search xiaohongshu for the given keyword, return list of SearchResult"""
    config = get_config()
    if count <= 0:
        count = config.search_max_results

    profile_dir = str(user_data_dir or USER_DATA_DIR)
    profile_dir_path = Path(profile_dir)
    profile_dir_path.mkdir(parents=True, exist_ok=True)

    results: list[SearchResult] = []
    url = _build_search_url(keyword, sort)

    log.info("Searching XHS: keyword=%s, count=%d", keyword, count)

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
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
            ],
        )
        page = context.new_page()

        try:
            page.goto(url, wait_until="domcontentloaded", timeout=30000)
            _random_delay(1, 2)

            blocked = _detect_block(page)
            if blocked == "captcha_detected":
                if not headless:
                    log.info("Captcha page detected, waiting for manual solve (120s)...")
                    for _ in range(120):
                        time.sleep(1)
                        if "captcha" not in page.url.lower():
                            log.info("Captcha solved, continuing")
                            blocked = None
                            break
                    if blocked:
                        raise XHSRateLimitError("Captcha timeout")
                else:
                    raise XHSRateLimitError("Captcha detected, try --no-headless")
            if blocked == "rate_limited":
                raise XHSRateLimitError("Rate limited by XHS")
            if blocked == "login_required":
                raise XHSLoginRequiredError(
                    "Login required. Run with headless=False to login first."
                )

            if not _wait_for_results(page):
                log.warning("No results found for: %s", keyword)
                return results

            _scroll_to_load_more(page, count)
            # prioritize .note-item cards, fallback to bare links
            cards = page.query_selector_all("section.note-item, .note-item")
            if not cards:
                cards = page.query_selector_all("a[href*='/explore/']")

            for card in cards:
                parsed = _parse_note_card(card)
                if parsed and parsed.url not in {r.url for r in results}:
                    results.append(parsed)
                if len(results) >= count:
                    break

            log.info("Found %d results for: %s", len(results), keyword)

        except XHSSearchError:
            raise
        except Exception:
            log.exception("Search failed for: %s", keyword)
            raise
        finally:
            context.close()

    return results


def search_batch(
    keywords: list[str],
    count_per: int = 20,
    headless: bool = True,
) -> dict[str, list[SearchResult]]:
    """search multiple keywords, return dict keyed by keyword"""
    all_results: dict[str, list[SearchResult]] = {}
    for i, kw in enumerate(keywords):
        log.info("[%d/%d] Searching: %s", i + 1, len(keywords), kw)
        try:
            results = search(kw, count=count_per, headless=headless)
            all_results[kw] = results
            if i < len(keywords) - 1:
                delay = random.uniform(5, 10)
                log.info("Waiting %.1fs before next keyword...", delay)
                time.sleep(delay)
        except XHSSearchError as e:
            log.error("Search failed for '%s': %s", kw, e)
            all_results[kw] = []
    return all_results