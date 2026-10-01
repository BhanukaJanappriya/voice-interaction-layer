"""Polite, incremental crawler for SLT-Mobitel's official public websites.

Which hosts (ALLOWED_HOSTS and LEGACY_HOSTS below):

    www.sltmobitel.lk   The current official site, the one named in the
                        owner's brief. It is a Nuxt
                        app: its server HTML holds the navigation, footer
                        and a store of banners, tiles (English, Sinhala,
                        Tamil) and page descriptions, read from the
                        window.__NUXT__ blob (see nuxt_state.py). Package
                        tables and most page bodies are NOT in the HTML;
                        the browser fetches them later from a separate API
                        host (apphub.mobitel.lk) that requires a client id
                        header. This crawler does not call that API: it is
                        a different host, and the header is a credential
                        issued to the website, not to us.
    www.mobitel.lk      Included by default since the owner approved it on
                        2026-10-01 (--no-include-legacy leaves it out). The
                        older official site of Mobitel (Pvt) Ltd, SLT's
                        wholly owned subsidiary, still online and server
                        rendered, with the package tables (prices, data
                        allowances, validity, TRC tariff references), FAQs
                        and USSD codes the new site only shows after
                        JavaScript runs. It is not linked from
                        www.sltmobitel.lk, so it widens the brief's one
                        domain; without it the index has no package prices
                        at all (see docs/RAG_EVAL.md). Its robots.txt
                        (Crawl-delay: 10) is read and honored like any
                        other host's.

Rules applied to every request (the owner's brief):
    robots.txt is fetched first for each host with urllib.robotparser and
    obeyed, including Crawl-delay; a missing robots.txt (404) means nothing
    is disallowed. Requests identify as config.SCRAPER_USER_AGENT, are at
    least config.SCRAPER_DELAY_SECONDS apart per host, stay on the listed
    hosts (redirects are followed by hand, only when they stay on them),
    skip login/cart/account/checkout/selfcare/payment/myslt style URLs and
    binary files, and stop at config.SCRAPER_MAX_PAGES pages in total
    (redirect hops excluded, total requests capped at twice that).

Incremental: every page is cached under KNOWLEDGE_DIR/raw/ with a
manifest (url, final url, fetched_at, checked_at, ETag, Last-Modified,
content hash). A re-run sends conditional requests (If-None-Match /
If-Modified-Since); a 304 or an identical content hash keeps the cached
copy and its original fetched_at.

Playwright: an optional fallback for pages whose HTML has no body text,
used only if the package is importable. It is not a dependency of this
project; when it is missing the crawler logs how many pages would have
needed it and continues with what the HTML and the Nuxt store provide.
Note that rendering those pages in a browser would itself call the
apphub.mobitel.lk API.

Run: python -m backend.knowledge.scraper [--max-pages N] (repository root)
or python scraper.py from inside backend/knowledge.
"""

import argparse
import hashlib
import heapq
import html as html_lib
import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from urllib import robotparser
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import requests
from bs4 import BeautifulSoup

import config
from knowledge.nuxt_state import extract_nuxt_state, iter_urls

RAW_DIR = config.KNOWLEDGE_DIR / "raw"
MANIFEST_PATH = RAW_DIR / "manifest.json"

PRIMARY_HOST = "www.sltmobitel.lk"
# The explicit list of hosts this crawler may touch; everything else is
# off site. ALLOWED_HOSTS is the brief's domain. LEGACY_HOSTS is the same
# company's older official site, used by default and dropped with
# --no-include-legacy; see the module docstring for why.
ALLOWED_HOSTS = ("www.sltmobitel.lk",)
LEGACY_HOSTS = ("www.mobitel.lk",)


def active_hosts(include_legacy: bool = True) -> tuple:
    return ALLOWED_HOSTS + (LEGACY_HOSTS if include_legacy else ())

