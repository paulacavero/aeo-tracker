"""
Brand mention and citation detection.
Works on plain text extracted from AI responses.
"""

import re
from urllib.parse import urlparse


def detect_brands(text, brand_config, competitors_config):
    """
    Returns a list of brand names (strings) found in the text.
    Checks your brand + all competitors.
    Case-insensitive, whole-word aware.
    """
    found = []
    all_entities = [brand_config] + competitors_config

    for entity in all_entities:
        for keyword in entity["keywords"]:
            # Use word-boundary regex for clean matching
            pattern = re.compile(r'\b' + re.escape(keyword) + r'\b', re.IGNORECASE)
            if pattern.search(text):
                if entity["name"] not in found:
                    found.append(entity["name"])
                break  # one keyword match is enough per brand

    return found


def detect_latitude_mentioned(brands_found, brand_name):
    return brand_name in brands_found


def detect_urls(text):
    """
    Extract all URLs from response text.
    Returns a list of dicts: {url, domain, title}
    Title is empty unless it was embedded in the DOM — filled by browser extractors.
    """
    # Regex to find URLs (http/https)
    url_pattern = re.compile(
        r'https?://[^\s\)\]\>\"\'\,\;\:]+',
        re.IGNORECASE
    )
    raw_urls = url_pattern.findall(text)

    # Clean trailing punctuation that sometimes gets caught
    cleaned = []
    for url in raw_urls:
        url = url.rstrip(".,;:!?\"'")
        parsed = urlparse(url)
        if parsed.netloc:  # valid URL with a domain
            domain = parsed.netloc.replace("www.", "")
            if not any(u["url"] == url for u in cleaned):
                cleaned.append({"url": url, "domain": domain, "title": ""})

    return cleaned


def detect_latitude_cited(urls_cited, brand_domain):
    """Check if our domain appears in any cited URL."""
    for item in urls_cited:
        if brand_domain in item.get("domain", "") or brand_domain in item.get("url", ""):
            return True
    return False


def merge_url_lists(from_dom, from_text):
    """
    Combine URLs extracted from DOM (with titles) and from plain text.
    DOM results take priority; text-only results are added if not already present.
    """
    seen = {item["url"] for item in from_dom}
    merged = list(from_dom)
    for item in from_text:
        if item["url"] not in seen:
            merged.append(item)
            seen.add(item["url"])
    return merged
