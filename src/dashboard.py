"""
Generates dashboard.html from the SQLite database.
All data is embedded as JSON; charts use Chart.js (CDN).
"""

import json
from collections import defaultdict
from pathlib import Path

from . import database

OUTPUT_PATH = Path(__file__).parent.parent / "dashboard.html"
CONFIG_DIR  = Path(__file__).parent.parent / "config"


def load_settings():
    with open(CONFIG_DIR / "settings.json") as f:
        return json.load(f)


def build_data(responses, settings):
    """
    Pre-compute all metrics needed for the dashboard from raw response rows.
    Returns a dict that will be embedded as JSON in the HTML.
    """
    brand_name  = settings["brand"]["name"]
    brand_domain = settings["brand"]["domain"]
    competitor_names = [c["name"] for c in settings["competitors"]]
    all_brand_names  = [brand_name] + competitor_names

    # ------------------------------------------------------------------ #
    # Per-day aggregations
    # ------------------------------------------------------------------ #
    days = sorted(set(r["date"] for r in responses))
    engines = sorted(set(r["engine"] for r in responses))
    prompts = sorted(set((r["prompt_id"], r["prompt_text"]) for r in responses),
                     key=lambda x: x[0])

    # sov_by_day[date][brand] = mention_count
    sov_by_day = defaultdict(lambda: defaultdict(int))
    # vis_by_day[date][brand] = number of responses that mentioned brand
    vis_by_day = defaultdict(lambda: defaultdict(int))
    # total responses per day (for visibility denominator)
    total_by_day = defaultdict(int)
    # total brand mentions per day (for SoV denominator)
    brand_mentions_by_day = defaultdict(int)

    # For citations tab
    citation_counts = defaultdict(lambda: {"url": "", "domain": "", "title": "", "count": 0, "own": False})
    total_citations_overall = 0

    # For AI answers tab (raw log, no response_text)
    answers_log = []

    for r in responses:
        d = r["date"]
        total_by_day[d] += 1

        brands = r["brands_mentioned"]
        urls   = r["urls_cited"]

        # SoV & visibility
        for b in brands:
            if b in all_brand_names:
                sov_by_day[d][b] += 1
                brand_mentions_by_day[d] += 1
        for b in set(brands) & set(all_brand_names):
            vis_by_day[d][b] += 1

        # Citations
        for u in urls:
            url = u.get("url", "")
            domain = u.get("domain", "")
            title = u.get("title", "") or url
            if url:
                citation_counts[url]["url"] = url
                citation_counts[url]["domain"] = domain
                citation_counts[url]["title"] = title or url
                citation_counts[url]["count"] += 1
                citation_counts[url]["own"] = brand_domain in domain or brand_domain in url
                total_citations_overall += 1

        # Answers log
        answers_log.append({
            "date":     r["date"],
            "engine":   r["engine"],
            "prompt_id":   r["prompt_id"],
            "prompt_text": r["prompt_text"],
            "latitude_mentioned": bool(r["latitude_mentioned"]),
            "latitude_cited":     bool(r["latitude_cited"]),
            "brands_mentioned":   brands,
            "urls_cited": [{"url": u.get("url",""), "domain": u.get("domain",""), "title": u.get("title","")} for u in urls],
        })

    # ------------------------------------------------------------------ #
    # Build chart series: SoV % per day per brand
    # ------------------------------------------------------------------ #
    sov_series = {}
    for b in all_brand_names:
        sov_series[b] = []
        for d in days:
            total = brand_mentions_by_day.get(d, 0)
            count = sov_by_day[d].get(b, 0)
            pct = round(count / total * 100, 2) if total > 0 else 0
            sov_series[b].append(pct)

    # Visibility % per day per brand
    vis_series = {}
    for b in all_brand_names:
        vis_series[b] = []
        for d in days:
            total = total_by_day.get(d, 0)
            count = vis_by_day[d].get(b, 0)
            pct = round(count / total * 100, 2) if total > 0 else 0
            vis_series[b].append(pct)

    # ------------------------------------------------------------------ #
    # Summary cards (all-time)
    # ------------------------------------------------------------------ #
    total_responses = len(responses)
    lat_mentions = sum(1 for r in responses if r["latitude_mentioned"])
    lat_cited_count = sum(1 for r in responses if r["latitude_cited"])

    total_brand_mentions_all = sum(brand_mentions_by_day.values())
    lat_brand_mentions_all   = sum(sov_by_day[d].get(brand_name, 0) for d in days)
    sov_overall = round(lat_brand_mentions_all / total_brand_mentions_all * 100, 2) if total_brand_mentions_all > 0 else 0
    vis_overall = round(lat_mentions / total_responses * 100, 2) if total_responses > 0 else 0

    # Brand rankings (by SoV)
    brand_totals = {b: sum(sov_by_day[d].get(b, 0) for d in days) for b in all_brand_names}
    total_all = sum(brand_totals.values())
    brands_table = sorted(
        [{"name": b, "mentions": brand_totals[b],
          "sov_pct": round(brand_totals[b] / total_all * 100, 2) if total_all else 0}
         for b in all_brand_names],
        key=lambda x: -x["mentions"]
    )
    lat_rank = next((i+1 for i, b in enumerate(brands_table) if b["name"] == brand_name), None)

    # Visibility rankings
    vis_totals = {b: sum(vis_by_day[d].get(b, 0) for d in days) for b in all_brand_names}
    vis_table = sorted(
        [{"name": b, "responses_with_mention": vis_totals[b],
          "vis_pct": round(vis_totals[b] / total_responses * 100, 2) if total_responses else 0}
         for b in all_brand_names],
        key=lambda x: -x["vis_pct"]
    )
    lat_vis_rank = next((i+1 for i, b in enumerate(vis_table) if b["name"] == brand_name), None)

    # Citations table (sorted by count desc)
    citations_table = sorted(citation_counts.values(), key=lambda x: -x["count"])
    lat_cite_count = sum(1 for r in responses if r["latitude_cited"])
    lat_domain_sov = round(lat_cite_count / len(responses) * 100, 2) if responses else 0

    # ------------------------------------------------------------------ #
    # Per-prompt breakdown
    # ------------------------------------------------------------------ #
    prompt_rows = {}
    for r in responses:
        pid = r["prompt_id"]
        if pid not in prompt_rows:
            prompt_rows[pid] = {
                "id": pid,
                "text": r["prompt_text"],
                "topic": r.get("prompt_topic", ""),
                "total": 0,
                "lat_mentioned": 0,
                "lat_cited": 0,
                "brand_counts": defaultdict(int),
            }
        pr = prompt_rows[pid]
        pr["total"] += 1
        if r["latitude_mentioned"]:
            pr["lat_mentioned"] += 1
        if r["latitude_cited"]:
            pr["lat_cited"] += 1
        for b in r["brands_mentioned"]:
            if b in all_brand_names:
                pr["brand_counts"][b] += 1

    # Load topic from prompts config if available
    try:
        with open(CONFIG_DIR / "prompts.json") as f:
            import json as _json
            prompts_config = {str(p["id"]): p for p in _json.load(f)}
    except Exception:
        prompts_config = {}

    prompts_table = []
    for pid, pr in sorted(prompt_rows.items()):
        total = pr["total"]
        vis_pct = round(pr["lat_mentioned"] / total * 100, 1) if total else 0
        top_brands = sorted(pr["brand_counts"].items(), key=lambda x: -x[1])[:5]
        topic = prompts_config.get(pid, {}).get("topic", "")
        prompts_table.append({
            "id":           pid,
            "text":         pr["text"],
            "topic":        topic,
            "total":        total,
            "lat_mentioned": pr["lat_mentioned"],
            "lat_cited":    pr["lat_cited"],
            "vis_pct":      vis_pct,
            "top_brands":   [{"name": b, "count": c} for b, c in top_brands],
        })

    return {
        "brand_name":  brand_name,
        "brand_domain": brand_domain,
        "days":        days,
        "engines":     engines,
        "prompts":     [{"id": p[0], "text": p[1]} for p in prompts],
        "summary": {
            "sov_pct":       sov_overall,
            "vis_pct":       vis_overall,
            "lat_rank":      lat_rank,
            "lat_vis_rank":  lat_vis_rank,
            "lat_mentions":  lat_mentions,
            "lat_cited":     lat_cited_count,
            "total_responses": total_responses,
            "lat_domain_sov": lat_domain_sov,
        },
        "sov_series":       sov_series,
        "vis_series":       vis_series,
        "brands_table":     brands_table,
        "vis_table":        vis_table,
        "citations_table":  citations_table,
        "answers_log":      sorted(answers_log, key=lambda x: x["date"], reverse=True),
        "all_brand_names":  all_brand_names,
        "prompts_table":    prompts_table,
    }


HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>AEO Tracker — {brand_name}</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
<style>
  *, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{
    font-family: "Inter", -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    background: #f8f9fb; color: #111827; font-size: 14px;
    display: flex; flex-direction: column; height: 100vh; overflow: hidden;
  }}

  /* ---- Top bar ---- */
  .topbar {{
    background: #fff; border-bottom: 1px solid #e5e7eb;
    padding: 0 20px; height: 52px; display: flex; align-items: center;
    flex-shrink: 0; gap: 12px; z-index: 10;
  }}
  .topbar-brand {{ font-size: 15px; font-weight: 700; color: #111; letter-spacing: -.3px; }}
  .topbar-sep {{ color: #d1d5db; }}
  .topbar-sub {{ font-size: 12px; color: #9ca3af; }}
  .topbar-date {{ margin-left: auto; font-size: 12px; color: #6b7280; }}

  /* ---- Shell ---- */
  .shell {{ display: flex; flex: 1; overflow: hidden; }}

  /* ---- Sidebar ---- */
  .sidebar {{
    width: 320px; min-width: 280px; max-width: 360px;
    background: #fff; border-right: 1px solid #e5e7eb;
    display: flex; flex-direction: column; overflow: hidden; flex-shrink: 0;
  }}
  .sidebar-head {{
    padding: 14px 16px 10px; border-bottom: 1px solid #f3f4f6; flex-shrink: 0;
  }}
  .sidebar-search {{
    width: 100%; border: 1px solid #e5e7eb; border-radius: 7px;
    padding: 7px 10px; font-size: 13px; background: #f9fafb; outline: none;
    margin-bottom: 8px;
  }}
  .sidebar-search:focus {{ border-color: #f59e0b; background: #fff; }}
  .filter-pills {{ display: flex; gap: 5px; flex-wrap: wrap; margin-bottom: 5px; }}
  .pill {{
    font-size: 11px; font-weight: 500; padding: 3px 9px; border-radius: 99px;
    border: 1px solid #e5e7eb; background: #f9fafb; color: #6b7280;
    cursor: pointer; transition: all .12s;
  }}
  .pill.active {{ background: #111; color: #fff; border-color: #111; }}
  .pill:hover:not(.active) {{ border-color: #9ca3af; color: #374151; }}
  .engine-pills {{ display: flex; gap: 5px; }}

  /* ---- Prompt list ---- */
  .prompt-list {{ flex: 1; overflow-y: auto; padding: 6px 0; }}
  .prompt-item {{
    padding: 10px 16px; cursor: pointer; border-left: 3px solid transparent;
    transition: background .1s;
  }}
  .prompt-item:hover {{ background: #f9fafb; }}
  .prompt-item.active {{ background: #fef9ec; border-left-color: #f59e0b; }}
  .prompt-item-row {{ display: flex; align-items: flex-start; gap: 8px; }}
  .prompt-text {{
    flex: 1; font-size: 12.5px; color: #374151; line-height: 1.45;
    display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical;
    overflow: hidden;
  }}
  .prompt-item.active .prompt-text {{ color: #111; font-weight: 500; }}
  .prompt-sov {{
    font-size: 12px; font-weight: 600; white-space: nowrap; padding-top: 1px;
  }}
  .prompt-sov.green {{ color: #16a34a; }}
  .prompt-sov.gray  {{ color: #9ca3af; }}
  .prompt-meta {{ margin-top: 3px; }}
  .topic-tag {{
    font-size: 10px; font-weight: 500; color: #6b7280;
    background: #f3f4f6; border-radius: 3px; padding: 1px 5px; display: inline-block;
  }}
  .sidebar-empty {{ text-align: center; padding: 32px 16px; color: #9ca3af; font-size: 13px; }}

  /* ---- Right panel ---- */
  .panel {{
    flex: 1; display: flex; flex-direction: column; overflow: hidden;
    background: #f8f9fb;
  }}

  /* ---- Panel tabs ---- */
  .panel-tabs {{
    background: #fff; border-bottom: 1px solid #e5e7eb;
    padding: 0 24px; display: flex; gap: 0; flex-shrink: 0;
  }}
  .tab-btn {{
    padding: 14px 16px; font-size: 13px; font-weight: 500; cursor: pointer;
    border: none; background: none; color: #6b7280;
    border-bottom: 2px solid transparent; transition: all .15s; white-space: nowrap;
  }}
  .tab-btn.active {{ color: #111; border-bottom-color: #f59e0b; }}
  .tab-btn:hover:not(.active) {{ color: #374151; }}

  /* ---- Panel engine filter (shown above tabs) ---- */
  .panel-engine-bar {{
    background: #fff; border-bottom: 1px solid #f3f4f6;
    padding: 8px 24px; display: flex; align-items: center; gap: 8px; flex-shrink: 0;
  }}
  .panel-engine-bar label {{ font-size: 12px; color: #6b7280; }}
  .panel-engine-bar select {{
    border: 1px solid #e5e7eb; border-radius: 6px; padding: 4px 8px;
    font-size: 12px; background: #fff; cursor: pointer; outline: none;
  }}

  /* ---- Panel content ---- */
  .panel-content {{ flex: 1; overflow-y: auto; padding: 24px; }}
  .tab-pane {{ display: none; }}
  .tab-pane.active {{ display: block; }}

  /* ---- Cards ---- */
  .cards {{ display: flex; gap: 14px; margin-bottom: 22px; flex-wrap: wrap; }}
  .card {{
    background: #fff; border: 1px solid #e5e7eb; border-radius: 10px;
    padding: 16px 20px; min-width: 140px; flex: 1;
    box-shadow: 0 1px 3px rgba(0,0,0,.04);
  }}
  .card-label {{ font-size: 11px; font-weight: 600; color: #6b7280; text-transform: uppercase; letter-spacing: .05em; margin-bottom: 6px; }}
  .card-value {{ font-size: 28px; font-weight: 700; color: #111; line-height: 1; }}
  .card-sub {{ font-size: 12px; color: #9ca3af; margin-top: 4px; }}

  /* ---- Chart box ---- */
  .chart-box {{
    background: #fff; border: 1px solid #e5e7eb; border-radius: 10px;
    padding: 18px 20px; margin-bottom: 20px;
    box-shadow: 0 1px 3px rgba(0,0,0,.04);
  }}
  .chart-box h2 {{ font-size: 13px; font-weight: 600; color: #374151; margin-bottom: 14px; }}
  .chart-wrap {{ position: relative; height: 240px; }}

  /* ---- Table box ---- */
  .table-box {{
    background: #fff; border: 1px solid #e5e7eb; border-radius: 10px;
    overflow: hidden; margin-bottom: 20px;
    box-shadow: 0 1px 3px rgba(0,0,0,.04);
  }}
  .table-box table {{ width: 100%; border-collapse: collapse; }}
  .table-box th {{
    background: #f9fafb; font-size: 11px; font-weight: 600;
    color: #6b7280; text-transform: uppercase; letter-spacing: .05em;
    padding: 9px 16px; text-align: left; border-bottom: 1px solid #e5e7eb;
  }}
  .table-box td {{
    padding: 9px 16px; border-bottom: 1px solid #f3f4f6;
    font-size: 13px; vertical-align: middle;
  }}
  .table-box tr:last-child td {{ border-bottom: none; }}
  .table-box tr.highlight td {{ background: #fef9ec; font-weight: 600; }}
  .table-box tr:hover td {{ background: #f9fafb; }}
  .table-box tr.highlight:hover td {{ background: #fef3c7; }}

  /* ---- Badges ---- */
  .badge {{ display: inline-block; padding: 2px 8px; border-radius: 99px; font-size: 11px; font-weight: 500; }}
  .badge-yes   {{ background: #dcfce7; color: #15803d; }}
  .badge-no    {{ background: #fee2e2; color: #b91c1c; }}
  .badge-own   {{ background: #ede9fe; color: #6d28d9; }}
  .badge-third {{ background: #f1f5f9; color: #475569; }}

  /* ---- Brand tags ---- */
  .brand-tag {{
    display: inline-block; background: #f1f5f9; color: #374151;
    border-radius: 4px; padding: 1px 6px; font-size: 11px; margin: 1px;
  }}
  .brand-tag.you {{ background: #fef3c7; color: #92400e; }}

  /* ---- URL cell ---- */
  .url-cell {{ max-width: 340px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }}
  .url-cell a {{ color: #2563eb; text-decoration: none; }}
  .url-cell a:hover {{ text-decoration: underline; }}

  /* ---- Bar ---- */
  .bar-bg {{ background: #f1f5f9; border-radius: 3px; height: 5px; width: 100%; margin-top: 5px; }}
  .bar-fill {{ border-radius: 3px; height: 5px; }}

  /* ---- Empty & misc ---- */
  .empty {{ text-align: center; padding: 48px; color: #9ca3af; font-size: 13px; }}
  .engine-name {{ font-size: 12px; color: #374151; font-weight: 500; }}

  /* ---- Overview panel ---- */
  .overview-section {{ margin-bottom: 24px; }}
  .overview-section h2 {{ font-size: 15px; font-weight: 700; color: #111; margin-bottom: 14px; }}
  .overview-table-prompt {{ max-width: 400px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }}
</style>
</head>
<body>

<!-- Top bar -->
<div class="topbar">
  <span class="topbar-brand">{brand_name} AEO Tracker</span>
  <span class="topbar-sep">·</span>
  <span class="topbar-sub">AI Engine Optimization</span>
  <span class="topbar-date" id="date-range"></span>
</div>

<!-- Main shell -->
<div class="shell">

  <!-- LEFT SIDEBAR -->
  <aside class="sidebar">
    <div class="sidebar-head">
      <input class="sidebar-search" type="text" id="sidebarSearch" placeholder="Search prompts…" oninput="renderSidebar()">
      <div class="filter-pills" id="topicPills">
        <button class="pill active" data-topic="all" onclick="setTopicFilter('all', this)">All</button>
        <button class="pill" data-topic="BOFU" onclick="setTopicFilter('BOFU', this)">BOFU</button>
        <button class="pill" data-topic="Branded" onclick="setTopicFilter('Branded', this)">Branded</button>
        <button class="pill" data-topic="Competitor" onclick="setTopicFilter('Competitor', this)">Competitor</button>
      </div>
      <div class="engine-pills">
        <button class="pill active" data-eng="all" onclick="setSidebarEngine('all', this)">All engines</button>
        <button class="pill" data-eng="chatgpt" onclick="setSidebarEngine('chatgpt', this)">ChatGPT</button>
        <button class="pill" data-eng="claude" onclick="setSidebarEngine('claude', this)">Claude</button>
      </div>
    </div>
    <div class="prompt-list" id="promptList"></div>
  </aside>

  <!-- RIGHT PANEL -->
  <main class="panel">
    <!-- Engine filter for right panel -->
    <div class="panel-engine-bar" id="panelEngineBar" style="display:none">
      <label>Engine:</label>
      <select id="panelEngine" onchange="renderActiveTab()">
        <option value="all">All</option>
        <option value="chatgpt">ChatGPT</option>
        <option value="claude">Claude</option>
      </select>
    </div>

    <!-- Tabs -->
    <nav class="panel-tabs" id="panelTabs" style="display:none">
      <button class="tab-btn active" onclick="switchTab('sov', this)">Share of Voice</button>
      <button class="tab-btn" onclick="switchTab('visibility', this)">Visibility</button>
      <button class="tab-btn" onclick="switchTab('citations', this)">Citations</button>
      <button class="tab-btn" onclick="switchTab('answers', this)">AI Answers</button>
    </nav>

    <!-- Scrollable area -->
    <div class="panel-content">

      <!-- OVERVIEW (no prompt selected) -->
      <div class="tab-pane active" id="pane-overview">
        <div class="overview-section">
          <h2>Overview</h2>
          <div class="cards">
            <div class="card">
              <div class="card-label">Total Prompts</div>
              <div class="card-value" id="ov-prompts">–</div>
            </div>
            <div class="card">
              <div class="card-label">Overall SoV</div>
              <div class="card-value" id="ov-sov">–</div>
              <div class="card-sub">share of voice</div>
            </div>
            <div class="card">
              <div class="card-label">Overall Visibility</div>
              <div class="card-value" id="ov-vis">–</div>
              <div class="card-sub">% responses mention you</div>
            </div>
            <div class="card">
              <div class="card-label">Total Responses</div>
              <div class="card-value" id="ov-total">–</div>
            </div>
          </div>
        </div>
        <div class="overview-section">
          <h2>All Prompts</h2>
          <div class="table-box" id="ov-table-wrap"></div>
        </div>
      </div>

      <!-- SOV TAB -->
      <div class="tab-pane" id="pane-sov">
        <div class="cards">
          <div class="card">
            <div class="card-label">Share of Voice</div>
            <div class="card-value" id="sov-pct">–</div>
            <div class="card-sub" id="sov-rank-sub"></div>
          </div>
          <div class="card">
            <div class="card-label">Brand Ranking</div>
            <div class="card-value" id="sov-rank-card">–</div>
            <div class="card-sub">by share of voice</div>
          </div>
          <div class="card">
            <div class="card-label">Mentions</div>
            <div class="card-value" id="sov-mentions">–</div>
            <div class="card-sub">responses where you appear</div>
          </div>
        </div>
        <div class="chart-box">
          <h2>Share of Voice over time</h2>
          <div class="chart-wrap"><canvas id="chartSov"></canvas></div>
        </div>
        <div class="table-box" id="sov-table-wrap"></div>
      </div>

      <!-- VISIBILITY TAB -->
      <div class="tab-pane" id="pane-visibility">
        <div class="cards">
          <div class="card">
            <div class="card-label">Visibility</div>
            <div class="card-value" id="vis-pct">–</div>
            <div class="card-sub">% of responses mentioning you</div>
          </div>
          <div class="card">
            <div class="card-label">Brand Ranking</div>
            <div class="card-value" id="vis-rank-card">–</div>
            <div class="card-sub">by visibility</div>
          </div>
        </div>
        <div class="chart-box">
          <h2>Visibility over time</h2>
          <div class="chart-wrap"><canvas id="chartVis"></canvas></div>
        </div>
        <div class="table-box" id="vis-table-wrap"></div>
      </div>

      <!-- CITATIONS TAB -->
      <div class="tab-pane" id="pane-citations">
        <div class="cards">
          <div class="card">
            <div class="card-label">Times Cited</div>
            <div class="card-value" id="cit-count">–</div>
            <div class="card-sub">responses citing your domain</div>
          </div>
          <div class="card">
            <div class="card-label">Citation Rate</div>
            <div class="card-value" id="cit-sov">–</div>
            <div class="card-sub">% of responses citing you</div>
          </div>
        </div>
        <div class="table-box" id="cit-table-wrap"></div>
      </div>

      <!-- AI ANSWERS TAB -->
      <div class="tab-pane" id="pane-answers">
        <div class="table-box" id="answers-table-wrap"></div>
      </div>

    </div><!-- /panel-content -->
  </main>
</div><!-- /shell -->

<script>
const RAW = __DATA__;

// ------------------------------------------------------------------ //
// Constants & helpers
// ------------------------------------------------------------------ //
const COLORS = [
  "#f59e0b","#3b82f6","#10b981","#ef4444","#8b5cf6",
  "#ec4899","#06b6d4","#84cc16","#f97316","#6366f1","#14b8a6","#a855f7"
];

function brandColor(name) {{
  const idx = RAW.all_brand_names.indexOf(name);
  return COLORS[idx % COLORS.length];
}}

function fmtPct(v) {{ return (v || 0).toFixed(1) + "%"; }}

function rankLabel(n) {{
  if (!n) return "–";
  const s = ["th","st","nd","rd"];
  const v = n % 100;
  return "#" + n + (s[(v - 20) % 10] || s[v] || s[0]);
}}

function esc(str) {{
  return String(str || "")
    .replace(/&/g,"&amp;").replace(/</g,"&lt;")
    .replace(/>/g,"&gt;").replace(/"/g,"&quot;");
}}

// ------------------------------------------------------------------ //
// State
// ------------------------------------------------------------------ //
let selectedPromptId = null;   // null = overview
let sidebarTopic = "all";
let sidebarEngine = "all";
let activeTab = "overview";
let chartSovInst, chartVisInst;

// ------------------------------------------------------------------ //
// Sidebar filter controls
// ------------------------------------------------------------------ //
function setTopicFilter(val, btn) {{
  sidebarTopic = val;
  document.querySelectorAll("#topicPills .pill").forEach(p => p.classList.remove("active"));
  btn.classList.add("active");
  renderSidebar();
}}

function setSidebarEngine(val, btn) {{
  sidebarEngine = val;
  document.querySelectorAll(".engine-pills .pill").forEach(p => p.classList.remove("active"));
  btn.classList.add("active");
  renderSidebar();
  if (selectedPromptId !== null) renderActiveTab();
}}

// ------------------------------------------------------------------ //
// Sidebar rendering
// ------------------------------------------------------------------ //
function computePromptSov(promptId, engine) {{
  const rows = RAW.answers_log.filter(r =>
    r.prompt_id === promptId &&
    (engine === "all" || r.engine === engine)
  );
  if (!rows.length) return 0;
  let total = 0, lat = 0;
  for (const r of rows) {{
    for (const b of r.brands_mentioned) {{
      if (RAW.all_brand_names.includes(b)) total++;
      if (b === RAW.brand_name) lat++;
    }}
  }}
  return total > 0 ? +(lat / total * 100).toFixed(1) : 0;
}}

function renderSidebar() {{
  const search = (document.getElementById("sidebarSearch").value || "").toLowerCase();
  const list = document.getElementById("promptList");

  const filtered = RAW.prompts_table.filter(p => {{
    if (sidebarTopic !== "all" && !p.topic.includes(sidebarTopic)) return false;
    if (search && !p.text.toLowerCase().includes(search) && !(p.topic||"").toLowerCase().includes(search)) return false;
    return true;
  }});

  if (!filtered.length) {{
    list.innerHTML = '<div class="sidebar-empty">No prompts match</div>';
    return;
  }}

  list.innerHTML = filtered.map(p => {{
    const sov = computePromptSov(p.id, sidebarEngine);
    const isActive = p.id === selectedPromptId;
    const sovClass = sov > 0 ? "green" : "gray";
    const topicHtml = p.topic ? `<span class="topic-tag">${{esc(p.topic)}}</span>` : "";
    return `<div class="prompt-item ${{isActive ? 'active' : ''}}" onclick="selectPrompt('${{esc(p.id)}}')">
      <div class="prompt-item-row">
        <div class="prompt-text" title="${{esc(p.text)}}">${{esc(p.text)}}</div>
        <div class="prompt-sov ${{sovClass}}">${{sov > 0 ? sov + "%" : "–"}}</div>
      </div>
      <div class="prompt-meta">${{topicHtml}}</div>
    </div>`;
  }}).join("");
}}

// ------------------------------------------------------------------ //
// Prompt selection
// ------------------------------------------------------------------ //
function selectPrompt(id) {{
  selectedPromptId = id;
  renderSidebar();
  document.getElementById("panelTabs").style.display = "flex";
  document.getElementById("panelEngineBar").style.display = "flex";
  // Switch to SoV tab on first selection
  if (activeTab === "overview") {{
    switchTab("sov", document.querySelector(".tab-btn"));
  }} else {{
    renderActiveTab();
  }}
}}

function filteredLog() {{
  return RAW.answers_log.filter(r =>
    (selectedPromptId === null || r.prompt_id === selectedPromptId) &&
    (sidebarEngine === "all" || r.engine === sidebarEngine) &&
    (selectedPromptId !== null ? true :
      (sidebarTopic === "all" || (() => {{
        const pt = RAW.prompts_table.find(p => p.id === r.prompt_id);
        return pt && (pt.topic || "").includes(sidebarTopic);
      }})()))
  );
}}

// ------------------------------------------------------------------ //
// Tab switching
// ------------------------------------------------------------------ //
function switchTab(id, btn) {{
  activeTab = id;
  document.querySelectorAll(".tab-pane").forEach(p => p.classList.remove("active"));
  document.querySelectorAll(".tab-btn").forEach(b => b.classList.remove("active"));
  document.getElementById("pane-" + id).classList.add("active");
  if (btn) btn.classList.add("active");
  renderActiveTab();
}}

function renderActiveTab() {{
  if (activeTab === "sov")        renderSov();
  else if (activeTab === "visibility") renderVisibility();
  else if (activeTab === "citations")  renderCitations();
  else if (activeTab === "answers")    renderAnswers();
  else if (activeTab === "overview")   renderOverview();
}}

// ------------------------------------------------------------------ //
// Chart helper
// ------------------------------------------------------------------ //
function buildDayMetrics(rows) {{
  const brands = RAW.all_brand_names;
  const days = [...new Set(rows.map(r => r.date))].sort();
  const sovByDay = {{}}, visByDay = {{}}, totalByDay = {{}}, brandMentionsByDay = {{}};
  for (const r of rows) {{
    const d = r.date;
    totalByDay[d] = (totalByDay[d] || 0) + 1;
    const seen = new Set();
    for (const b of r.brands_mentioned) {{
      if (!brands.includes(b)) continue;
      sovByDay[d] = sovByDay[d] || {{}};
      sovByDay[d][b] = (sovByDay[d][b] || 0) + 1;
      brandMentionsByDay[d] = (brandMentionsByDay[d] || 0) + 1;
      if (!seen.has(b)) {{
        visByDay[d] = visByDay[d] || {{}};
        visByDay[d][b] = (visByDay[d][b] || 0) + 1;
        seen.add(b);
      }}
    }}
  }}
  return {{ days, sovByDay, visByDay, totalByDay, brandMentionsByDay }};
}}

function lineChartOptions() {{
  return {{
    responsive: true, maintainAspectRatio: false,
    plugins: {{ legend: {{ position: "bottom", labels: {{ boxWidth: 12, font: {{ size: 11 }} }} }} }},
    scales: {{
      y: {{ ticks: {{ callback: v => v + "%" }}, grid: {{ color: "#f3f4f6" }} }},
      x: {{ grid: {{ display: false }}, ticks: {{ font: {{ size: 11 }} }} }}
    }}
  }};
}}

// ------------------------------------------------------------------ //
// SOV tab
// ------------------------------------------------------------------ //
function renderSov() {{
  const rows = filteredLog();
  const brands = RAW.all_brand_names;
  const {{ days, sovByDay, brandMentionsByDay }} = buildDayMetrics(rows);

  let totalBM = 0, latBM = 0;
  const brandCounts = {{}};
  for (const r of rows) {{
    for (const b of r.brands_mentioned) {{
      if (!brands.includes(b)) continue;
      totalBM++;
      brandCounts[b] = (brandCounts[b] || 0) + 1;
      if (b === RAW.brand_name) latBM++;
    }}
  }}
  const sovPct = totalBM > 0 ? latBM / totalBM * 100 : 0;
  const latMentions = rows.filter(r => r.latitude_mentioned).length;
  const ranked = brands.slice().sort((a, b) => (brandCounts[b] || 0) - (brandCounts[a] || 0));
  const latRank = ranked.indexOf(RAW.brand_name) + 1;

  document.getElementById("sov-pct").textContent = fmtPct(sovPct);
  document.getElementById("sov-rank-sub").textContent = rankLabel(latRank) + " of " + brands.length + " brands";
  document.getElementById("sov-rank-card").textContent = rankLabel(latRank);
  document.getElementById("sov-mentions").textContent = latMentions;

  const topBrands = ranked.slice(0, 6);
  if (!topBrands.includes(RAW.brand_name)) topBrands[5] = RAW.brand_name;

  const datasets = topBrands.map(b => ({{
    label: b,
    data: days.map(d => {{
      const tot = brandMentionsByDay[d] || 0;
      const cnt = (sovByDay[d] || {{}})[b] || 0;
      return tot > 0 ? +(cnt / tot * 100).toFixed(2) : 0;
    }}),
    borderColor: brandColor(b),
    backgroundColor: brandColor(b) + "22",
    borderWidth: b === RAW.brand_name ? 3 : 1.5,
    pointRadius: 3, tension: 0.3,
  }}));

  if (chartSovInst) chartSovInst.destroy();
  chartSovInst = new Chart(document.getElementById("chartSov"), {{
    type: "line",
    data: {{ labels: days, datasets }},
    options: lineChartOptions(),
  }});

  const tableData = brands.map(b => ({{
    name: b, mentions: brandCounts[b] || 0,
    sov: totalBM > 0 ? +((brandCounts[b] || 0) / totalBM * 100).toFixed(2) : 0,
  }})).sort((a, b) => b.mentions - a.mentions);

  document.getElementById("sov-table-wrap").innerHTML = buildBrandsTable(tableData, "sov");
}}

// ------------------------------------------------------------------ //
// Visibility tab
// ------------------------------------------------------------------ //
function renderVisibility() {{
  const rows = filteredLog();
  const brands = RAW.all_brand_names;
  const {{ days, visByDay, totalByDay }} = buildDayMetrics(rows);
  const total = rows.length;

  const visCounts = {{}};
  for (const r of rows) {{
    for (const b of [...new Set(r.brands_mentioned)]) {{
      if (brands.includes(b)) visCounts[b] = (visCounts[b] || 0) + 1;
    }}
  }}
  const ranked = brands.slice().sort((a, b) => (visCounts[b] || 0) - (visCounts[a] || 0));
  const latRank = ranked.indexOf(RAW.brand_name) + 1;
  const latVisPct = total > 0 ? (visCounts[RAW.brand_name] || 0) / total * 100 : 0;

  document.getElementById("vis-pct").textContent = fmtPct(latVisPct);
  document.getElementById("vis-rank-card").textContent = rankLabel(latRank);

  const topBrands = ranked.slice(0, 6);
  if (!topBrands.includes(RAW.brand_name)) topBrands[5] = RAW.brand_name;

  const datasets = topBrands.map(b => ({{
    label: b,
    data: days.map(d => {{
      const tot = totalByDay[d] || 0;
      const cnt = (visByDay[d] || {{}})[b] || 0;
      return tot > 0 ? +(cnt / tot * 100).toFixed(2) : 0;
    }}),
    borderColor: brandColor(b),
    backgroundColor: brandColor(b) + "22",
    borderWidth: b === RAW.brand_name ? 3 : 1.5,
    pointRadius: 3, tension: 0.3,
  }}));

  if (chartVisInst) chartVisInst.destroy();
  chartVisInst = new Chart(document.getElementById("chartVis"), {{
    type: "line",
    data: {{ labels: days, datasets }},
    options: lineChartOptions(),
  }});

  const tableData = brands.map(b => ({{
    name: b, responses: visCounts[b] || 0,
    vis: total > 0 ? +((visCounts[b] || 0) / total * 100).toFixed(2) : 0,
  }})).sort((a, b) => b.vis - a.vis);

  document.getElementById("vis-table-wrap").innerHTML = buildBrandsTable(tableData, "vis");
}}

// ------------------------------------------------------------------ //
// Citations tab
// ------------------------------------------------------------------ //
function renderCitations() {{
  const rows = filteredLog();
  const latCited = rows.filter(r => r.latitude_cited).length;
  const total = rows.length;
  const rate = total > 0 ? latCited / total * 100 : 0;

  document.getElementById("cit-count").textContent = latCited;
  document.getElementById("cit-sov").textContent = fmtPct(rate);

  const citMap = {{}};
  for (const r of rows) {{
    for (const u of r.urls_cited) {{
      if (!u.url) continue;
      if (!citMap[u.url]) citMap[u.url] = {{ url: u.url, domain: u.domain, title: u.title || u.url, count: 0, own: u.domain && u.domain.includes(RAW.brand_domain) }};
      citMap[u.url].count++;
    }}
  }}
  const sorted = Object.values(citMap).sort((a, b) => b.count - a.count);

  const bodyRows = sorted.length
    ? sorted.map(c => `
      <tr>
        <td class="url-cell"><a href="${{esc(c.url)}}" target="_blank" title="${{esc(c.url)}}">${{esc(c.title || c.url)}}</a></td>
        <td>${{esc(c.domain)}}</td>
        <td><span class="badge ${{c.own ? 'badge-own' : 'badge-third'}}">${{c.own ? 'Own' : 'Third-party'}}</span></td>
        <td style="font-weight:600">${{c.count}}</td>
      </tr>`).join("")
    : '<tr><td colspan="4" class="empty">No citations in this selection.</td></tr>';

  document.getElementById("cit-table-wrap").innerHTML = `
    <table>
      <thead><tr><th>Source</th><th>Domain</th><th>Type</th><th>Count</th></tr></thead>
      <tbody>${{bodyRows}}</tbody>
    </table>`;
}}

// ------------------------------------------------------------------ //
// AI Answers tab
// ------------------------------------------------------------------ //
function renderAnswers() {{
  const rows = filteredLog();

  const bodyRows = rows.length
    ? rows.map(r => `
      <tr>
        <td style="white-space:nowrap;color:#6b7280">${{esc(r.date)}}</td>
        <td><span class="engine-name">${{r.engine === "chatgpt" ? "ChatGPT" : "Claude"}}</span></td>
        <td><span class="badge ${{r.latitude_mentioned ? 'badge-yes' : 'badge-no'}}">${{r.latitude_mentioned ? 'Yes' : 'No'}}</span></td>
        <td>${{r.brands_mentioned.map(b => `<span class="brand-tag ${{b === RAW.brand_name ? 'you' : ''}}">${{esc(b)}}</span>`).join("")}}</td>
        <td>${{r.urls_cited.slice(0, 4).map(u => `<span class="brand-tag" title="${{esc(u.url)}}">${{esc(u.domain)}}</span>`).join("") + (r.urls_cited.length > 4 ? ` <span style="color:#9ca3af;font-size:11px">+${{r.urls_cited.length - 4}}</span>` : "")}}</td>
      </tr>`).join("")
    : '<tr><td colspan="5" class="empty">No responses in this selection.</td></tr>';

  document.getElementById("answers-table-wrap").innerHTML = `
    <table>
      <thead><tr>
        <th>Date</th><th>Engine</th><th>Mentioned?</th>
        <th>Brands mentioned</th><th>Citations</th>
      </tr></thead>
      <tbody>${{bodyRows}}</tbody>
    </table>`;
}}

// ------------------------------------------------------------------ //
// Overview panel
// ------------------------------------------------------------------ //
function renderOverview() {{
  const s = RAW.summary;
  document.getElementById("ov-prompts").textContent = RAW.prompts_table.length;
  document.getElementById("ov-sov").textContent = fmtPct(s.sov_pct);
  document.getElementById("ov-vis").textContent = fmtPct(s.vis_pct);
  document.getElementById("ov-total").textContent = s.total_responses;

  const rows = RAW.prompts_table.map(p => {{
    const topBrands = (p.top_brands || []).slice(0, 3).map(b =>
      `<span class="brand-tag ${{b.name === RAW.brand_name ? 'you' : ''}}">${{esc(b.name)}}</span>`
    ).join("");
    const sovColor = p.vis_pct > 0 ? "#16a34a" : "#9ca3af";
    return `<tr>
      <td class="overview-table-prompt" title="${{esc(p.text)}}">${{esc(p.text)}}</td>
      <td><span class="topic-tag">${{esc(p.topic || "–")}}</span></td>
      <td style="font-weight:600;color:${{sovColor}}">${{p.vis_pct > 0 ? p.vis_pct + "%" : "–"}}</td>
      <td>${{topBrands}}</td>
    </tr>`;
  }}).join("");

  document.getElementById("ov-table-wrap").innerHTML = `
    <table>
      <thead><tr><th>Prompt</th><th>Topic</th><th>Visibility</th><th>Top brands</th></tr></thead>
      <tbody>${{rows || '<tr><td colspan="4" class="empty">No data yet.</td></tr>'}}</tbody>
    </table>`;
}}

// ------------------------------------------------------------------ //
// Brand table builder (shared by SoV + Visibility tabs)
// ------------------------------------------------------------------ //
function buildBrandsTable(data, mode) {{
  const maxVal = data.length ? (mode === "sov" ? data[0].sov : data[0].vis) : 1;
  const rows = data.map((b, i) => {{
    const isYou = b.name === RAW.brand_name;
    const pct = mode === "sov" ? b.sov : b.vis;
    const count = mode === "sov" ? b.mentions : b.responses;
    const barWidth = maxVal > 0 ? (pct / maxVal * 100).toFixed(1) : 0;
    return `<tr class="${{isYou ? 'highlight' : ''}}">
      <td style="color:#9ca3af;width:28px;font-size:12px">${{i + 1}}</td>
      <td>
        <strong>${{esc(b.name)}}</strong>
        ${{isYou ? '<span style="font-size:10px;color:#f59e0b;margin-left:4px;font-weight:600">YOU</span>' : ''}}
      </td>
      <td style="width:160px">
        ${{fmtPct(pct)}}
        <div class="bar-bg"><div class="bar-fill" style="width:${{barWidth}}%;background:${{brandColor(b.name)}}"></div></div>
      </td>
      <td style="color:#374151">${{count}}</td>
    </tr>`;
  }}).join("");

  const col3 = mode === "sov" ? "Share of Voice" : "Visibility";
  const col4 = mode === "sov" ? "Mentions" : "Responses";
  return `<table>
    <thead><tr><th>#</th><th>Brand</th><th>${{col3}}</th><th>${{col4}}</th></tr></thead>
    <tbody>${{rows}}</tbody>
  </table>`;
}}

// ------------------------------------------------------------------ //
// Init
// ------------------------------------------------------------------ //
function init() {{
  if (RAW.days.length) {{
    const first = RAW.days[0], last = RAW.days[RAW.days.length - 1];
    document.getElementById("date-range").textContent =
      first === last ? first : first + " – " + last;
  }}

  renderSidebar();
  renderOverview();
}}

init();
</script>
</body>
</html>
"""


def generate(output_path=None):
    settings  = load_settings()
    responses = database.get_all_responses()

    if not responses:
        print("No data in the database yet. Run a daily scan first.")
        return

    data = build_data(responses, settings)
    data_json = json.dumps(data)

    html = HTML_TEMPLATE.format(brand_name=settings["brand"]["name"]) \
        .replace("__DATA__", data_json)

    out = output_path or OUTPUT_PATH
    Path(out).write_text(html, encoding="utf-8")
    print(f"Dashboard written to: {out}")
    print(f"Open it in your browser: open {out}")
