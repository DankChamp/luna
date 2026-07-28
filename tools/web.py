from __future__ import annotations

import re

import httpx

from .registry import ToolDef


async def web_fetch(url: str) -> str:
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    try:
        async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
            resp = await client.get(url, headers={"User-Agent": "Luna/1.0"})
            resp.raise_for_status()
            content_type = resp.headers.get("content-type", "")
            text = resp.text

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
    description="Fetch content from a URL and return as plain text",
    parameters={
        "url": {
            "type": "string",
            "description": "The URL to fetch",
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