# Per host crawl settings. Seeds for www.mobitel.lk are pages from its
# own menu (it has no sitemap); every other page is found by following
# links. Language prefixed copies (/si/, /ta/) of the legacy site are
# skipped: they are mostly untranslated and would double the crawl time
# at a 10 second delay, and bge-m3 already matches Sinhala and Tamil
# questions to English text.
SITES = {
    "www.sltmobitel.lk": {
        "seeds": ["/"],
        "sitemap": "/sitemap.xml",
        "skip_prefixes": ("/career", "/_nuxt/", "/api/", "/documents/"),
        # Its ETag changes on every render (random element ids in the
        # HTML), and a request carrying If-None-Match was answered with
        # HTTP 400, so change detection there uses the content hash only.
        "conditional_requests": False,
    },
    "www.mobitel.lk": {
        "seeds": [
            "/data-rollover-plans", "/prepaid", "/postpaid", "/broadband/plans-and-rates-prepaid",
            "/broadband/plans-and-rates-postpaid", "/voice-and-data-plans", "/postpaid-plans-rates",
            "/prepaid-short-codes", "/postpaid-short-codes", "/international-roaming", "/idd",
            "/prepaid/tourist-pack", "/tourist-plans", "/visiting-sri-lanka", "/how-to-recharge",
            "/faqs/broadband-general", "/postpaid/faqs", "/faqs/idd", "/contact-us", "/about-us",
            "/new-esim", "/4g-lte", "/unlimited-streaming", "/broadband/internet-devices-settings",
            "/smart-loan", "/mcash", "/support",
        ],
        "sitemap": None,
        "skip_prefixes": ("/si/", "/ta/", "/user", "/node/add", "/search", "/admin", "/comment/",
                          "/sites/default/files/", "/tv-commercials", "/news-and-alerts"),
        "conditional_requests": True,
    },
}

# Host spellings that mean one of the hosts above.
_HOST_ALIASES = {"sltmobitel.lk": "www.sltmobitel.lk", "mobitel.lk": "www.mobitel.lk"}

# Words that mean a private or transactional page: never fetched. They are
# matched against whole URL tokens (path segments and query values split
# on - _ . and similar), not substrings, so "account" skips
# /my/account but not /accountant, and "print" does not match
# "footprint". Hyphenated forms such as sign-in are covered because both
# the whole segment and its parts are checked.
_SKIP_TOKENS = frozenset((
    "login", "logout", "signin", "sign-in", "signup", "sign-up", "register", "password", "cart",
    "account", "accounts", "checkout", "selfcare", "self-care", "payment", "payments", "pay-bill",
    "paybill", "myslt", "myaccount", "my-account", "wishlist", "basket", "otp", "undefined", "print",
))
_BINARY_EXTENSIONS = (
    ".pdf", ".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg", ".ico", ".bmp", ".zip", ".rar", ".7z",
    ".gz", ".mp3", ".mp4", ".avi", ".mov", ".wav", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
    ".apk", ".exe", ".dmg", ".css", ".js", ".json", ".woff", ".woff2", ".ttf", ".eot", ".xml", ".txt",
)
_TRACKING_PARAMS = ("utm_", "fbclid", "gclid", "_rdc", "_rdr", "hl")

# Below this many characters of visible body text (after dropping
# scripts), a page is considered client rendered.
_MIN_BODY_TEXT = 800


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def canonical_url(url: str, base: Optional[str] = None) -> Optional[str]:
    """Absolute https URL on a crawled host, with fragment, tracking
    parameters and default ports removed and the query sorted, or None
    when the URL is off site."""
    if base:
        url = urljoin(base, url)
    parts = urlparse(url.strip())
    if parts.scheme not in ("http", "https"):
        return None
    host = (parts.hostname or "").lower()
    host = _HOST_ALIASES.get(host, host)
    if host not in SITES:
        return None
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
             if not any(k.startswith(p) for p in _TRACKING_PARAMS)]
    path = parts.path or "/"
    return urlunparse(("https", host, path, "", urlencode(sorted(query)), ""))


def _url_tokens(parts) -> set:
    segments = [seg for seg in re.split(r"[/?&=;,+]", (parts.path + "?" + parts.query).lower()) if seg]
    tokens = set(segments)
    for seg in segments:
        tokens.update(tok for tok in re.split(r"[-_.~%:]", seg) if tok)
    return tokens


def is_crawlable(url: str) -> bool:
    """True for an on site, non private, non binary page URL. Unknown
    hosts return False rather than raising."""
    parts = urlparse(url)
    site = SITES.get((parts.hostname or "").lower())
    if site is None:
        return False
    path = parts.path.lower()
    if _url_tokens(parts) & _SKIP_TOKENS:
        return False
    if path.endswith(_BINARY_EXTENSIONS):
        return False
    if any(path.startswith(prefix.lower()) for prefix in site["skip_prefixes"]):
        return False
    return True


def _cache_file(url: str) -> Path:
    host = urlparse(url).hostname
    return RAW_DIR / host / (hashlib.sha1(url.encode("utf-8")).hexdigest() + ".html")


