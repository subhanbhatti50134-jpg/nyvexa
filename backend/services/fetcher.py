"""Fetch web pages and extract readable text for research/RAG."""

from __future__ import annotations

import ipaddress
import re
import socket
from dataclasses import dataclass
from urllib.parse import urljoin, urlparse

import httpx

USER_AGENT = "Nyvexa/1.0 (+https://localhost; research assistant)"
MAX_BYTES = 1_500_000
TIMEOUT = 12.0
MAX_REDIRECTS = 5

# 100.64.0.0/10 (CGNAT) is internal but not flagged by ipaddress defaults
_CGNAT = ipaddress.ip_network("100.64.0.0/10")


@dataclass
class Page:
    url: str
    title: str
    text: str


def _clean(text: str) -> str:
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _ip_blocked(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    if ip in _CGNAT:
        return True
    return bool(
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
        or getattr(ip, "is_site_local", False)
    )


def url_is_public(url: str) -> bool:
    """True only for http(s) URLs whose hostname resolves solely to public IPs.

    Blocks loopback, RFC1918, link-local (incl. 169.254.169.254 metadata),
    CGNAT, multicast, reserved and unspecified addresses — SSRF defense for the
    research/agent fetch path. Each redirect hop must re-pass this check.
    """
    try:
        parts = urlparse(url)
    except ValueError:
        return False
    if parts.scheme not in ("http", "https"):
        return False
    if not parts.hostname:
        return False
    if parts.username or parts.password:  # userinfo tricks (http://good@internal)
        return False
    port = parts.port or (443 if parts.scheme == "https" else 80)
    try:
        infos = socket.getaddrinfo(parts.hostname, port, proto=socket.IPPROTO_TCP)
    except (socket.gaierror, OSError, ValueError):
        return False
    if not infos:
        return False
    for info in infos:
        raw = info[4][0].split("%")[0]
        try:
            ip = ipaddress.ip_address(raw)
        except ValueError:
            return False
        if _ip_blocked(ip):
            return False
    return True


def extract_text(html: str) -> tuple[str, str]:
    """Return (title, text) using a lightweight HTML parse."""
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        # Fallback: crude tag strip
        title = re.search(r"<title[^>]*>(.*?)</title>", html, re.IGNORECASE | re.DOTALL)
        title = _clean(re.sub(r"<[^>]+>", "", title.group(1))) if title else ""
        body = re.sub(r"(?is)<(script|style|noscript)[^>]*>.*?</\1>", " ", html)
        body = re.sub(r"(?s)<[^>]+>", " ", body)
        return title, _clean(body)[:20000]

    soup = BeautifulSoup(html, "html.parser")
    title = soup.title.get_text(strip=True) if soup.title else ""
    drop = [
        "script", "style", "noscript", "svg", "iframe",
        "nav", "footer", "header", "aside", "form", "button",
    ]
    for tag in soup(drop):
        tag.decompose()
    # Prefer <article> / <main> when present
    main = soup.find("article") or soup.find("main") or soup.body or soup
    text = main.get_text(separator="\n")
    return title, _clean(text)[:20000]


async def fetch_page(url: str) -> Page | None:
    """Fetch a URL with every redirect hop validated; returns None on failure."""
    if not url.startswith(("http://", "https://")):
        return None
    current = url
    try:
        async with httpx.AsyncClient(
            timeout=TIMEOUT,
            follow_redirects=False,  # validate each hop — redirects can reach internal hosts
            headers={"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml"},
        ) as client:
            resp = None
            for _ in range(MAX_REDIRECTS + 1):
                if not url_is_public(current):
                    return None
                resp = await client.get(current)
                if resp.status_code in (301, 302, 303, 307, 308):
                    location = resp.headers.get("location")
                    if not location:
                        return None
                    current = urljoin(current, location)
                    continue
                break
            else:
                return None  # redirect loop / too many hops

            if resp is None or resp.status_code >= 400:
                return None
            ctype = resp.headers.get("content-type", "")
            if "html" not in ctype and "text" not in ctype and ctype:
                return None
            raw = resp.content[:MAX_BYTES]
            html = raw.decode(resp.charset or "utf-8", errors="replace")
            title, text = extract_text(html)
            if len(text) < 80:
                return None
            return Page(url=str(resp.url), title=title or url, text=text)
    except Exception:
        return None


async def fetch_pages(urls: list[str], limit: int = 3) -> list[Page]:
    """Fetch up to `limit` pages concurrently."""
    import asyncio

    tasks = [fetch_page(u) for u in urls[:limit]]
    results = await asyncio.gather(*tasks)
    return [p for p in results if p is not None]
