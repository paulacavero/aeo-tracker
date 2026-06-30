"""
ChatGPT browser automation via Playwright.

Launches a dedicated automation Chrome with remote debugging, then connects to
it via CDP (Chrome DevTools Protocol). This avoids bot detection since Chrome
isn't launched by Playwright — Playwright just remote-controls it.

Chrome 136+ refuses remote debugging on the default profile, so we run a
separate --user-data-dir. That profile runs alongside your normal Chrome and
keeps its own logins.
"""

import time
import subprocess
from pathlib import Path
from urllib.parse import urlparse

CHROME_BIN = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
CDP_PORT = 9222
# Dedicated automation profile (under auth/, gitignored). Persists logins
# between runs and lets automation Chrome coexist with your normal Chrome.
PROFILE_DIR = Path(__file__).resolve().parent.parent / "auth" / "chrome_profile"
RESPONSE_TIMEOUT = 120


def _get_domain(url):
    try:
        return urlparse(url).netloc.replace("www.", "")
    except Exception:
        return ""


def _ensure_chrome_running():
    """
    Launch the dedicated automation Chrome with remote debugging if it isn't
    already up. Uses a separate --user-data-dir so it runs alongside your normal
    Chrome (Chrome 136+ blocks remote debugging on the default profile).
    """
    import urllib.request
    # Check if debugging port is already open
    try:
        urllib.request.urlopen(f"http://localhost:{CDP_PORT}/json", timeout=2)
        print("  Automation Chrome already running with debug port.")
        return
    except Exception:
        pass

    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    print("  Launching automation Chrome (separate profile — your normal Chrome is untouched)...")
    subprocess.Popen(
        [
            CHROME_BIN,
            f"--remote-debugging-port={CDP_PORT}",
            f"--user-data-dir={PROFILE_DIR}",
            "--no-first-run",
            "--no-default-browser-check",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    # Wait for Chrome to be ready
    for _ in range(20):
        try:
            urllib.request.urlopen(f"http://localhost:{CDP_PORT}/json", timeout=1)
            print("  Automation Chrome is ready.")
            return
        except Exception:
            time.sleep(1)

    raise RuntimeError("Chrome did not start in time.")


def run_prompt(playwright, prompt_text, headless=True):
    """
    Submit a prompt to ChatGPT and return:
      { "response_text": str, "urls_cited": [{"url", "domain", "title"}] }
    Returns None on failure.
    """
    _ensure_chrome_running()

    browser = playwright.chromium.connect_over_cdp(f"http://localhost:{CDP_PORT}")
    context = browser.contexts[0] if browser.contexts else browser.new_context()
    page = context.new_page()
    page.bring_to_front()

    try:
        print(f"  [ChatGPT] Navigating to chatgpt.com...")
        try:
            page.goto("https://chatgpt.com/", wait_until="domcontentloaded", timeout=15000)
        except Exception:
            pass
        print(f"  [ChatGPT] URL after goto: {page.url}")
        # Fallback: force navigation via JS if goto didn't work
        if "chatgpt.com" not in page.url:
            print(f"  [ChatGPT] goto failed, trying JS navigation...")
            page.evaluate("window.location.href = 'https://chatgpt.com/'")
            page.wait_for_url("*chatgpt.com*", timeout=15000)
        print(f"  [ChatGPT] URL now: {page.url}")
        page.bring_to_front()
        page.wait_for_timeout(3000)
        page.wait_for_timeout(3000)

        if "login" in page.url or "auth/error" in page.url:
            print("  [ChatGPT] Not authenticated — please log in in the browser window.")
            # Give user time to log in manually
            deadline = time.time() + 120
            while time.time() < deadline:
                if "chatgpt.com" in page.url and "login" not in page.url:
                    break
                time.sleep(2)

        textarea = _find_textarea(page)
        if not textarea:
            print("  [ChatGPT] Could not find input textarea.")
            return None

        print(f"  [ChatGPT] Submitting prompt...")
        textarea.click()
        textarea.fill(prompt_text)
        page.wait_for_timeout(500)
        _submit(page)

        print(f"  [ChatGPT] Waiting for response...")
        response_text = _wait_for_response(page)
        if not response_text:
            print("  [ChatGPT] No response received.")
            return None

        urls_cited = _extract_citations(page)
        print(f"  [ChatGPT] Done. {len(response_text)} chars, {len(urls_cited)} citations.")

        # Close this tab when done
        page.close()
        return {"response_text": response_text, "urls_cited": urls_cited}

    except Exception as e:
        print(f"  [ChatGPT] Error: {e}")
        page.close()
        return None


def _find_textarea(page):
    selectors = [
        "#prompt-textarea",
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
        "[data-testid='send-button']",
        "button[aria-label='Send prompt']",
        "button[aria-label='Send message']",
        "button[type='submit']",
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
    stop_selectors = [
        "[data-testid='stop-button']",
        "button[aria-label='Stop streaming']",
        "button[aria-label='Stop generating']",
    ]
    for _ in range(30):
        if any(_is_visible(page, s) for s in stop_selectors):
            break
        time.sleep(1)

    deadline = time.time() + RESPONSE_TIMEOUT
    while time.time() < deadline:
        if not any(_is_visible(page, s) for s in stop_selectors):
            break
        time.sleep(1)

    page.wait_for_timeout(2000)
    return _extract_response_text(page)


def _is_visible(page, selector):
    try:
        return page.locator(selector).first.is_visible(timeout=300)
    except Exception:
        return False


def _extract_response_text(page):
    selectors = [
        "[data-message-author-role='assistant'] .markdown",
        "[data-message-author-role='assistant']",
        ".agent-turn .markdown",
        ".markdown.prose",
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


def _extract_citations(page):
    citations = []
    seen_urls = set()
    source_selectors = [
        "[data-testid='citation'] a",
        ".source-card a",
        "[data-message-author-role='assistant'] a[href^='http']",
        "article a[href^='http']",
        ".prose a[href^='http']",
    ]
    for sel in source_selectors:
        try:
            for link in page.locator(sel).all():
                try:
                    url = link.get_attribute("href")
                    if not url or "openai.com" in url or "chatgpt.com" in url:
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
    """
    Open ChatGPT in Chrome and wait until logged in.
    """
    _ensure_chrome_running()
    browser = playwright.chromium.connect_over_cdp(f"http://localhost:{CDP_PORT}")
    context = browser.contexts[0] if browser.contexts else browser.new_context()
    page = context.new_page()
    page.bring_to_front()
    page.goto("https://chatgpt.com/", wait_until="domcontentloaded")
    page.wait_for_timeout(2000)

    print("\nWaiting for ChatGPT chat interface (up to 120s)...")
    print("Log in if prompted.")

    textarea_selectors = [
        "#prompt-textarea",
        "div[contenteditable='true'][data-placeholder]",
        "div[contenteditable='true']",
    ]

    deadline = time.time() + 120
    while time.time() < deadline:
        for sel in textarea_selectors:
            try:
                if page.locator(sel).first.is_visible(timeout=300):
                    page.close()
                    print("ChatGPT: logged in and ready.")
                    return
            except Exception:
                pass
        time.sleep(1)

    page.close()
    print("ChatGPT: timed out. Try again.")