def load_manifest() -> dict:
    if MANIFEST_PATH.exists():
        try:
            return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
        except ValueError:
            pass
    return {"version": 1, "pages": {}}


def _save_manifest(manifest: dict) -> None:
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    tmp = MANIFEST_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(manifest, indent=1, ensure_ascii=False), encoding="utf-8")
    tmp.replace(MANIFEST_PATH)


def _visible_text_length(soup: BeautifulSoup) -> int:
    body = soup.body or soup
    for tag in body.find_all(["script", "style", "noscript", "nav", "header", "footer"]):
        tag.decompose()
    return len(" ".join(body.get_text(" ").split()))


def content_fingerprint(html: str) -> str:
    """Hash of what the knowledge base actually uses from a page: its
    visible text and its Nuxt store. Hashing the raw bytes would report
    every page as changed on every run, because both sites embed per
    request values (random element ids, form tokens) in their markup."""
    state = extract_nuxt_state(html)
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup.find_all(["script", "style", "noscript"]):
        tag.decompose()
    text = " ".join(soup.get_text(" ").split())
    payload = text + "\n" + json.dumps(state, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _playwright_available() -> bool:
    try:
        import playwright.sync_api  # noqa: F401
    except ImportError:
        return False
    return True


def _render_with_playwright(url: str) -> Optional[str]:
    """Renders one page in headless Chromium. Only reached when Playwright
    is installed; any failure returns None and the plain HTML is kept."""
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            page = browser.new_page(user_agent=config.SCRAPER_USER_AGENT)
            page.goto(url, wait_until="networkidle", timeout=45000)
            html = page.content()
            browser.close()
            return html
    except Exception as exc:
        print(f"  Playwright render failed for {url}: {exc}")
        return None


class _Host:
    """Per host robots rules and politeness clock."""

    def __init__(self, name: str, session: requests.Session):
        self.name = name
        self.robots = robotparser.RobotFileParser()
        self.delay = config.SCRAPER_DELAY_SECONDS
        self.next_allowed = 0.0
        self.robots_status = None
        url = f"https://{name}/robots.txt"
        try:
            response = session.get(url, timeout=30, allow_redirects=True)
            self.robots_status = response.status_code
            if response.status_code in (401, 403):
                self.robots.disallow_all = True
            elif response.status_code >= 400:
                self.robots.allow_all = True
            else:
                self.robots.parse(response.text.splitlines())
        except requests.RequestException as exc:
            # Could not read the rules: be conservative and crawl nothing.
            print(f"robots.txt for {name} unreadable ({exc}); skipping this host.")
            self.robots.disallow_all = True
        crawl_delay = self.robots.crawl_delay(config.SCRAPER_USER_AGENT)
        if crawl_delay:
            self.delay = max(self.delay, float(crawl_delay))
        self.next_allowed = time.monotonic() + self.delay
        print(f"robots.txt {name}: HTTP {self.robots_status}, delay {self.delay:.1f}s")

    def allowed(self, url: str) -> bool:
        return self.robots.can_fetch(config.SCRAPER_USER_AGENT, url)


class Crawler:
    def __init__(self, max_pages: int = None, hosts: Optional[list] = None, include_legacy: bool = True):
        self.max_pages = config.SCRAPER_MAX_PAGES if max_pages is None else max_pages
        self.host_names = [h for h in (hosts or active_hosts(include_legacy)) if h in SITES]
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": config.SCRAPER_USER_AGENT,
            "Accept": "text/html,application/xhtml+xml",
            "Accept-Language": "en",
        })
        self.manifest = load_manifest()
        self.pages = self.manifest.setdefault("pages", {})
        self.hosts = {}
        self.queues = {}  # host -> list of (priority, order, url)
        self.seen = set()
        self.order = 0
        self.stats = {"requests": 0, "fetched_new_or_changed": 0, "not_modified": 0, "unchanged": 0,
                      "redirects": 0, "errors": 0, "robots_disallowed": 0, "non_html": 0,
                      "client_rendered": 0, "playwright_rendered": 0}
        self.playwright = _playwright_available()

    # Queue

    def _enqueue(self, url: Optional[str], priority: int = 5) -> None:
        if not url or url in self.seen:
            return
        host = urlparse(url).hostname
        if host not in self.queues or not is_crawlable(url):
            return
        self.seen.add(url)
        self.order += 1
        heapq.heappush(self.queues[host], (priority, self.order, url))

    def _enqueue_links(self, html: str, base_url: str) -> None:
        soup = BeautifulSoup(html, "html.parser")
        for anchor in soup.find_all("a", href=True):
            self._enqueue(canonical_url(anchor["href"], base_url))
        state = extract_nuxt_state(html)
        if state:
            for link in iter_urls(state):
                # Nuxt paths are site relative even without a leading slash.
                if not link.startswith(("http://", "https://", "/")):
                    link = "/" + link
                self._enqueue(canonical_url(link, base_url), priority=3)

    def _seed(self, host: str) -> None:
        cfg = SITES[host]
        for path in cfg["seeds"]:
            self._enqueue(canonical_url(f"https://{host}{path}"), priority=0)
        if cfg.get("sitemap"):
            sitemap_url = f"https://{host}{cfg['sitemap']}"
            if self.hosts[host].allowed(sitemap_url):
                try:
                    self._wait(host)
                    response = self.session.get(sitemap_url, timeout=30)
                    self.stats["requests"] += 1
                    if response.ok and "xml" in response.headers.get("Content-Type", ""):
                        # A flat urlset; a regex avoids needing an XML
                        # parser (lxml) just for <loc> elements.
                        locs = re.findall(r"<loc>\s*([^<\s]+)\s*</loc>", response.text)
                        for loc in locs:
                            self._enqueue(canonical_url(html_lib.unescape(loc)), priority=1)
                        print(f"Sitemap {sitemap_url}: {len(locs)} URLs")
                except requests.RequestException as exc:
                    print(f"Sitemap {sitemap_url} failed: {exc}")

    # Fetching

    def _wait(self, host: str) -> None:
        state = self.hosts[host]
        pause = state.next_allowed - time.monotonic()
        if pause > 0:
            time.sleep(pause)
        state.next_allowed = time.monotonic() + state.delay

    def _fetch(self, url: str) -> None:
        host = urlparse(url).hostname
        if not self.hosts[host].allowed(url):
            self.stats["robots_disallowed"] += 1
            return
        entry = self.pages.get(url, {})
        headers = {}
        cache_file = _cache_file(url)
        if SITES[host]["conditional_requests"] and entry.get("status") == 200 and cache_file.exists():
            if entry.get("etag"):
                headers["If-None-Match"] = entry["etag"]
            if entry.get("last_modified"):
                headers["If-Modified-Since"] = entry["last_modified"]

        self._wait(host)
        self.stats["requests"] += 1
        started = time.perf_counter()
        try:
            response = self.session.get(url, headers=headers, timeout=45, allow_redirects=False)
        except requests.RequestException as exc:
            self.stats["errors"] += 1
            print(f"  error {url}: {exc.__class__.__name__}")
            entry.update({"url": url, "checked_at": _now(), "error": str(exc)[:200]})
            self.pages[url] = entry
            return
        elapsed_ms = (time.perf_counter() - started) * 1000

        if response.status_code in (301, 302, 303, 307, 308):
            target = canonical_url(response.headers.get("Location", ""), url)
            self.stats["redirects"] += 1
            self.pages[url] = {"url": url, "status": response.status_code, "redirect_to": target,
                               "checked_at": _now()}
            if target and target not in self.seen:
                # Stays on a crawled host: fetch it soon. Off site: dropped.
                self._enqueue(target, priority=0)
            return

        if response.status_code == 304:
            self.stats["not_modified"] += 1
            entry["checked_at"] = _now()
            self.pages[url] = entry
            self._after_page(url, cache_file.read_text(encoding="utf-8"))
            print(f"  304 {url} ({elapsed_ms:.0f} ms)")
            return

        if response.status_code != 200:
            self.stats["errors"] += 1
            self.pages[url] = {"url": url, "status": response.status_code, "checked_at": _now()}
            print(f"  HTTP {response.status_code} {url}")
            return

        content_type = response.headers.get("Content-Type", "")
        if "html" not in content_type:
            self.stats["non_html"] += 1
            self.pages[url] = {"url": url, "status": 200, "content_type": content_type,
                               "checked_at": _now(), "skipped": "not html"}
            return

        html = response.content.decode(response.encoding or "utf-8", errors="replace")
        rendered_by = "http"
        if _visible_text_length(BeautifulSoup(html, "html.parser")) < _MIN_BODY_TEXT:
            self.stats["client_rendered"] += 1
            if self.playwright:
                rendered = _render_with_playwright(url)
                if rendered:
                    html = rendered
                    rendered_by = "playwright"
                    self.stats["playwright_rendered"] += 1

        content_hash = content_fingerprint(html)
        now = _now()
        if entry.get("content_hash") == content_hash and cache_file.exists():
            self.stats["unchanged"] += 1
            entry["checked_at"] = now
        else:
            self.stats["fetched_new_or_changed"] += 1
            cache_file.parent.mkdir(parents=True, exist_ok=True)
            cache_file.write_text(html, encoding="utf-8")
            entry = {"url": url, "fetched_at": now, "content_hash": content_hash}
        entry.update({
            "url": url,
            "status": 200,
            "checked_at": now,
            "etag": response.headers.get("ETag"),
            "last_modified": response.headers.get("Last-Modified"),
            "file": str(cache_file.relative_to(RAW_DIR).as_posix()),
            "rendered_by": rendered_by,
            "host": host,
        })
        entry.pop("error", None)
        self.pages[url] = entry
        print(f"  200 {url} ({elapsed_ms:.0f} ms, {len(html) // 1024} KB)")
        self._after_page(url, html)

    def _after_page(self, url: str, html: str) -> None:
        self._enqueue_links(html, url)

    # Main loop

    def _pages_done(self) -> int:
        return (self.stats["fetched_new_or_changed"] + self.stats["unchanged"] + self.stats["not_modified"]
                + self.stats["non_html"] + self.stats["errors"])

    def run(self) -> dict:
        started = time.perf_counter()
        for name in self.host_names:
            self.hosts[name] = _Host(name, self.session)
            self.queues[name] = []
        for name in self.host_names:
            self._seed(name)

        # The cap counts pages (HTML responses and 304s). Redirect hops do
        # not count, because every navigation link on www.sltmobitel.lk
        # is a query URL answered with a 301 to its path form; a separate
        # hard stop at twice the cap bounds the total number of requests.
        while self._pages_done() < self.max_pages and self.stats["requests"] < 2 * self.max_pages:
            # Interleave hosts: take the one whose politeness delay ends
            # first, so the legacy site's 10 s delay does not idle the run.
            ready = [(self.hosts[h].next_allowed, h) for h in self.host_names if self.queues[h]]
            if not ready:
                break
            _, host = min(ready)
            _, _, url = heapq.heappop(self.queues[host])
            self._fetch(url)
            if self.stats["requests"] % 20 == 0:
                _save_manifest(self.manifest)

        self.manifest["last_run"] = {"finished_at": _now(), "stats": self.stats,
                                     "max_pages": self.max_pages, "pages": self._pages_done(),
                                     "queued_not_fetched": sum(len(q) for q in self.queues.values())}
        _save_manifest(self.manifest)
        if self.stats["client_rendered"] and not self.playwright:
            print(f"{self.stats['client_rendered']} pages had little server rendered body text; "
                  "Playwright is not installed, so their Nuxt store and HTML text were used as is.")
        elapsed = time.perf_counter() - started
        print(f"Scrape stats: {json.dumps(self.stats)}")
        print(f"Scrape latency: {elapsed:.1f}s")
        return self.stats


