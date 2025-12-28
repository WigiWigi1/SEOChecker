from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple
import requests
from urllib.parse import urlparse

from .utils import normalize_url, absolutize, same_host, is_http_url
from .parser import parse_html, extract_head_data, extract_body_signals, parse_jsonld_blocks


@dataclass
class PageData:
    url: str
    final_url: str
    status_code: int
    headers: dict
    redirect_chain: list
    depth: int = 0
    html: str = ""
    head: dict = field(default_factory=dict)
    body: dict = field(default_factory=dict)
    jsonld: dict = field(default_factory=dict)


@dataclass
class CrawlResult:
    target_url: str
    normalized_url: str
    host: str
    pages: Dict[str, PageData]
    crawled_urls: List[str]
    errors: List[str]
    robots_txt: Optional[str]
    robots_status: Optional[int]
    sitemap_url: Optional[str]
    sitemap_xml: Optional[str]
    sitemap_status: Optional[int]


class MiniCrawler:
    def __init__(self, user_agent: str, timeout_s: int, max_bytes: int):
        self.user_agent = user_agent
        self.timeout_s = timeout_s
        self.max_bytes = max_bytes

    def _trim_bytes(self, text: str) -> str:
        if not text:
            return ""
        b = text.encode("utf-8", errors="ignore")
        if len(b) <= self.max_bytes:
            return text
        return b[: self.max_bytes].decode("utf-8", errors="ignore")

    def _fetch(self, url: str) -> Tuple[Optional[requests.Response], list, str, Optional[str]]:
        headers = {
            "User-Agent": self.user_agent,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
        }
        chain = []
        try:
            r = requests.get(url, headers=headers, timeout=self.timeout_s, allow_redirects=True)
            for h in r.history:
                chain.append({"url": h.url, "status": h.status_code})
            body = self._trim_bytes(r.text or "")
            return r, chain, body, None
        except Exception as e:
            return None, [], "", f"{type(e).__name__}: {e}"

    def _fetch_text(self, url: str) -> Tuple[Optional[str], Optional[int], Optional[str]]:
        headers = {"User-Agent": self.user_agent, "Accept": "*/*"}
        try:
            r = requests.get(url, headers=headers, timeout=self.timeout_s, allow_redirects=True)
            txt = self._trim_bytes(r.text or "")
            return txt, r.status_code, None
        except Exception as e:
            return None, None, f"{type(e).__name__}: {e}"

    def _discover_and_fetch_sitemap(self, start_url: str, robots_txt: Optional[str]) -> Tuple[Optional[str], Optional[str], Optional[int]]:
        host = urlparse(start_url).netloc.lower()
        base = f"{urlparse(start_url).scheme}://{host}"

        candidates = []
        if robots_txt:
            for line in robots_txt.splitlines():
                line = line.strip()
                if line.lower().startswith("sitemap:"):
                    sm = line.split(":", 1)[1].strip()
                    candidates.append(sm)

        candidates += [
            f"{base}/sitemap.xml",
            f"{base}/sitemap_index.xml",
            f"{base}/sitemap-index.xml"
        ]

        for sm_url in candidates:
            sm_url = normalize_url(sm_url)
            txt, st, err = self._fetch_text(sm_url)
            if err:
                continue
            if st and 200 <= st < 300 and txt:
                low = txt.lower()
                if ("<urlset" in low) or ("<sitemapindex" in low):
                    return sm_url, txt, st

        fallback = normalize_url(f"{base}/sitemap.xml")
        txt, st, _err = self._fetch_text(fallback)
        return fallback, txt, st

    def crawl(self, target_url: str, max_pages: int = 10, max_depth: int = 2) -> CrawlResult:
        start = normalize_url(target_url)
        host = urlparse(start).netloc.lower()

        pages: Dict[str, PageData] = {}
        visited = set()
        queue: List[Tuple[str, int]] = [(start, 0)]
        errors: List[str] = []

        robots_url = f"{urlparse(start).scheme}://{host}/robots.txt"
        robots_txt, robots_status, robots_err = self._fetch_text(robots_url)
        if robots_err:
            errors.append(f"{robots_url}: {robots_err}")

        sitemap_url, sitemap_xml, sitemap_status = self._discover_and_fetch_sitemap(start, robots_txt)

        while queue and len(pages) < max_pages:
            url, depth = queue.pop(0)
            url = normalize_url(url)
            if url in visited:
                continue
            visited.add(url)

            if not is_http_url(url):
                continue

            if urlparse(url).netloc.lower() != host and len(pages) > 0:
                continue

            resp, chain, body, err = self._fetch(url)
            if err:
                errors.append(f"{url}: {err}")
                continue
            assert resp is not None

            final_host = urlparse(resp.url).netloc.lower()
            if len(pages) == 0 and final_host != host:
                host = final_host

            content_type = (resp.headers.get("content-type") or "").lower()
            html = body if ("text/html" in content_type or "application/xhtml+xml" in content_type or content_type == "") else ""

            page = PageData(
                url=url,
                final_url=normalize_url(resp.url),
                status_code=resp.status_code,
                headers={k.lower(): v for k, v in resp.headers.items()},
                redirect_chain=chain,
                depth=depth,
                html=html
            )

            if html:
                soup = parse_html(html)
                page.head = extract_head_data(soup)
                page.body = extract_body_signals(soup)
                page.jsonld = parse_jsonld_blocks(page.head.get("jsonld_blocks", []))

                # build internal links list (absolute + same-host)
                internal_links = []
                for href in page.body.get("all_links", []):
                    absu = absolutize(page.final_url, href)
                    if not absu:
                        continue
                    if same_host(absu, page.final_url):
                        internal_links.append(normalize_url(absu))
                page.body["internal_links"] = internal_links
                page.body["internal_links_count"] = len(internal_links)

                if depth < max_depth:
                    for absu in internal_links:
                        if absu not in visited:
                            queue.append((absu, depth + 1))

            pages[url] = page

        return CrawlResult(
            target_url=target_url,
            normalized_url=start,
            host=host,
            pages=pages,
            crawled_urls=list(pages.keys()),
            errors=errors,
            robots_txt=robots_txt,
            robots_status=robots_status,
            sitemap_url=sitemap_url,
            sitemap_xml=sitemap_xml,
            sitemap_status=sitemap_status
        )
