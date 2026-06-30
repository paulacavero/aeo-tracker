"""
Claude via Anthropic API, with the web_search server tool enabled.

Web search makes Claude browse the live web and return real citations
(url/title/cited_text) — comparable to the consumer claude.ai experience and to
our OpenAI search path. Requires web search to be enabled for your org in the
Anthropic Console; if it isn't, we fall back to a no-search answer and warn.

Sonnet is the most representative of typical claude.ai usage (free tier +
common default) and far cheaper for a daily job. Swap MODEL to an Opus id
(e.g. claude-opus-4-8) if you'd rather track the premium tier.
"""

import os
import re
from pathlib import Path
from urllib.parse import urlparse
from dotenv import load_dotenv
import anthropic

load_dotenv(Path(__file__).parent.parent / ".env", override=True)

MODEL = "claude-sonnet-4-6"
# Web search results get pulled into context and answers run long, so give the
# response room (the no-search default of 2048 truncated mid-answer in testing).
MAX_TOKENS = 4096
# Server-side web search tool. max_uses caps searches per prompt (cost control;
# each search bills ~$10/1k on top of tokens).
WEB_SEARCH_TOOL = {"type": "web_search_20250305", "name": "web_search", "max_uses": 5}

_client = None

def _get_client():
    global _client
    if _client is None:
        key = os.environ.get("ANTHROPIC_API_KEY")
        if not key:
            raise ValueError("ANTHROPIC_API_KEY not set in .env")
        _client = anthropic.Anthropic(api_key=key)
    return _client


def run_prompt(prompt_text):
    """
    Submit a prompt (with web search) and return:
      { "response_text": str, "urls_cited": [{"url", "domain", "title"}] }
    Returns None on failure.
    """
    client = _get_client()

    try:
        return _query(client, prompt_text, with_search=True)
    except Exception as e:
        # Most likely: web search not enabled for the org. Fall back so the run
        # still produces data, but make the degradation visible.
        print(f"  [Claude] web search call failed ({e}); retrying without web search.")
        try:
            return _query(client, prompt_text, with_search=False)
        except Exception as e2:
            print(f"  [Claude] Error: {e2}")
            return None


def _query(client, prompt_text, with_search):
    kwargs = dict(
        model=MODEL,
        max_tokens=MAX_TOKENS,
        messages=[{"role": "user", "content": prompt_text}],
    )
    if with_search:
        kwargs["tools"] = [WEB_SEARCH_TOOL]

    response = client.messages.create(**kwargs)

    # With tool use the response has multiple blocks (server_tool_use,
    # web_search_tool_result, and one or more text blocks). Concatenate the
    # text blocks, and pull citations off them.
    text_parts = []
    urls_cited = []
    seen = set()
    for block in response.content:
        if getattr(block, "type", None) != "text":
            continue
        text_parts.append(getattr(block, "text", "") or "")
        for c in (getattr(block, "citations", None) or []):
            url = getattr(c, "url", None)
            if not url or url in seen:
                continue
            seen.add(url)
            title = getattr(c, "title", "") or ""
            urls_cited.append({"url": url, "domain": _get_domain(url), "title": title})

    response_text = "".join(text_parts)

    # If no web-search citations (e.g. Claude didn't search, or fallback path),
    # still capture any URLs Claude wrote inline.
    if not urls_cited:
        urls_cited = _extract_urls(response_text)

    searched = " (web search)" if with_search else " (no search)"
    print(f"  [Claude] {len(response_text)} chars, {len(urls_cited)} citations{searched}.")
    return {"response_text": response_text, "urls_cited": urls_cited}


def _get_domain(url):
    try:
        return urlparse(url).netloc.replace("www.", "")
    except Exception:
        return ""


def _extract_urls(text):
    pattern = re.compile(r'https?://[^\s\)\]\>\"\'\,\;\:]+', re.IGNORECASE)
    urls = []
    seen = set()
    for url in pattern.findall(text):
        url = url.rstrip(".,;:!?\"'")
        if url in seen:
            continue
        seen.add(url)
        try:
            domain = urlparse(url).netloc.replace("www.", "")
        except Exception:
            domain = ""
        urls.append({"url": url, "domain": domain, "title": ""})
    return urls
