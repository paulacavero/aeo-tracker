"""
URL helpers shared by the engine modules.

Both engines tag citations with their own tracking params — ChatGPT appends
?utm_source=chatgpt.com in the UI and ?utm_source=openai via the API. These
have to be stripped before storing, for two reasons:

  1. citations.json is keyed by URL, so the same article arriving from two
     engines with different params would become two entries and split its
     times_cited — the number the outreach ranking sorts on.
  2. A substring check for "chatgpt.com" against a URL carrying
     ?utm_source=chatgpt.com matches the param, not the host. That is exactly
     how the browser scraper silently discarded every ChatGPT citation it
     collected between 2026-06-30 and 2026-08-24. Filter on host, never on the
     whole URL.
"""

from urllib.parse import urlparse, parse_qsl, urlencode, urlunparse

TRACKING_PARAMS = ("utm_", "ref_", "fbclid", "gclid", "mc_cid", "mc_eid")

# Hosts that are the AI product itself rather than a cited source.
INTERNAL_HOSTS = ("openai.com", "chatgpt.com", "anthropic.com", "claude.ai")


def get_domain(url):
    try:
        return urlparse(url).netloc.replace("www.", "")
    except Exception:
        return ""


def clean_url(url):
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


def is_internal(url):
    """True for links back into the AI product, which aren't citations."""
    host = get_domain(url).lower()
    return any(host == h or host.endswith("." + h) for h in INTERNAL_HOSTS)
