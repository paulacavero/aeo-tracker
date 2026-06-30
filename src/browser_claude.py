"""
Claude.ai browser automation via Playwright.
Same CDP approach as ChatGPT — connects to a normally-launched Chrome instance.
"""

import time
import subprocess
from pathlib import Path
from urllib.parse import urlparse

from .browser_chatgpt import _ensure_chrome_running, CDP_PORT

RESPONSE_TIMEOUT = 120


def _get_domain(url):
    try:
        return urlparse(url).netloc.replace("www.", "")
    except Exception:
        return ""


def run_prompt(playwright, prompt_text, headless=True):
    """
    Submit a prompt to Claude and return:
      { "response_text": str, "urls_cited": [{"url", "domain", "title"}] }
    Returns None on failure.
    """
    _ensure_chrome_running()

    browser = playwright.chromium.connect_over_cdp(f"http://localhost:{CDP_PORT}")
    context = browser.contexts[0] if browser.contexts else browser.new_context()
    page = context.new_page()
    page.bring_to_front()

    try:
        print(f"  [Claude] Loading chat page...")
        page.goto("https://claude.ai/new", wait_until="domcontentloaded", timeout=30000)
        page.bring_to_front()
        page.wait_for_timeout(3000)

        if "login" in page.url or "auth" in page.url:
            print("  [Claude] Not authenticated — please log in in the browser window.")
            deadline = time.time() + 120
            while time.time() < deadline:
                if "claude.ai" in page.url and "login" not in page.url:
                    break
                time.sleep(2)

        textarea = _find_textarea(page)
        if not textarea:
            print("  [Claude] Could not find input textarea.")
            return None

        print(f"  [Claude] Submitting prompt...")
        textarea.click()
        textarea.fill(prompt_text)
        page.wait_for_timeout(500)
        _submit(page)

        print(f"  [Claude] Waiting for response...")
        response_text = _wait_for_response(page)
        if not response_text:
            print("  [Claude] No response received.")
            return None

        urls_cited = _extract_inline_links(page)
        print(f"  [Claude] Done. {len(response_text)} chars, {len(urls_cited)} URLs found.")

        page.close()
        return {"response_text": response_text, "urls_cited": urls_cited}

    except Exception as e:
        print(f"  [Claude] Error: {e}")
        page.close()
        return None


def _find_textarea(page):
    selectors = [
        "div[contenteditable='true'].ProseMirror",
        "div[contenteditable='true'][data-placeholder]",
        "div[contenteditable='true']",
        "textarea[placeholder]",
    ]
    for sel in selectors:
        try:
            el = page.locator(sel).first
            if el.is_visible(timeout=3000):
                return el
        except Exception:
            continue
    return None


def _submit(page):
    selectors = [
        "button[aria-label='Send Message']",
        "button[aria-label='Send message']",
        "button[type='submit']",
        "[data-testid='send-button']",
    ]
    for sel in selectors:
        try:
            btn = page.locator(sel).first
            if btn.is_visible(timeout=1000):
                btn.click()
                return
        except Exception:
            continue
    page.keyboard.press("Enter")


def _wait_for_response(page):
    # Claude marks each assistant message group with data-is-streaming.
    # Wait for the response container to appear, then for streaming to finish.
    for _ in range(30):
        try:
            if page.locator("[data-is-streaming]").count() > 0:
                break
        except Exception:
            pass
        time.sleep(1)

    deadline = time.time() + RESPONSE_TIMEOUT
    while time.time() < deadline:
        try:
            vals = page.locator("[data-is-streaming]").evaluate_all(
                "els => els.map(e => e.getAttribute('data-is-streaming'))"
            )
            if vals and all(v == "false" for v in vals):
                break
        except Exception:
            pass
        time.sleep(1)

    page.wait_for_timeout(1500)
    return _extract_response_text(page)


def _is_visible(page, selector):
    try:
        return page.locator(selector).first.is_visible(timeout=300)
    except Exception:
        return False


def _extract_response_text(page):
    selectors = [
        ".font-claude-response",
        "[data-is-streaming='false'] .font-claude-response",
        ".font-claude-message",
        "[data-is-streaming='false'] .prose",
        "[data-testid='assistant-message']",
        ".prose.max-w-none",
        ".prose",
    ]
    for sel in selectors:
        try:
            elements = page.locator(sel).all()
            if elements:
                text = elements[-1].inner_text()
                if text and len(text) > 20:
                    return text
        except Exception:
            continue
    return None


def _extract_inline_links(page):
    citations = []
    seen_urls = set()
    selectors = [
        ".font-claude-response a[href^='http']",
        ".font-claude-message a[href^='http']",
        ".prose a[href^='http']",
        "[data-testid='assistant-message'] a[href^='http']",
    ]
    for sel in selectors:
        try:
            for link in page.locator(sel).all():
                try:
                    url = link.get_attribute("href")
                    if not url or "claude.ai" in url or "anthropic.com" in url:
                        continue
                    if url in seen_urls:
                        continue
                    seen_urls.add(url)
                    title = link.inner_text().strip() or ""
                    citations.append({"url": url, "domain": _get_domain(url), "title": title})
                except Exception:
                    continue
        except Exception:
            continue
    return citations


def save_auth(playwright, headless=False):
    _ensure_chrome_running()
    browser = playwright.chromium.connect_over_cdp(f"http://localhost:{CDP_PORT}")
    context = browser.contexts[0] if browser.contexts else browser.new_context()
    page = context.new_page()
    page.bring_to_front()
    page.goto("https://claude.ai/new", wait_until="domcontentloaded")
    page.wait_for_timeout(2000)

    print("\nWaiting for Claude chat interface (up to 120s)...")
    print("Log in if prompted.")

    textarea_selectors = [
        "div[contenteditable='true'].ProseMirror",
        "div[contenteditable='true'][data-placeholder]",
        "div[contenteditable='true']",
    ]

    deadline = time.time() + 120
    while time.time() < deadline:
        for sel in textarea_selectors:
            try:
                if page.locator(sel).first.is_visible(timeout=300):
                    page.close()
                    print("Claude: logged in and ready.")
                    return
            except Exception:
                pass
        time.sleep(1)

    page.close()
    print("Claude: timed out. Try again.")
