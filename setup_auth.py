#!/usr/bin/env python3
"""
One-time sign-in for the browser path.

Opens a dedicated automation Chrome (a separate profile — your normal Chrome is
NOT touched or closed) and waits for you to log in. The login persists in that
profile, so daily scans can reuse it.

By default this signs you into ChatGPT, which is the engine the tracker scrapes
via the browser. (Claude is queried through the Anthropic API with web search,
so it does not need a browser login.)

Usage:
  python3 setup_auth.py            # ChatGPT (default)
  python3 setup_auth.py chatgpt    # ChatGPT only
  python3 setup_auth.py claude     # Claude only (optional; not needed for API path)
  python3 setup_auth.py both       # both

Chrome detection is cross-platform (macOS/Windows/Linux). If your Chrome is in a
non-standard location, set the AEO_CHROME_BIN environment variable to its path.
"""

import sys
from playwright.sync_api import sync_playwright
from src.browser_chatgpt import save_auth as save_chatgpt
from src.browser_claude import save_auth as save_claude


def main():
    args = sys.argv[1:]
    target = args[0] if args else "chatgpt"

    print("A separate automation Chrome will open. Log in when it appears.")
    print("(Your normal Chrome is left untouched.)\n")

    with sync_playwright() as playwright:
        if target in ("chatgpt", "both"):
            print("=== ChatGPT ===")
            save_chatgpt(playwright)

        if target in ("claude", "both"):
            print("\n=== Claude (optional — API path doesn't need this) ===")
            save_claude(playwright)

    print("\nSetup complete. Run a scan with: python3 run.py now")


if __name__ == "__main__":
    main()
