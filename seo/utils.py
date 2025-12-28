import re
from urllib.parse import urlparse, urljoin, urldefrag

def normalize_url(url: str) -> str:
    url = url.strip()
    url, _frag = urldefrag(url)
    if not url:
        return url
    parsed = urlparse(url)
    if not parsed.scheme:
        url = "https://" + url
        parsed = urlparse(url)
    # normalize path
    path = parsed.path or "/"
    # remove duplicate slashes
    path = re.sub(r"/{2,}", "/", path)
    # keep trailing slash on root only
    if path != "/" and path.endswith("/"):
        path = path[:-1]
    rebuilt = parsed._replace(path=path, params="", fragment="").geturl()
    return rebuilt

def same_host(a: str, b: str) -> bool:
    return urlparse(a).netloc.lower() == urlparse(b).netloc.lower()

def is_http_url(url: str) -> bool:
    p = urlparse(url)
    return p.scheme in ("http", "https")

def absolutize(base: str, href: str) -> str:
    return normalize_url(urljoin(base, href))
