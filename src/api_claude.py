"""
Claude via Anthropic API.

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
    Submit a prompt and return:
      { "response_text": str, "urls_cited": [{"url", "domain", "title"}] }
    Returns None on failure.
    """
    client = _get_client()

    try:
        response = client.messages.create(
            model=MODEL,
            max_tokens=2048,
            messages=[{"role": "user", "content": prompt_text}],
        )

        response_text = response.content[0].text if response.content else ""

        # Claude doesn't browse the web, but sometimes cites URLs inline
        urls_cited = _extract_urls(response_text)

        print(f"  [Claude] {len(response_text)} chars, {len(urls_cited)} URLs found.")
        return {"response_text": response_text, "urls_cited": urls_cited}

    except Exception as e:
        print(f"  [Claude] Error: {e}")
        return None


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
