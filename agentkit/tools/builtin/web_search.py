"""Web search tool adapter integrating external search APIs with graceful fallbacks."""

import httpx

from agentkit.config import get_settings
from agentkit.tools.registry import tool

DEFAULT_MAX_RESULTS = 5
MAX_ALLOWED_RESULTS = 10
TAVILY_SEARCH_URL = "https://api.tavily.com/search"
DEFAULT_SEARCH_TIMEOUT_S = 15.0


async def search_web(
    query: str,
    max_results: int = DEFAULT_MAX_RESULTS,
    api_key: str | None = None,
    timeout_s: float = DEFAULT_SEARCH_TIMEOUT_S,
) -> str:
    """Execute a web search query via the search API and return formatted results.

    Args:
        query: Search keywords or question.
        max_results: Maximum number of search results to return (capped at 10).
        api_key: Optional explicit API key override. Defaults to Settings.SEARCH_API_KEY.
        timeout_s: Request timeout in seconds.

    Returns:
        Formatted string containing titles, URLs, and snippet excerpts.

    Raises:
        ValueError: If SEARCH_API_KEY is not configured or query is empty.
    """
    key = api_key or get_settings().SEARCH_API_KEY
    if not key:
        raise ValueError(
            "SEARCH_API_KEY is not configured. Set SEARCH_API_KEY in the environment or .env file to enable web search."
        )

    clean_query = query.strip()
    if not clean_query:
        raise ValueError("Empty or whitespace-only search query.")

    limit = max(1, min(max_results, MAX_ALLOWED_RESULTS))
    payload = {
        "api_key": key,
        "query": clean_query,
        "max_results": limit,
    }

    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(timeout_s)) as client:
            response = await client.post(TAVILY_SEARCH_URL, json=payload)
            response.raise_for_status()
            data = response.json()
    except httpx.HTTPStatusError as exc:
        raise ValueError(
            f"Search API returned error HTTP {exc.response.status_code}: {exc.response.text}"
        ) from exc
    except httpx.RequestError as exc:
        raise ValueError(f"Failed to communicate with search API: {exc}") from exc

    results = data.get("results", [])
    if not results:
        return f"No search results found for query: '{clean_query}'"

    formatted_items: list[str] = []
    for idx, item in enumerate(results[:limit], start=1):
        title = item.get("title", "Untitled").strip()
        url = item.get("url", "").strip()
        content = item.get("content", "").strip()
        formatted_items.append(f"{idx}. {title}\n   URL: {url}\n   Snippet: {content}")

    return "\n\n".join(formatted_items)


@tool
async def web_search(query: str) -> str:
    """Search the web for real-time information, documentation, and external references.

    Returns the top relevant web results including title, URL, and snippet summary.
    Requires SEARCH_API_KEY configured in environment.
    """
    return await search_web(query)
