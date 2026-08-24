"""
ChatGPT via OpenAI API using a search-enabled model.
Includes web search, so citations are real URLs — same as the UI.
"""

import os
from urllib.parse import urlparse, parse_qsl, urlencode, urlunparse
from openai import OpenAI

# gpt-4o-search-preview was retired — it still appears in models.list() but
# every call 404s, so don't go back to it without checking.
MODEL = "gpt-5-search-api"

# This model appends ?utm_source=openai to every citation. Claude's come back
# clean, so keeping the param would make one article look like two different
# URLs in citations.json and split its times_cited — the number the outreach
# ranking is built on.
TRACKING_PARAMS = ("utm_", "ref_", "fbclid", "gclid", "mc_cid", "mc_eid")

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
            url = _clean_url(url)
            if url in seen:
                continue
            seen.add(url)
            title = getattr(uc, "title", "") or ""
            domain = _get_domain(url)
            urls_cited.append({"url": url, "domain": domain, "title": title})

        print(f"  [ChatGPT] {len(response_text)} chars, {len(urls_cited)} citations.")
        return {"response_text": response_text, "urls_cited": urls_cited}

    except Exception as e:
        print(f"  [ChatGPT] Error: {e}")
        return None


def _get_domain(url):
    try:
        return urlparse(url).netloc.replace("www.", "")
    except Exception:
        return ""


def _clean_url(url):
    """Drop tracking params so the same article dedupes across engines."""
    try:
        parsed = urlparse(url)
        if not parsed.query:
            return url
        kept = [(k, v) for k, v in parse_qsl(parsed.query)
                if not k.lower().startswith(TRACKING_PARAMS)]
        return urlunparse(parsed._replace(query=urlencode(kept)))
    except Exception:
        return url