def cached_pages(include_legacy: bool = True) -> list:
    """Manifest entries for pages with cached HTML on the active hosts, for
    normalize.py. Pages cached from a legacy host in an earlier opt in run
    are ignored unless include_legacy is set again."""
    hosts = set(active_hosts(include_legacy))
    manifest = load_manifest()
    result = []
    for url, entry in manifest.get("pages", {}).items():
        if urlparse(url).hostname not in hosts:
            continue
        if entry.get("status") == 200 and entry.get("file") and (RAW_DIR / entry["file"]).exists():
            result.append(entry)
    return result


def read_cached(entry: dict) -> str:
    return (RAW_DIR / entry["file"]).read_text(encoding="utf-8")


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description="Crawl the official SLT-Mobitel websites.")
    parser.add_argument("--max-pages", type=int, default=None,
                        help=f"page cap (default config.SCRAPER_MAX_PAGES = {config.SCRAPER_MAX_PAGES})")
    parser.add_argument("--include-legacy", action=argparse.BooleanOptionalAction, default=True,
                        help="also use the second official host www.mobitel.lk (default on; --no-include-legacy keeps to www.sltmobitel.lk only)")
    args = parser.parse_args(argv)
    Crawler(max_pages=args.max_pages, include_legacy=args.include_legacy).run()


if __name__ == "__main__":
    main()
