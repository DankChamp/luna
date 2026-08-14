from __future__ import annotations

import ipaddress
import re
from urllib.parse import urlparse

import httpx

from .registry import ToolDef

# Private IP ranges to block (SSRF protection)
_PRIVATE_IP_RANGES = [
    ipaddress.ip_network("127.0.0.0/8"),      # Loopback
    ipaddress.ip_network("10.0.0.0/8"),       # Private Class A
    ipaddress.ip_network("172.16.0.0/12"),    # Private Class B
    ipaddress.ip_network("192.168.0.0/16"),   # Private Class C
    ipaddress.ip_network("169.254.0.0/16"),   # Link-local
    ipaddress.ip_network("::1/128"),          # IPv6 loopback
    ipaddress.ip_network("fc00::/7"),         # IPv6 private
    ipaddress.ip_network("fe80::/10"),        # IPv6 link-local
]

# Metadata service endpoints to block
_BLOCKED_HOSTS = {
    "metadata.google.internal",
    "metadata",
    "169.254.169.254",
    "[::1]",
    "localhost",
    "localhost.localdomain",
}

# Allowed schemes
_ALLOWED_SCHEMES = {"http", "https"}

# Maximum response size (1MB)
_MAX_RESPONSE_SIZE = 1_000_000


def _is_private_ip(host: str) -> bool:
    """Check if host resolves to a private IP address."""
    try:
        # Try parsing as IP directly
        ip = ipaddress.ip_address(host)
        return any(ip in network for network in _PRIVATE_IP_RANGES)
    except ValueError:
        # Hostname - check if it's a known blocked host
        if host.lower() in _BLOCKED_HOSTS:
            return True
        # For hostnames, we'd need DNS resolution to check IP
        # This is a basic check - production should resolve and verify
        return False


def _validate_url(url: str) -> tuple[bool, str]:
    """Validate URL for SSRF protection. Returns (is_valid, error_message)."""
    try:
        parsed = urlparse(url)
    except Exception:
        return False, "Invalid URL format"

    # Check scheme
    if parsed.scheme.lower() not in _ALLOWED_SCHEMES:
        return False, f"Scheme '{parsed.scheme}' not allowed (only http/https)"

    # Check host
    host = parsed.hostname or ""
    if not host:
        return False, "Missing hostname"

    # Block known metadata/internal hosts
    if host.lower() in _BLOCKED_HOSTS:
        return False, f"Access to '{host}' is blocked"

    # Block private IP addresses in URL
    if _is_private_ip(host):
        return False, f"Access to private IP '{host}' is blocked"

    # Note: Full SSRF protection would require DNS resolution and IP checking
    # before connecting. For now we block known bad hosts and private IPs in URL.
    # A production system should resolve hostname and verify IP is not private.

    return True, ""


async def web_fetch(url: str) -> str:
    """Fetch content from a URL with SSRF protection."""
    if not url.startswith(("http://", "https://")):
        url = "https://" + url

    # Validate URL
    valid, error = _validate_url(url)
    if not valid:
        return f"Error: {error}"

    try:
        # Use a client with no redirect following for security
        async with httpx.AsyncClient(
            timeout=30.0,
            follow_redirects=False,  # Don't follow redirects (SSRF risk)
            limits=httpx.Limits(max_connections=1, max_keepalive_connections=0),
        ) as client:
            resp = await client.get(
                url,
                headers={"User-Agent": "Luna/1.0"},
            )
            
            # Handle redirects manually if needed (with validation)
            if resp.is_redirect:
                location = resp.headers.get("location")
                if location:
                    # Validate redirect target
                    valid, error = _validate_url(location)
                    if not valid:
                        return f"Error: Redirect blocked - {error}"
                    # Could follow one redirect with validation, but skip for safety
                    return f"Error: Redirects not followed for security (target: {location})"

            resp.raise_for_status()
            
            # Check content length before reading
            content_length = resp.headers.get("content-length")
            if content_length and int(content_length) > _MAX_RESPONSE_SIZE:
                return f"Error: Response too large ({content_length} bytes)"

            content_type = resp.headers.get("content-type", "")
            text = resp.text

            # Enforce max size on actual content
            if len(text) > _MAX_RESPONSE_SIZE:
                text = text[:_MAX_RESPONSE_SIZE] + "\n... (truncated)"

            if "text/html" in content_type or "text/plain" in content_type:
                text = re.sub(r"<style[^>]*>.*?</style>", "", text, flags=re.DOTALL)
                text = re.sub(r"<script[^>]*>.*?</script>", "", text, flags=re.DOTALL)
                text = re.sub(r"<[^>]+>", " ", text)
                text = re.sub(r"\s+", " ", text).strip()
                if len(text) > 8000:
                    text = text[:8000] + "\n... (truncated)"
                return text

            if len(text) > 8000:
                text = text[:8000] + "\n... (truncated)"
            return text
    except httpx.HTTPStatusError as e:
        return f"HTTP {e.response.status_code}"
    except httpx.TimeoutException:
        return "Error: request timed out"
    except Exception as e:
        return f"Error: {e}"


async def web_search(query: str, count: int = 10) -> str:
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(
                "https://api.duckduckgo.com/",
                params={"q": query, "format": "json", "no_html": 1},
            )
            if resp.status_code == 200:
                data = resp.json()
                results = []
                topics = data.get("RelatedTopics", [])
                for t in topics[:min(count, 20)]:
                    if "Text" in t and "FirstURL" in t:
                        results.append(f"- {t['Text']}\n  {t['FirstURL']}")
                    elif "Topics" in t:
                        for sub in t["Topics"][:3]:
                            if "Text" in sub and "FirstURL" in sub:
                                results.append(f"- {sub['Text']}\n  {sub['FirstURL']}")
                if results:
                    return "\n".join(results)
                return f"No results for '{query}'"
            return f"Search API returned HTTP {resp.status_code}"
    except Exception as e:
        return f"Search error: {e}"


web_fetch_tool = ToolDef(
    name="web_fetch",
    description="Fetch content from a URL and return as plain text. SSRF-protected: blocks private IPs, metadata endpoints, and redirects.",
    parameters={
        "url": {
            "type": "string",
            "description": "The URL to fetch (http/https only, no private/internal addresses)",
        },
    },
    required=["url"],
    handler=web_fetch,
)

web_search_tool = ToolDef(
    name="web_search",
    description="Search the web using DuckDuckGo",
    parameters={
        "query": {
            "type": "string",
            "description": "Search query",
        },
        "count": {
            "type": "integer",
            "description": "Number of results (max 10)",
        },
    },
    required=["query"],
    handler=web_search,
)
