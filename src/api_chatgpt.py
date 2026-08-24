"""
ChatGPT via OpenAI API using a search-enabled model.
Includes web search, so citations are real URLs — same as the UI.
"""

import os
from openai import OpenAI

from . import urls

# gpt-4o-search-preview was retired — it still appears in models.list() but
# every call 404s, so don't go back to it without checking.
MODEL = "gpt-5-search-api"

_client = None

def _get_client():
    global _client
    if _client is None:
        _client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
    return _client


def run_prompt(prompt_text):
    """
    Submit a prompt and return:
      { "response_text": str, "urls_cited": [{"url", "domain", "title"}] }
    Returns None on failure.
    """
    client = _get_client()

    try:
        response = client.chat.completions.create(
            model=MODEL,
            messages=[{"role": "user", "content": prompt_text}],
        )

        message = response.choices[0].message
        response_text = message.content or ""

        # Extract citations from annotations (web search results)
        urls_cited = []
        seen = set()
        annotations = getattr(message, "annotations", []) or []
        for ann in annotations:
            # annotations are nested: ann.url_citation.url / .title
            uc = getattr(ann, "url_citation", None)
            if uc is None:
                continue
            url = getattr(uc, "url", None)
            if not url:
                continue
            url = urls.clean_url(url)
            if url in seen:
                continue
            seen.add(url)
            title = getattr(uc, "title", "") or ""
            domain = urls.get_domain(url)
            urls_cited.append({"url": url, "domain": domain, "title": title})

        print(f"  [ChatGPT] {len(response_text)} chars, {len(urls_cited)} citations.")
        return {"response_text": response_text, "urls_cited": urls_cited}

    except Exception as e:
        print(f"  [ChatGPT] Error: {e}")
        return None


