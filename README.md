# AEO Tracker

> ⚠️ **Work in progress.** This project is actively being built and the internals are still changing — expect rough edges. The architecture is mid-transition (see [Status & roadmap](#status--roadmap)); this README describes how it works *today*.

Open-source AEO (Answer Engine Optimization) monitoring tool. Tracks whether your brand appears in ChatGPT and Claude answers across a set of prompts, and measures share of voice, visibility, and citations vs. competitors.

## What it tracks

- **Share of Voice** — your brand mentions / total brand mentions across all competitors
- **Visibility** — % of responses where you appear at least once
- **Citations** — which full URLs the AI uses as sources, so you can study the most-cited articles
- **AI Answers log** — raw daily log per engine per prompt

## How it works

Each engine can be queried two ways — the **API** (official OpenAI/Anthropic APIs: reliable, headless) or the **browser** (drives the real ChatGPT/Claude website in a dedicated Chrome profile via the DevTools Protocol: closest to what real users see). You set the method **per engine** in `config/settings.json`:

```json
"engine_methods": { "chatgpt": "browser", "claude": "api" }
```

The default above is the **hybrid**, chosen because each surface has a different sweet spot:
- **ChatGPT → browser** — captures the real logged-in consumer answer. (The OpenAI API is also available; set `"chatgpt": "api"` to use it.)
- **Claude → API with web search** — Claude browses and returns real citations. We use the API here on purpose: automating the claude.ai site violates Anthropic's ToS.

| Command | What it does |
|---------|--------------|
| `python run.py now` | Daily run using the per-engine methods above (the hybrid). |
| `python run.py browser` | Spot-check forcing **every** engine through the browser. Stored in a separate DB + dashboard so it never distorts the daily trend. |

**On citations (important):** Citations only appear when the model actually runs a web search. The OpenAI API and the Anthropic `web_search` tool both return real citations (url/title). **ChatGPT in the *browser* only cites when it chooses to search** — for many "best tool" prompts the free tier answers from memory and returns *no* citations (the brand-mention metrics still work regardless).

## Setup

### 1. Install dependencies

```bash
cd aeo-tracker
pip3 install -r requirements.txt
python3 -m playwright install chromium   # only needed for the browser path
```

### 2. Configure your brand, competitors, and prompts

Copy the example files (your real config is gitignored and stays local):

```bash
cp config/prompts.example.json config/prompts.json
cp config/settings.example.json config/settings.json
```

- `config/settings.json` — your `brand` (name, keywords, domain), `competitors`, the `engines` to run, and `schedule_time`.
- `config/prompts.json` — your prompts. Each entry needs a unique `id` and the `text` to send.

### 3. Add your API keys

Create a `.env` file (also gitignored):

```
OPENAI_API_KEY=sk-...
ANTHROPIC_API_KEY=sk-ant-...
```

These power the **API** path. If you only use the **browser** path you can skip them.

### 4. Sign in for the browser path (one-time, optional)

Only needed if you use `run.py browser`. This opens a **dedicated automation Chrome** (a separate profile — your normal Chrome is *not* touched or closed) and waits for you to log in. The login persists in that profile and is reused on later runs.

```bash
python setup_auth.py            # ChatGPT (default — the engine we scrape)
python setup_auth.py claude     # optional; the API path doesn't need this
```

**Cross-platform Chrome detection:** the browser path auto-detects Google Chrome on macOS, Windows, and Linux. If your Chrome is in a non-standard location (or you want to use Chromium/Brave/Edge), point it explicitly:

```bash
export AEO_CHROME_BIN="/path/to/your/chrome"
```

For unbiased results, sign in with a **neutral account** (not one tied to your brand) and turn **Memory and Custom Instructions off** in ChatGPT settings so answers aren't personalized.

## Running

```bash
python run.py now                    # API scan of all prompts (the daily workhorse)
python run.py now --date 2026-04-20  # backfill a specific date (skips if already done)
python run.py browser                # browser spot-check → separate DB + dashboard
python run.py dashboard              # regenerate dashboard.html from existing data
python run.py schedule               # simple in-process daily scheduler (keeps running)
```

`run.py browser` writes to `data/results_browser.db` and `dashboard_browser.html` so a faithful spot-check never mixes into your daily API trend.

### Scheduling it daily (macOS)

For a hands-off daily run, use the included `launchd` agent instead of leaving a terminal open:

```bash
cp scheduling/com.latitude.aeo-tracker.plist ~/Library/LaunchAgents/
launchctl load -w ~/Library/LaunchAgents/com.latitude.aeo-tracker.plist
```

It runs `run.py now` daily at the time set in the plist (edit `StartCalendarInterval`). To stop it: `launchctl unload ~/Library/LaunchAgents/com.latitude.aeo-tracker.plist`.

> **Note:** with the default hybrid, the daily run drives ChatGPT through the browser, so your Mac must be **awake and logged into your desktop session** at that time (the automation Chrome needs a GUI), and the automation profile must already be signed in (`setup_auth.py`). If you'd rather have a fully headless daily run, set `"chatgpt": "api"` in `engine_methods`.

## Dashboard

After running, open `dashboard.html`. It has four tabs:

| Tab | What it shows |
|---|---|
| Share of Voice | Your SoV % and ranking, trend chart vs competitors, brands table |
| Visibility | % of responses mentioning you, trend chart, ranking |
| Citations | Which URLs are being cited, with full URLs so you can study top-cited articles |
| AI Answers | Raw log: every prompt × engine × day, with mentioned brands and citations |

The dashboard is fully self-contained (no server). You can host it privately, or commit it to a `gh-pages` branch for GitHub Pages — but note it contains your competitor data, so keep it private unless you intend to share that.

## Project structure

```
aeo-tracker/
├── config/
│   ├── prompts.json / prompts.example.json     # your prompts (real one gitignored)
│   └── settings.json / settings.example.json   # brand, competitors, engines, schedule
├── data/
│   ├── results.db            # API-path SQLite database (auto-created, gitignored)
│   └── results_browser.db    # browser-path database (separate)
├── auth/
│   └── chrome_profile/       # dedicated Chrome profile for the browser path (gitignored)
├── src/
│   ├── database.py           # SQLite read/write
│   ├── detector.py           # brand mention + URL extraction
│   ├── api_chatgpt.py        # ChatGPT via OpenAI API (search model → citations)
│   ├── api_claude.py         # Claude via Anthropic API
│   ├── browser_chatgpt.py    # ChatGPT via real website (Chrome + DevTools Protocol)
│   ├── browser_claude.py     # Claude via real website
│   ├── runner.py             # daily run orchestration (api | browser modes)
│   └── dashboard.py          # HTML dashboard generator
├── scheduling/               # launchd agent for daily runs
├── run.py                    # CLI entry point
├── setup_auth.py             # one-time browser sign-in
└── requirements.txt
```

## Status & roadmap

The **hybrid is now the default** — `run.py now` scrapes ChatGPT's consumer site and queries Claude via the API with web search:

- **Claude → Anthropic API with the web-search tool** — ✅ live answers with real citations. (Scraping claude.ai is avoided because it violates Anthropic's ToS.)
- **ChatGPT → browser scraping** — ✅ captures the logged-in consumer answer.

Known limitations:
- **ChatGPT citations are partial** — only captured when ChatGPT actually searches the web (see [How it works](#how-it-works)).
- **Single sample per prompt per day.** LLM answers vary run-to-run, so treat small day-to-day movements as noise and watch the longer trend.

## Notes

- The `data/*.db` files are your source of truth — back them up if you care about history.
- Adding a new engine: add an `api_[engine].py` and/or `browser_[engine].py` module and register it in `runner.py`.
