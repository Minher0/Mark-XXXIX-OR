#web_search.py
import json
import sys
from pathlib import Path

def _get_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


BASE_DIR        = _get_base_dir()
API_CONFIG_PATH = BASE_DIR / "config" / "api_keys.json"


def _get_api_key() -> str:
    with open(API_CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)["gemini_api_key"]


def _gemini_search(query: str) -> str:
    """Legacy function name — now uses the local LLM for the search-style response.

    The Gemini grounded search is no longer available (Gemini API removed).
    This function returns a chat-style answer; for real web results, see the
    main web_search() dispatcher which uses OpenRouter + DuckDuckGo fallback.
    """
    from local_llm import client as llm_client
    result = llm_client.chat(
        query,
        system="You are a web search assistant. Answer factually and concisely "
               "based on your knowledge. Note that you do not have real-time web access.",
        temperature=0.3,
        max_tokens=2048,
    )
    if not result or result.startswith("[local_llm error"):
        raise ValueError("Local LLM returned an empty or error response.")
    return result


def _ddg_search(query: str, max_results: int = 6) -> list[dict]:
    try:
        from ddgs import DDGS
    except ImportError:
        from duckduckgo_search import DDGS

    results = []
    with DDGS() as ddgs:
        for r in ddgs.text(query, max_results=max_results):
            results.append({
                "title":   r.get("title",  ""),
                "snippet": r.get("body",   ""),
                "url":     r.get("href",   ""),
            })
    return results


def _format_ddg(query: str, results: list[dict]) -> str:
    if not results:
        return f"No results found for: {query}"

    lines = [f"Search results for: {query}\n"]
    for i, r in enumerate(results, 1):
        if r.get("title"):   lines.append(f"{i}. {r['title']}")
        if r.get("snippet"): lines.append(f"   {r['snippet']}")
        if r.get("url"):     lines.append(f"   {r['url']}")
        lines.append("")
    return "\n".join(lines).strip()

def _compare(items: list[str], aspect: str) -> str:
    query = (
        f"Compare {', '.join(items)} in terms of {aspect}. "
        "Give specific facts and data."
    )
    try:
        return _gemini_search(query)
    except Exception as e:
        print(f"[WebSearch] ⚠️ Gemini compare failed: {e} — falling back to DDG")

    # DDG fallback: fetch results per item and merge
    all_results: dict[str, list] = {}
    for item in items:
        try:
            all_results[item] = _ddg_search(f"{item} {aspect}", max_results=3)
        except Exception:
            all_results[item] = []

    lines = [f"Comparison — {aspect.upper()}", "─" * 40]
    for item in items:
        lines.append(f"\n▸ {item}")
        for r in all_results.get(item, [])[:2]:
            if r.get("snippet"):
                lines.append(f"  • {r['snippet']}")
    return "\n".join(lines)

def web_search(
    parameters:     dict,
    response=None,
    player=None,
    session_memory=None,
) -> str:
    params = parameters or {}
    query  = params.get("query", "").strip()
    mode   = params.get("mode",  "search").lower().strip()
    items  = params.get("items", [])
    aspect = params.get("aspect", "general").strip() or "general"

    if not query and not items:
        return "Please provide a search query, sir."

    if items and mode != "compare":
        mode = "compare"

    if player:
        player.write_log(f"[Search] {query or ', '.join(items)}")

    print(f"[WebSearch] 🔍 Query: {query!r}  Mode: {mode}")

    # Compare mode — multi-item comparison via _compare()
    if mode == "compare" and items:
        try:
            return _compare(items, aspect)
        except Exception as e:
            print(f"[WebSearch] ❌ Compare failed: {e}")
            return f"Comparison failed, sir: {e}"

    # Search mode — OpenRouter first, DDG fallback
    try:
        from or_client import client
        result = client.chat(
            query,
            system="You are a web search assistant. Answer factually and concisely."
        )
        print("[WebSearch] ✅ OpenRouter OK.")
        return result
    except Exception as e:
        print(f"[WebSearch] ⚠️ OpenRouter failed ({e}) — trying DDG...")

    try:
        results = _ddg_search(query)
        result  = _format_ddg(query, results)
        print(f"[WebSearch] ✅ DDG: {len(results)} result(s).")
        return result
    except Exception as e:
        print(f"[WebSearch] ❌ All backends failed: {e}")
        return f"Search failed, sir: {e}"