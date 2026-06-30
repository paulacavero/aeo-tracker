#!/usr/bin/env python3
"""
Verifies that Playwright can open ChatGPT and Claude using your real Chrome profile.
Run this once to confirm everything works before scheduling daily runs.

Usage:
  python3 setup_auth.py chatgpt
  python3 setup_auth.py claude
  python3 setup_auth.py both
"""

import sys
from playwright.sync_api import sync_playwright
from src.browser_chatgpt import save_auth as save_chatgpt
from src.browser_claude import save_auth as save_claude


def main():
    args = sys.argv[1:]
    target = args[0] if args else "both"

    print("NOTE: This will close Chrome if it's open (needed to use your profile).")
    print()

    with sync_playwright() as playwright:
        if target in ("chatgpt", "both"):
            print("=== Checking ChatGPT ===")
            save_chatgpt(playwright)

        if target in ("claude", "both"):
            print("\n=== Checking Claude ===")
            save_claude(playwright)

    print("\nSetup complete. Run daily scans with: python3 run.py now")


if __name__ == "__main__":
    main()
