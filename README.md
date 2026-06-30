# AEO Tracker

> ⚠️ **Work in progress.** This project is actively being built and the internals are still changing — expect rough edges, and parts of this README may lag behind the code. Feedback welcome, but it's not yet stable.

Open-source AEO (Answer Engine Optimization) monitoring tool. Tracks whether your brand appears in ChatGPT and Claude responses across a set of prompts, and measures share of voice, visibility, and citations vs. competitors.

## What it tracks

- **Share of Voice** — your brand mentions / total brand mentions across all competitors
- **Visibility** — % of responses where you appear at least once
- **Citations** — which full URLs (not just domains) the AI uses as sources, so you can study the most-cited articles
- **AI Answers log** — raw daily log per engine per prompt

## Setup

### 1. Install dependencies

```bash
cd aeo-tracker
pip3 install -r requirements.txt
python3 -m playwright install chromium
```

### 2. Authenticate (one-time)

This opens visible browser windows so you can log in manually. Sessions are saved locally.

```bash
python setup_auth.py both      # both ChatGPT and Claude
python setup_auth.py chatgpt   # ChatGPT only
python setup_auth.py claude    # Claude only
```

Sessions are saved to `auth/` and reused on every run. Re-run `setup_auth.py` if a session expires.

### 3. Add your prompts

First copy the example files (your real config is gitignored and stays local):

```bash
cp config/prompts.example.json config/prompts.json
cp config/settings.example.json config/settings.json
```

Then edit `config/prompts.json`. Each entry needs an `id` (unique string) and `text` (the prompt to send).

```json
[
  { "id": "01", "text": "What is the best LLM observability platform?" },
  { "id": "02", "text": "..." }
]
```

### 4. Configure competitors and brand

Edit `config/settings.json` to adjust:
- `brand` — your brand name, keywords, and domain
- `competitors` — list of competitors with their names, keywords, and domains
- `schedule_time` — time to run daily (24h format, e.g. "09:00")
- `headless` — `true` for silent background runs, `false` to watch the browser

## Running

```bash
# Run now (all prompts on all engines)
python run.py now

# Run with visible browser (good for debugging)
python run.py now --visible

# Run for a specific date (won't re-run if already done)
python run.py now --date 2026-04-20

# Schedule to run automatically every day at the time in settings.json
python run.py schedule

# Regenerate the dashboard from existing data (no new scans)
python run.py dashboard
```

## Dashboard

After running, open `dashboard.html` in your browser. It has four tabs:

| Tab | What it shows |
|---|---|
| Share of Voice | Your SoV % and ranking, trend chart vs competitors, brands table |
| Visibility | % of responses mentioning you, trend chart, ranking |
| Citations | Which URLs are being cited, with full URLs so you can study top-cited articles |
| AI Answers | Raw log: every prompt × engine × day, with mentioned brands and citations |

### Hosting on GitHub Pages (optional)

Commit `dashboard.html` to a `gh-pages` branch and enable GitHub Pages in your repo settings. The dashboard is fully self-contained — no server needed.

## Project structure

```
aeo-tracker/
├── config/
│   ├── prompts.json        # your prompts
│   └── settings.json       # brand, competitors, engines, schedule
├── data/
│   └── results.db          # SQLite database (auto-created)
├── auth/
│   ├── chatgpt.json        # saved browser session (auto-created)
│   └── claude.json         # saved browser session (auto-created)
├── src/
│   ├── database.py         # SQLite read/write
│   ├── detector.py         # brand mention + URL extraction
│   ├── browser_chatgpt.py  # Playwright automation for ChatGPT
│   ├── browser_claude.py   # Playwright automation for Claude
│   ├── runner.py           # daily run orchestration
│   └── dashboard.py        # HTML dashboard generator
├── run.py                  # CLI entry point
├── setup_auth.py           # one-time login setup
└── requirements.txt
```

## Notes

- Sessions expire periodically — re-run `setup_auth.py` when they do
- ChatGPT's web search is on by default in the UI, so its responses include real-time citations
- Claude doesn't currently browse the web, so citation tracking for Claude reflects URLs mentioned inline
- The `data/results.db` file is your source of truth — back it up if you care about history
- Adding a new engine later is straightforward: add a `browser_[engine].py` module and register it in `runner.py`
