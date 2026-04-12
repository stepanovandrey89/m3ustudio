"""HLS stream proxy.

Browsers can't fetch cross-origin HLS streams without CORS headers, and many
IPTV providers serve plain `.m3u8` manifests without `Access-Control-Allow-Origin`.
We proxy both the playlist manifest and its segments, rewriting relative URIs
inside the manifest to route back through us.

The proxy is intentionally minimal — no auth, no quota. It only runs on localhost.
"""

from __future__ import annotations

from urllib.parse import urlencode, urljoin, urlparse

import httpx
from fastapi import HTTPException
from fastapi.responses import Response

_HOP_BY_HOP = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailers",
    "transfer-encoding",
    "upgrade",
    "content-length",
    "content-encoding",
}


def _filter_headers(headers: dict[str, str]) -> dict[str, str]:
    return {k: v for k, v in headers.items() if k.lower() not in _HOP_BY_HOP}


def _rewrite_manifest(body: str, upstream_url: str, proxy_base: str) -> str:
    """Rewrite relative URIs inside a .m3u8 manifest to route through /api/proxy."""
    lines = body.splitlines()
    out: list[str] = []
    base = upstream_url.rsplit("/", 1)[0] + "/"

    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            # Handle KEY URI attribute inline (#EXT-X-KEY:METHOD=AES-128,URI="...")
            if "URI=" in stripped:
                import re

                def sub(m: re.Match[str]) -> str:
                    raw_uri = m.group(1)
                    absolute = raw_uri if raw_uri.startswith("http") else urljoin(base, raw_uri)
                    return f'URI="{proxy_base}?{urlencode({"u": absolute})}"'

                line = re.sub(r'URI="([^"]+)"', sub, line)
            out.append(line)
            continue

        absolute = stripped if stripped.startswith("http") else urljoin(base, stripped)
        out.append(f"{proxy_base}?{urlencode({'u': absolute})}")

    return "\n".join(out) + "\n"


async def proxy_stream(url: str, proxy_base: str) -> Response:
    if not url or urlparse(url).scheme not in ("http", "https"):
        raise HTTPException(400, "Invalid upstream URL")

    headers = {
        "User-Agent": "VLC/3.0.20 LibVLC/3.0.20",
        "Accept": "*/*",
    }

    async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
        try:
            resp = await client.get(url, headers=headers)
        except httpx.HTTPError as exc:
            raise HTTPException(502, f"Upstream fetch failed: {exc}") from exc

    content_type = resp.headers.get("content-type", "").lower()

    # Rewrite manifests so relative segment URIs come back through us
    if url.endswith(".m3u8") or "mpegurl" in content_type:
        body = resp.content.decode("utf-8", errors="ignore")
        rewritten = _rewrite_manifest(body, url, proxy_base)
        return Response(
            content=rewritten,
            status_code=resp.status_code,
            media_type="application/vnd.apple.mpegurl",
            headers={"Cache-Control": "no-store"},
        )

    # Segments and other binary payloads stream through as-is
    return Response(
        content=resp.content,
        status_code=resp.status_code,
        media_type=content_type or "application/octet-stream",
        headers=_filter_headers(dict(resp.headers)),
    )
