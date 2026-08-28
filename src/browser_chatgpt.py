"""
ChatGPT browser automation via Playwright.

Launches a dedicated automation Chrome with remote debugging, then connects to
it via CDP (Chrome DevTools Protocol). This avoids bot detection since Chrome
isn't launched by Playwright — Playwright just remote-controls it.

Chrome 136+ refuses remote debugging on the default profile, so we run a
separate --user-data-dir. That profile runs alongside your normal Chrome and
keeps its own logins.
"""

import os
import time
import shutil
import platform
import subprocess
from pathlib import Path

from . import urls


def _detect_chrome():
    """
    Find a Chrome/Chromium binary across macOS, Windows, and Linux so anyone
    who clones the repo can run the browser path without editing code.
    Override by setting the AEO_CHROME_BIN environment variable to your Chrome
    executable path (useful for non-standard installs or Brave/Edge).
    Returns a path/command string, or None if nothing was found.
    """
    override = os.environ.get("AEO_CHROME_BIN")
    if override:
        return override

    system = platform.system()
    if system == "Darwin":
        candidates = [
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            "/Applications/Chromium.app/Contents/MacOS/Chromium",
            "/Applications/Google Chrome Canary.app/Contents/MacOS/Google Chrome Canary",
        ]
    elif system == "Windows":
        candidates = [
            r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
            os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
        ]
    else:  # Linux and others
        candidates = ["google-chrome", "google-chrome-stable", "chromium", "chromium-browser"]

    for c in candidates:
        if os.path.sep in c or c.lower().endswith(".exe"):
            if Path(c).exists():
                return c
        else:  # bare command name — look it up on PATH
            found = shutil.which(c)
            if found:
                return found
    return None


CHROME_BIN = _detect_chrome()
CDP_PORT = 9222

# How the automation Chrome presents itself. Set with AEO_CHROME_MODE.
#   visible   — a normal window (the original behaviour)
#   offscreen — a real window parked far off the desktop: it still renders and
#               isn't throttled like a background tab, but never covers your work
#   headless  — Chrome's new headless mode. No window at all, but a headless
#               fingerprint is much easier for a site to flag as automation.
# Changing this only takes effect on a fresh Chrome: _ensure_chrome_running
# reuses an already-open debug port and won't relaunch with new flags.
CHROME_MODE = os.environ.get("AEO_CHROME_MODE", "offscreen").lower()

# bring_to_front() on every prompt is what made the scan unusable while it ran
# — 100+ focus grabs a day. Off-screen windows render without it. Set
# AEO_CHROME_FOCUS=1 to restore the old behaviour if a mode needs it.
FOCUS_TABS = os.environ.get("AEO_CHROME_FOCUS", "0") == "1"
# Dedicated automation profile (under auth/, gitignored). Persists logins
# between runs and lets automation Chrome coexist with your normal Chrome.
PROFILE_DIR = Path(__file__).resolve().parent.parent / "auth" / "chrome_profile"
RESPONSE_TIMEOUT = 120


def _kill_automation_chrome():
    """Kill only our automation Chrome, matched on its debug port."""
    subprocess.run(["pkill", "-f", f"remote-debugging-port={CDP_PORT}"],
                   capture_output=True)
    time.sleep(3)


def _ensure_chrome_running(force_fresh=False):
    """
    Launch the dedicated automation Chrome with remote debugging if it isn't
    already up. Uses a separate --user-data-dir so it runs alongside your normal
    Chrome (Chrome 136+ blocks remote debugging on the default profile).

    force_fresh kills any existing instance first. An open debug port is not
    proof of a healthy browser: a Chrome left running for days goes stale and
    every connect_over_cdp then fails with "Browser context management is not
    supported", which cost two full days of ChatGPT collection in Aug 2026
    because the port was open so we kept reusing the broken instance.
    """
    import urllib.request

    if force_fresh:
        print("  Restarting automation Chrome (previous instance unusable)...")
        _kill_automation_chrome()
    else:
        # Check if debugging port is already open
        try:
            urllib.request.urlopen(f"http://localhost:{CDP_PORT}/json", timeout=2)
            print("  Automation Chrome already running with debug port.")
            return
        except Exception:
            pass

    if not CHROME_BIN:
        raise RuntimeError(
            "Could not find a Chrome/Chromium browser. Install Google Chrome, or "
            "set the AEO_CHROME_BIN environment variable to your Chrome executable path."
        )

    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    args = [
        CHROME_BIN,
        f"--remote-debugging-port={CDP_PORT}",
        f"--user-data-dir={PROFILE_DIR}",
        "--no-first-run",
        "--no-default-browser-check",
    ]
    if CHROME_MODE == "offscreen":
        # Far outside any plausible display, so macOS never shows it.
        args += ["--window-position=-32000,-32000", "--window-size=1280,900"]
    elif CHROME_MODE == "headless":
        args += ["--headless=new", "--window-size=1280,900"]

    print(f"  Launching automation Chrome (mode: {CHROME_MODE}, "
          f"separate profile — your normal Chrome is untouched)...")
    subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

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

    # A stale-but-listening Chrome fails here, not at launch. Recover once by
    # forcing a fresh browser rather than failing this prompt and every prompt
    # after it.
    try:
        browser = playwright.chromium.connect_over_cdp(f"http://localhost:{CDP_PORT}")
    except Exception as e:
        print(f"  [ChatGPT] CDP connect failed ({type(e).__name__}), restarting Chrome")
        _ensure_chrome_running(force_fresh=True)
        browser = playwright.chromium.connect_over_cdp(f"http://localhost:{CDP_PORT}")

    context = browser.contexts[0] if browser.contexts else browser.new_context()
    page = context.new_page()
    if FOCUS_TABS:
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
        if FOCUS_TABS:
            page.bring_to_front()
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
    """
    Pull cited URLs out of the rendered answer.

    ChatGPT renders citations as separate pill elements
    (data-testid="webpage-citation-pill"), not as inline markdown links — so
    they never appear in the response text, only in the DOM.

    Every pill href carries ?utm_source=chatgpt.com. The previous version of
    this function dropped any URL containing "chatgpt.com", which matched that
    param on every citation and discarded 100% of them. Filter on host instead.
    """
    citations = []
    seen_urls = set()
    source_selectors = [
        "[data-testid='webpage-citation-pill']",
        "[data-testid*='citation'] a",
        "[data-testid*='citation']",
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
                    if not url or not url.startswith("http"):
                        continue
                    if urls.is_internal(url):
                        continue
                    url = urls.clean_url(url)
                    if url in seen_urls:
                        continue
                    seen_urls.add(url)
                    title = link.inner_text().strip() or ""
                    citations.append({"url": url, "domain": urls.get_domain(url), "title": title})
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
