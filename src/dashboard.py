"""
Generates dashboard.html from the SQLite database.
Layout implements the "Daily briefing" design (Claude Design project
"AEO Tracker UIUX Improvements"): Briefing / Prompts / Citations / AI Answers.
All data is embedded as JSON; charts are inline SVG (no external deps).
"""

import json
from collections import defaultdict
from pathlib import Path

from . import database

OUTPUT_PATH = Path(__file__).parent.parent / "dashboard.html"
CONFIG_DIR  = Path(__file__).parent.parent / "config"

# A scan day is "full" when total brand mentions reach this floor;
# partial days (Mac asleep mid-run) are excluded from trend charts.
FULL_DAY_MIN_MENTIONS = 300
# Response texts are embedded for the most recent N scan days only,
# to keep the HTML payload bounded as history grows.
ANSWER_TEXT_DAYS = 7
ANSWER_TEXT_MAX_CHARS = 6000


def load_settings():
    with open(CONFIG_DIR / "settings.json") as f:
        return json.load(f)


def article_type(domain, url, own_domain, competitor_domains):
    if own_domain in domain or own_domain in url:
        return "own"
    if any(d in domain for d in competitor_domains):
        return "comp"
    return "third"


def build_data(responses, settings):
    """Pre-compute everything the dashboard needs from raw response rows."""
    brand_name  = settings["brand"]["name"]
    brand_domain = settings["brand"]["domain"]
    competitor_names = [c["name"] for c in settings["competitors"]]
    competitor_domains = {c["domain"] for c in settings["competitors"]}
    all_brand_names = [brand_name] + competitor_names

    try:
        prompts_config = {str(p["id"]): p for p in json.load(open(CONFIG_DIR / "prompts.json"))}
    except Exception:
        prompts_config = {}

    days = sorted(set(r["date"] for r in responses))
    text_days = set(days[-ANSWER_TEXT_DAYS:])

    mentions_by_day = defaultdict(lambda: defaultdict(int))
    articles = {}          # url -> aggregate
    prompt_rows = {}       # pid -> aggregate
    prompt_articles = defaultdict(lambda: defaultdict(int))  # pid -> url -> cites
    prompt_daily = defaultdict(lambda: defaultdict(lambda: [0, 0, 0]))  # pid -> date -> [mentioned, total, cited]
    # pid -> date -> {brand: mentions} — per-prompt per-day per-brand, for the multi-line prompt chart
    prompt_brand_daily = defaultdict(lambda: defaultdict(lambda: defaultdict(int)))
    answers = []

    for r in responses:
        d, pid = r["date"], r["prompt_id"]
        brands = [b for b in r["brands_mentioned"] if b in all_brand_names]
        urls = r["urls_cited"]

        for b in brands:
            mentions_by_day[d][b] += 1

        pr = prompt_rows.setdefault(pid, {
            "id": pid,
            "text": r["prompt_text"],
            "topic": prompts_config.get(pid, {}).get("topic", ""),
            "total": 0, "latMentions": 0, "latCited": False, "cites": 0,
            "brand_counts": defaultdict(int),
        })
        pr["total"] += 1
        pr["text"] = r["prompt_text"]  # keep latest text
        pd = prompt_daily[pid][d]
        pd[1] += 1
        if r["latitude_mentioned"]:
            pr["latMentions"] += 1
            pd[0] += 1
        if r["latitude_cited"]:
            pr["latCited"] = True
            pd[2] += 1
        for b in brands:
            pr["brand_counts"][b] += 1
            prompt_brand_daily[pid][d][b] += 1

        for u in urls:
            url, domain = u.get("url", ""), u.get("domain", "")
            if not url:
                continue
            pr["cites"] += 1
            prompt_articles[pid][url] += 1
            a = articles.setdefault(url, {
                "url": url, "domain": domain, "title": "",
                "cites": 0, "prompts": set(), "first": d, "last": d,
            })
            a["cites"] += 1
            a["prompts"].add(pid)
            a["first"], a["last"] = min(a["first"], d), max(a["last"], d)
            if len(u.get("title", "")) > len(a["title"]):
                a["title"] = u["title"]

        status = "absent"
        if r["latitude_mentioned"]:
            status = "recommended" if r.get("latitude_recommended") else "mentioned"
        text = ""
        if d in text_days and r.get("response_text"):
            text = r["response_text"][:ANSWER_TEXT_MAX_CHARS]
            if len(r["response_text"]) > ANSWER_TEXT_MAX_CHARS:
                text += "\n[… truncated — full text in results.db]"
        answers.append({
            "date": d, "engine": r["engine"], "pid": pid,
            "prompt": r["prompt_text"], "status": status,
            "brands": brands,
            "sources": [u.get("domain", "") for u in urls if u.get("domain")],
            "text": text,
        })

    # ---- articles table ---- #
    articles_out = []
    for a in sorted(articles.values(), key=lambda x: -x["cites"]):
        articles_out.append({
            "url": a["url"], "domain": a["domain"],
            "title": a["title"] or a["url"],
            "cites": a["cites"], "nPrompts": len(a["prompts"]),
            "last": a["last"],
            "type": article_type(a["domain"], a["url"], brand_domain, competitor_domains),
        })

    cit_totals = {
        "total": sum(a["cites"] for a in articles_out),
        "own":   sum(a["cites"] for a in articles_out if a["type"] == "own"),
        "comp":  sum(a["cites"] for a in articles_out if a["type"] == "comp"),
        "articles": len(articles_out),
    }
    by_url = {a["url"]: a for a in articles_out}

    # ---- per-prompt outputs ---- #
    prompts_out, prompt_articles_out, prompt_brands_out = [], {}, {}
    for pid, pr in sorted(prompt_rows.items()):
        arts = sorted(prompt_articles[pid].items(), key=lambda x: -x[1])[:12]
        prompt_articles_out[pid] = [
            {"title": by_url[u]["title"], "domain": by_url[u]["domain"],
             "url": u, "cites": c, "type": by_url[u]["type"]}
            for u, c in arts
        ]
        prompt_brands_out[pid] = [
            {"name": b, "count": c}
            for b, c in sorted(pr["brand_counts"].items(), key=lambda x: -x[1])
        ]
        prompts_out.append({
            "id": pid, "text": pr["text"], "topic": pr["topic"],
            "total": pr["total"], "latMentions": pr["latMentions"],
            "latCited": pr["latCited"], "cites": pr["cites"],
            "nArts": len(prompt_articles[pid]),
        })

    # citations per engine (for the coverage card)
    cit_by_engine = defaultdict(int)
    for r in responses:
        if r["urls_cited"]:
            cit_by_engine[r["engine"]] += 1

    return {
        "brand": brand_name,
        "domain": brand_domain,
        "brands": all_brand_names,
        "days": days,
        "fullDayMin": FULL_DAY_MIN_MENTIONS,
        "mentionsByDay": {d: dict(v) for d, v in mentions_by_day.items()},
        "prompts": prompts_out,
        "promptArticles": prompt_articles_out,
        "promptBrands": prompt_brands_out,
        "promptDaily": {pid: {d: list(v) for d, v in days_.items()}
                        for pid, days_ in prompt_daily.items()},
        "promptBrandDaily": {pid: {d: dict(bc) for d, bc in days_.items()}
                             for pid, days_ in prompt_brand_daily.items()},
        "articles": articles_out,
        "citTotals": cit_totals,
        "citByEngine": dict(cit_by_engine),
        "answers": sorted(answers, key=lambda x: x["date"], reverse=True),
    }


HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>AEO Tracker — __BRAND__</title>
<style>
  *, *::before, *::after { box-sizing:border-box; margin:0; padding:0; }
  body { font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","Helvetica Neue",sans-serif;
         background:#eef1f5; color:#0b1220; font-size:14px; }
  a { color:#0a6fd1; text-decoration:none; } a:hover { text-decoration:underline; }

  .topbar { background:#fff; border-bottom:1px solid #e5e7eb; padding:0 28px; height:56px;
            display:flex; align-items:center; gap:12px; position:sticky; top:0; z-index:20; }
  .logo { width:26px; height:26px; border-radius:7px; background:#0a6fd1; color:#fff;
          font-size:13px; font-weight:800; display:flex; align-items:center; justify-content:center; }
  .brand { font-size:15px; font-weight:700; letter-spacing:-.2px; }
  .tabs { display:flex; gap:2px; margin-left:20px; background:#f1f4f8; border-radius:8px; padding:3px; }
  .tab { font-size:12px; font-weight:500; padding:4px 12px; color:#66707a; cursor:pointer; border-radius:6px; }
  .tab.on { font-weight:600; color:#0b1220; background:#fff; box-shadow:0 1px 2px rgba(0,0,0,.08); }
  .range { margin-left:auto; font-size:12px; color:#66707a; display:flex; align-items:center; gap:5px; }
  .scanbadge { font-size:11px; font-weight:600; color:#a16207; background:#fef9c3;
               border:1px solid #fde68a; padding:3px 9px; border-radius:99px; }

  .page { max-width:1280px; margin:0 auto; padding:24px 28px 64px; }
  .card { background:#fff; border:1px solid #e5e7eb; border-radius:12px; }
  .h { font-size:13px; font-weight:700; }
  .sub { font-size:11px; color:#8a93a0; }
  .kicker { font-size:10.5px; font-weight:700; color:#8a93a0; text-transform:uppercase; letter-spacing:.06em; }
  .chip { font-size:11px; font-weight:500; padding:3px 10px; border-radius:99px;
          border:1px solid #e5e7eb; color:#66707a; cursor:pointer; white-space:nowrap; }
  .chip.on { font-weight:600; background:#0b1220; color:#fff; border-color:#0b1220; }
  .bar { height:5px; background:#eef1f5; border-radius:3px; overflow:hidden; display:block; }
  .bar > span { display:block; height:5px; border-radius:3px; background:#94a3b8; }
  .pill { font-size:10px; font-weight:700; padding:2px 8px; border-radius:99px; text-align:center; white-space:nowrap; }
  .pill.own { background:#dcfce7; color:#15803d; } .pill.comp { background:#fee2e2; color:#b91c1c; }
  .pill.third { background:#eef2f7; color:#475569; }
  .pill.gap { background:#fef3c7; color:#a16207; } .pill.cited { background:#dcfce7; color:#15803d; }
  .bchip { font-size:10.5px; font-weight:600; padding:2px 7px; border-radius:5px; background:#f1f4f8; color:#475569; }
  .bchip.you { background:#eaf3fd; color:#0a6fd1; }
  .btn { font-size:12px; font-weight:600; color:#0a6fd1; border:1px solid #cfe3f8; background:#f4f9fe;
         border-radius:7px; padding:7px 14px; cursor:pointer; display:inline-block; }
  .rowline { border-bottom:1px solid #f4f6f9; }
  .muted { color:#8a93a0; } .tiny { font-size:11px; }
  .clamp2 { display:-webkit-box; -webkit-line-clamp:2; -webkit-box-orient:vertical; overflow:hidden; }
  .ellip { white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
  input.search { border:1px solid #e5e7eb; border-radius:7px; padding:7px 10px; font-size:12.5px;
                 background:#f9fafb; width:100%; outline:none; }
  input.search:focus { border-color:#0a6fd1; background:#fff; }
</style>
</head>
<body>
<div class="topbar">
  <div class="logo">L</div>
  <span class="brand">__BRAND__ · AEO Tracker</span>
  <div class="tabs" id="tabs"></div>
  <span class="range" id="range"></span>
  <span class="scanbadge" id="scanbadge" style="display:none;"></span>
</div>
<div class="page" id="view"></div>

<script id="data" type="application/json">__DATA__</script>
<script>
const RAW = JSON.parse(document.getElementById("data").textContent);
const YOU = RAW.brand;
const TABS = ["Briefing", "Prompts", "Citations", "AI Answers"];
const state = { tab:"Briefing", pSel:null, pChip:"All", pSearch:"",
                citChip:"All", ansEngine:"All", ansOnlyLat:false, ansPrompt:null,
                leadAll:false, ansOpen:{}, range:"All" };
const RANGES = { "7d":7, "30d":30, "All":Infinity };

/* ---------- range-independent helpers + base day data ---------- */
const byDay = RAW.mentionsByDay;
const allDays = RAW.days;
const dayTotal = {}; allDays.forEach(d => dayTotal[d] = Object.values(byDay[d]||{}).reduce((a,b)=>a+b,0));
const allFull = allDays.filter(d => dayTotal[d] >= RAW.fullDayMin);
const MO = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"];
const fmtD = d => MO[+d.split("-")[1]-1] + " " + (+d.split("-")[2]);
const sovOf = (d,b) => (byDay[d][b]||0) / dayTotal[d] * 100;
const sovAvgOf = (ds,b) => ds.length ? ds.reduce((s,d)=>s+sovOf(d,b),0)/ds.length : 0;
const fmt1 = v => (Math.round(v*10)/10) + "%";
const esc = s => String(s??"").replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;").replace(/"/g,"&quot;");

// range-independent (not affected by the day-window slicer)
const gaps = RAW.prompts.filter(p=>!p.latCited).sort((a,b)=>b.cites-a.cites);
const topThird = RAW.articles.filter(a=>a.type==="third").slice(0,60);
const ownArts = RAW.articles.filter(a=>a.type==="own");
const ownCites = ownArts.reduce((s,a)=>s+a.cites,0);

/* ---------- windowed derived data (recomputed per render for state.range) ---------- */
// D holds the current window's values; derive(range) refreshes it. All render
// functions read from D so the slicer flows through every chart + KPI.
let D = {};
function derive(range){
  const span = RANGES[range] || Infinity;
  const full = span === Infinity ? allFull : allFull.slice(-span);
  const n = full.length;
  const totBrand = {}; RAW.brands.forEach(b => totBrand[b] = full.reduce((s,d)=>s+(byDay[d][b]||0),0));
  const grand = Object.values(totBrand).reduce((a,b)=>a+b,0) || 1;
  const sorted = RAW.brands.slice().sort((a,b)=>totBrand[b]-totBrand[a]);
  const rank = sorted.indexOf(YOU)+1;
  // deltas in SoV percentage points (robust to prompt-set size changes)
  const l3 = full.slice(-3), f3 = full.slice(0,3);
  const delta = {}; RAW.brands.forEach(b => delta[b] = +(sovAvgOf(l3,b)-sovAvgOf(f3,b)).toFixed(1));
  const series = full.map(d => ({d, pct: sovOf(d,YOU)}));
  const sovNowV = n ? series[n-1].pct : 0, sovFirstV = n ? series[0].pct : 0;
  const sovAvgV = n ? series.reduce((s,p)=>s+p.pct,0)/n : 0;
  const dPts = sovNowV - sovFirstV;
  const flat = Math.abs(dPts) < 0.25;
  const trendWord = flat ? "Holding flat" : (dPts>0 ? "Trending up" : "Trending down");
  const trendColor = flat ? "#66707a" : (dPts>0 ? "#15803d" : "#dc2626");
  return { range, full, n, totBrand, grand, sorted, rank, l3, f3, delta,
           series, sovNowV, sovFirstV, sovAvgV, dPts, flat, trendWord, trendColor };
}

function chartGeo(H, maxV){
  const P=8, W=1000;
  return { X:i=>P+(W-2*P)*i/Math.max(1,D.n-1), Y:v=>H-P-(H-2*P)*v/maxV };
}
function typePill(t){ return t==="own" ? RAW.domain : (t==="comp" ? "competitor" : "third-party"); }
// shared brand palette (competitor lines); YOU/Latitude is always #0a6fd1
const PALETTE = {"Arize":"#8b5cf6","Braintrust":"#f59e0b","LangSmith":"#10b981","LangChain":"#0ea5e9","Langfuse":"#ec4899","Datadog":"#64748b"};
const brandColor = b => PALETTE[b] || "#94a3b8";
// multi-line chart legend row (reused by Briefing hero + per-prompt chart)
function chartLegend(brands){
  return brands.map(b=>`<span style="display:inline-flex; align-items:center; gap:5px; font-size:11px; color:#5b6572;"><span style="width:10px; height:3px; border-radius:2px; background:${b===YOU?"#0a6fd1":brandColor(b)}; display:inline-block;"></span>${esc(b)}</span>`).join("");
}

/* ---------- topbar ---------- */
function renderTabs(){
  document.getElementById("tabs").innerHTML = TABS.map(t =>
    `<span class="tab ${state.tab===t?"on":""}" onclick="setTab('${t}')">${t}</span>`).join("");
  const span = D.full.length
    ? fmtD(D.full[0]) + " – " + fmtD(D.full[D.full.length-1]) + ", " + D.full[D.full.length-1].slice(0,4)
    : "";
  const chips = Object.keys(RANGES).map(r =>
    `<span class="chip ${state.range===r?"on":""}" onclick="setRange('${r}')">${r}</span>`).join("");
  document.getElementById("range").innerHTML =
    `<span class="tiny muted" style="margin-right:2px;">${span}</span>${chips}`;
  const lastDay = allDays[allDays.length-1];
  const badge = document.getElementById("scanbadge");
  if (dayTotal[lastDay] < RAW.fullDayMin) { badge.style.display=""; badge.textContent = "Latest scan (" + fmtD(lastDay) + ") partial"; }
}
function setTab(t){ state.tab = t; render(); }
function setRange(r){ state.range = r; render(); }

/* ---------- Briefing ---------- */
function svgLine(H, maxV, pts, color, w, op){
  return `<polyline points="${pts}" fill="none" stroke="${color}" stroke-width="${w}" opacity="${op}" vector-effect="non-scaling-stroke"></polyline>`;
}
function renderBriefing(){
  const { n, full, series, sovAvgV, sovNowV, dPts, sorted, totBrand, grand, rank,
          delta, l3, trendWord, trendColor } = D;
  if (!n) return `<div class="card" style="padding:24px;">Not enough full scan days yet for this range.</div>`;

  // ---- hero chart: top-6 brands (thin) + YOU (emphasized) ----
  const top6 = sorted.slice(0,6);
  const heroBrands = top6.includes(YOU) ? top6 : [...top6, YOU];
  const maxAll = Math.max(...heroBrands.map(b=>Math.max(...full.map(d=>sovOf(d,b)))), 0.1) * 1.20;
  const g = chartGeo(200, maxAll);
  const heroPts = b => full.map((d,i)=>g.X(i).toFixed(1)+","+g.Y(sovOf(d,b)).toFixed(1)).join(" ");
  const latPtsArr = full.map((d,i)=>g.X(i).toFixed(1)+","+g.Y(sovOf(d,YOU)).toFixed(1));
  const latArea = "M"+g.X(0)+",192 L"+latPtsArr.join(" L")+" L"+g.X(n-1)+",192 Z";
  const avgY = g.Y(sovAvgV).toFixed(1);
  let heroLines = top6.filter(b=>b!==YOU).map(b=>svgLine(200,maxAll,heroPts(b),brandColor(b),1.6,0.8));
  heroLines.push(`<path d="${latArea}" fill="#0a6fd1" opacity="0.08"></path>`);
  heroLines.push(svgLine(200,maxAll,heroPts(YOU),"#0a6fd1",3,1));
  const dots = latPtsArr.map(p=>`<circle cx="${p.split(",")[0]}" cy="${p.split(",")[1]}" r="3" fill="#fff" stroke="#0a6fd1" stroke-width="2" vector-effect="non-scaling-stroke"></circle>`).join("");
  const heroLegend = chartLegend(heroBrands);
  const dayLabels = series.map((p,i)=>(i%2===0||i===n-1)?fmtD(p.d):"·");

  const thr = 0.75;
  const movers = RAW.brands.map(b=>({name:b, d:delta[b], avg:sovAvgOf(l3,b).toFixed(1)})).filter(m=>Math.abs(m.d)>=thr);
  const gainers = movers.filter(m=>m.d>0).sort((a,b)=>b.d-a.d).slice(0,4);
  const losers  = movers.filter(m=>m.d<0).sort((a,b)=>a.d-b.d).slice(0,4);
  const moverRow = (m,c) => `<div style="display:flex; align-items:center; gap:8px; padding:6px 0;" class="rowline">
      <span style="font-size:12.5px; font-weight:600; flex:1;">${esc(m.name)}</span>
      <span class="tiny muted">${m.avg}% SoV</span>
      <span style="font-size:12px; font-weight:700; color:${c};">${m.d>0?"+":""}${m.d} pts</span></div>`;

  const N = state.leadAll ? sorted.length : 10;
  const leadAll = sorted.map((b,i)=>({
    rank:i+1, name:b, you:b===YOU,
    sov:(totBrand[b]/grand*100).toFixed(1),
    barW:(totBrand[b]/(totBrand[sorted[0]]||1)*100).toFixed(1),
    delta: delta[b]>0 ? "+"+delta[b] : (delta[b]<0 ? ""+delta[b] : "—"),
    dColor: delta[b]>0.5 ? "#15803d" : (delta[b]<-0.5 ? "#dc2626" : "#a8b0bb"),
  }));
  let lead = leadAll.slice(0,N);
  if (!lead.some(r=>r.you)) lead.push(leadAll[rank-1]);
  const leadRows = lead.map(b=>`
    <div style="display:grid; grid-template-columns:26px 1fr 120px 44px 46px; align-items:center; gap:10px; padding:6px 8px; border-radius:6px; background:${b.you?"#eaf3fd":"transparent"};">
      <span class="tiny muted">${b.rank}</span>
      <span class="ellip" style="font-size:12.5px; font-weight:600;">${esc(b.name)}${b.you?'<span style="font-size:9.5px; font-weight:800; color:#0a6fd1; margin-left:6px; letter-spacing:.05em;">YOU</span>':""}</span>
      <span class="bar"><span style="background:${b.you?"#0a6fd1":"#c3ccd6"}; width:${b.barW}%;"></span></span>
      <span style="font-size:12px; font-weight:600; text-align:right;">${b.sov}%</span>
      <span style="font-size:11px; font-weight:700; text-align:right; color:${b.dColor};">${b.delta}</span>
    </div>`).join("");

  const gapRows = gaps.slice(0,6).map(p=>`
    <div style="display:flex; gap:10px; align-items:flex-start; padding:8px 0;" class="rowline">
      <span style="font-size:10.5px; font-weight:700; color:#b45309; background:#fef3c7; border-radius:4px; padding:2px 6px; margin-top:1px; white-space:nowrap;">${p.cites} cites in play</span>
      <span class="clamp2" style="font-size:12px; color:#374151; line-height:1.4; cursor:pointer;" onclick="openPrompt('${p.id}')">${esc(p.text)}</span>
    </div>`).join("");

  const outRows = topThird.slice(0,6).map(o=>`
    <div style="display:flex; gap:10px; align-items:flex-start; padding:8px 0;" class="rowline">
      <span style="font-size:12px; font-weight:700; color:#0a6fd1; min-width:24px;">${o.cites}×</span>
      <div style="min-width:0;">
        <div class="ellip" style="font-size:12px; color:#374151; line-height:1.35;"><a href="${esc(o.url)}" target="_blank" rel="noopener">${esc(o.title)}</a></div>
        <div class="tiny muted">${esc(o.domain)} · ${o.nPrompts} prompts</div>
      </div>
    </div>`).join("");

  const latPerDay = sovAvgOf(l3,YOU).toFixed(1);
  return `
  <div style="display:flex; flex-direction:column; gap:20px;">
    <div class="card" style="padding:24px 28px; display:grid; grid-template-columns:300px 1fr; gap:36px;">
      <div style="display:flex; flex-direction:column; gap:14px;">
        <div style="font-size:12px; font-weight:700; color:${trendColor}; text-transform:uppercase; letter-spacing:.08em;">${trendWord}</div>
        <div style="display:flex; align-items:baseline; gap:10px;">
          <span style="font-size:56px; font-weight:800; letter-spacing:-.03em; line-height:1;">${fmt1(sovNowV)}</span>
          <span style="font-size:15px; font-weight:600; color:${trendColor};">${(dPts>=0?"+":"")+dPts.toFixed(1)} pts vs ${fmtD(full[0])}</span>
        </div>
        <div style="font-size:13px; color:#5b6572; line-height:1.5;">Share of Voice — ${esc(YOU)}'s slice of every brand mention across ${RAW.prompts.length} tracked prompts</div>
        <div style="display:flex; gap:20px; border-top:1px solid #eef1f5; padding-top:14px;">
          <div><div style="font-size:22px; font-weight:700;">#${rank}</div><div class="tiny muted">of ${RAW.brands.length} brands</div></div>
          <div><div style="font-size:22px; font-weight:700;">${latPerDay}%</div><div class="tiny muted">SoV, last 3 scans</div></div>
          <div><div style="font-size:22px; font-weight:700;">${ownCites}</div><div class="tiny muted">citations of ${esc(RAW.domain)}</div></div>
        </div>
      </div>
      <div style="display:flex; flex-direction:column; gap:6px;">
        <div style="font-size:12px; font-weight:600; color:#66707a;">${esc(YOU)} SoV, daily — vs top ${top6.length} brands</div>
        <div style="position:relative; height:200px;">
          <svg viewBox="0 0 1000 200" preserveAspectRatio="none" style="position:absolute; inset:0; width:100%; height:200px;">
            <line x1="8" x2="992" y1="${avgY}" y2="${avgY}" stroke="#8a93a0" stroke-width="1" stroke-dasharray="4 4"></line>
            ${heroLines.join("")}
            ${dots}
          </svg>
          <span style="position:absolute; right:0; top:${Math.max(12,+avgY)}px; transform:translateY(-100%); font-size:10px; color:#8a93a0;">${esc(YOU)} avg ${fmt1(sovAvgV)}</span>
        </div>
        <div style="display:flex; justify-content:space-between; font-size:10.5px; color:#8a93a0;">${dayLabels.map(t=>`<span>${t}</span>`).join("")}</div>
        <div style="display:flex; flex-wrap:wrap; gap:6px 14px; padding-top:4px;">${heroLegend}</div>
      </div>
    </div>

    <div style="display:grid; grid-template-columns:1fr 1fr 1fr; gap:16px;">
      <div class="card" style="padding:18px 20px; display:flex; flex-direction:column; gap:12px;">
        <div style="display:flex; align-items:baseline; justify-content:space-between;"><span class="h">Content gaps</span><span class="sub">${gaps.length} prompts never cite ${esc(RAW.domain)}</span></div>
        <div>${gapRows}</div>
        <a href="#" onclick="state.pChip='Gaps'; setTab('Prompts'); return false;" style="font-size:12px; font-weight:600;">All ${gaps.length} gap prompts →</a>
      </div>
      <div class="card" style="padding:18px 20px; display:flex; flex-direction:column; gap:10px;">
        <div style="display:flex; align-items:baseline; justify-content:space-between;"><span class="h">Competitor movement</span><span class="sub">last 3 scans vs first 3, SoV pts</span></div>
        <div style="font-size:11px; font-weight:600; color:#15803d; text-transform:uppercase; letter-spacing:.05em;">Gaining</div>
        <div>${gainers.map(m=>moverRow(m,"#15803d")).join("") || '<span class="tiny muted">none above threshold</span>'}</div>
        <div style="font-size:11px; font-weight:600; color:#dc2626; text-transform:uppercase; letter-spacing:.05em;">Slipping</div>
        <div>${losers.map(m=>moverRow(m,"#dc2626")).join("") || '<span class="tiny muted">none above threshold</span>'}</div>
      </div>
      <div class="card" style="padding:18px 20px; display:flex; flex-direction:column; gap:12px;">
        <div style="display:flex; align-items:baseline; justify-content:space-between;"><span class="h">Outreach targets</span><span class="sub">most-cited third-party articles</span></div>
        <div>${outRows}</div>
        <a href="#" onclick="setTab('Citations'); return false;" style="font-size:12px; font-weight:600;">All citations →</a>
      </div>
    </div>

    <div class="card" style="padding:20px 24px; display:flex; flex-direction:column; gap:4px;">
      <div style="display:flex; align-items:baseline; justify-content:space-between;"><span class="h">Leaderboard — share of voice</span>
        <a href="#" onclick="state.leadAll=!state.leadAll; render(); return false;" style="font-size:11.5px; font-weight:600;">${state.leadAll?"Top 10 ▴":"Show all "+RAW.brands.length+" ▾"}</a></div>
      ${leadRows}
    </div>
  </div>`;
}

/* ---------- Prompts (master-detail) ---------- */
function openPrompt(pid){ state.pSel = pid; state.tab = "Prompts"; render(); }
function promptVisChart(pid){
  const daily = RAW.promptDaily[pid] || {};          // date -> [mentioned, total, cited]
  const bDaily = RAW.promptBrandDaily[pid] || {};     // date -> {brand: mentions}
  // respect the range slicer: only days inside the current window
  const win = new Set(D.full);
  let ds = Object.keys(daily).sort().filter(d => win.has(d));
  if (ds.length < 2) return "";

  // top-6 brands for THIS prompt by total mentions within the window, + YOU
  const totals = {};
  RAW.brands.forEach(b => totals[b] = ds.reduce((s,d)=>s+((bDaily[d]||{})[b]||0),0));
  const ranked = RAW.brands.filter(b=>totals[b]>0).sort((a,b)=>totals[b]-totals[a]);
  const top6 = ranked.slice(0,6);
  const chartBrands = top6.includes(YOU) ? top6 : [...top6, YOU];

  const H = 140, P = 10, W = 1000, m = ds.length;
  const X = i => P + (W-2*P)*i/(m-1);
  // per-brand SoV for the prompt = brand mentions / total answers that day
  const bPct = (b,d) => ((bDaily[d]||{})[b]||0) / (daily[d][1]||1) * 100;
  const maxV = Math.max(...chartBrands.map(b=>Math.max(...ds.map(d=>bPct(b,d)))), 0.1) * 1.20;
  const Y = v => H-P - (H-2*P)*v/maxV;
  const bLine = b => ds.map((d,i)=>X(i).toFixed(1)+","+Y(bPct(b,d)).toFixed(1)).join(" ");

  const latAvg = ds.reduce((s,d)=>s+bPct(YOU,d),0)/m;
  const latPts = ds.map((d,i)=>X(i).toFixed(1)+","+Y(bPct(YOU,d)).toFixed(1));
  const area = "M"+X(0)+","+(H-P)+" L"+latPts.join(" L")+" L"+X(m-1)+","+(H-P)+" Z";
  let lines = top6.filter(b=>b!==YOU).map(b=>svgLine(H,maxV,bLine(b),brandColor(b),1.6,0.8));
  lines.push(`<path d="${area}" fill="#0a6fd1" opacity="0.08"></path>`);
  lines.push(svgLine(H,maxV,latPts.join(" "),"#0a6fd1",2.5,1));
  const dots = ds.map((d,i)=>`<circle cx="${latPts[i].split(",")[0]}" cy="${latPts[i].split(",")[1]}" r="3" fill="${daily[d][2]>0?"#0a6fd1":"#fff"}" stroke="#0a6fd1" stroke-width="2" vector-effect="non-scaling-stroke"><title>${fmtD(d)}: ${esc(YOU)} mentioned in ${daily[d][0]}/${daily[d][1]} answers${daily[d][2]>0?" · cited":""}</title></circle>`).join("");
  const labels = ds.map((d,i)=>(i%2===0||i===m-1)?fmtD(d):"·");
  const legend = chartLegend(chartBrands);
  return `
  <div class="card" style="padding:18px 22px; display:flex; flex-direction:column; gap:8px;">
    <div style="display:flex; align-items:baseline; justify-content:space-between;">
      <span class="h">Brand visibility for this prompt, daily</span>
      <span class="sub">% of the day's answers mentioning each brand · filled dot = ${esc(RAW.domain)} cited</span>
    </div>
    <div style="position:relative; height:${H}px;">
      <svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" style="position:absolute; inset:0; width:100%; height:${H}px;">
        <line x1="${P}" x2="${W-P}" y1="${Y(latAvg).toFixed(1)}" y2="${Y(latAvg).toFixed(1)}" stroke="#8a93a0" stroke-width="1" stroke-dasharray="4 4"></line>
        ${lines.join("")}
        ${dots}
      </svg>
      <span style="position:absolute; right:0; top:${Math.max(12,Y(latAvg))}px; transform:translateY(-100%); font-size:10px; color:#8a93a0;">${esc(YOU)} avg ${fmt1(latAvg)}</span>
    </div>
    <div style="display:flex; justify-content:space-between; font-size:10.5px; color:#8a93a0;">${labels.map(t=>`<span>${t}</span>`).join("")}</div>
    <div style="display:flex; flex-wrap:wrap; gap:6px 14px; padding-top:4px;">${legend}</div>
  </div>`;
}
function whatToDo(p, arts){
  const top = arts[0];
  if (!p.latCited && !p.latMentions)
    return `${RAW.domain} has never been cited and ${esc(YOU)} is never mentioned for this prompt. The articles above are what AI answers lean on — pitch the third-party ones for inclusion, or publish a direct answer to this question.`;
  if (!p.latCited)
    return `${esc(YOU)} gets mentioned here but ${RAW.domain} is never cited as a source. Publishing citable content for this question would anchor the mention.`;
  if (top && top.type === "comp")
    return `${RAW.domain} is cited here, but <b>${esc(top.domain)}</b> owns the top slot. Refresh the comparison post and pitch the top third-party articles for inclusion.`;
  return `${RAW.domain} is cited for this prompt. Keep the content fresh to defend the slot.`;
}
function renderPrompts(){
  let list = RAW.prompts.slice().sort((a,b)=>b.cites-a.cites);
  const citedCount = RAW.prompts.filter(p=>p.latCited).length;
  if (state.pChip==="Cited") list = list.filter(p=>p.latCited);
  if (state.pChip==="Gaps")  list = list.filter(p=>!p.latCited);
  const q = state.pSearch.trim().toLowerCase();
  if (q) list = list.filter(p=>p.text.toLowerCase().includes(q) || p.id.includes(q));
  if (!state.pSel || !RAW.prompts.find(p=>p.id===state.pSel)) state.pSel = (list[0]||RAW.prompts[0]).id;
  const sel = RAW.prompts.find(p=>p.id===state.pSel);
  const arts = RAW.promptArticles[sel.id] || [];
  const maxC = arts.length ? arts[0].cites : 1;
  const chips = (RAW.promptBrands[sel.id]||[]).map(c=>
    `<span class="bchip ${c.name===YOU?"you":""}">${esc(c.name)} ×${c.count}</span>`).join("") || '<span class="tiny muted">no tracked brands mentioned</span>';

  const rows = list.map(p=>`
    <div onclick="openPrompt('${p.id}')" style="padding:10px 18px; border-left:3px solid ${p.id===sel.id?"#0a6fd1":(p.latCited?"transparent":"#f59e0b")}; background:${p.id===sel.id?"#eaf3fd":"transparent"}; display:flex; gap:10px; align-items:flex-start; cursor:pointer;" class="rowline">
      <span class="clamp2" style="flex:1; font-size:12.5px; color:#374151; line-height:1.4;">${esc(p.text)}</span>
      <span style="font-size:11px; font-weight:700; color:#5b6572; white-space:nowrap; padding-top:1px;">${p.cites}</span>
      <span class="pill ${p.latCited?"cited":"gap"}">${p.latCited?"cited":"gap"}</span>
    </div>`).join("");

  const artRows = arts.map(a=>`
    <div style="display:grid; grid-template-columns:34px 1fr 88px 110px; align-items:center; gap:12px; padding:6px 0;" class="rowline">
      <span style="font-size:13px; font-weight:700; color:#0a6fd1;">${a.cites}×</span>
      <div style="min-width:0;"><div class="ellip" style="font-size:12.5px; color:#374151;"><a href="${esc(a.url)}" target="_blank" rel="noopener">${esc(a.title)}</a></div><div class="tiny muted">${esc(a.domain)}</div></div>
      <span class="bar"><span style="width:${(a.cites/maxC*100).toFixed(0)}%;"></span></span>
      <span class="pill ${a.type}">${typePill(a.type)}</span>
    </div>`).join("") || '<span class="tiny muted">no citations recorded for this prompt</span>';

  return `
  <div class="card" style="overflow:hidden; display:flex; height:calc(100vh - 140px); min-height:560px;">
    <div style="width:440px; border-right:1px solid #e5e7eb; display:flex; flex-direction:column; flex-shrink:0;">
      <div style="padding:14px 18px 10px; border-bottom:1px solid #f1f4f8; display:flex; flex-direction:column; gap:8px;">
        <input class="search" placeholder="Search ${RAW.prompts.length} prompts…" value="${esc(state.pSearch)}"
               oninput="state.pSearch=this.value; renderKeepFocus(this);">
        <div style="display:flex; gap:5px;">
          <span class="chip ${state.pChip==="All"?"on":""}" onclick="state.pChip='All'; render();">All ${RAW.prompts.length}</span>
          <span class="chip ${state.pChip==="Cited"?"on":""}" onclick="state.pChip='Cited'; render();">Cited ${citedCount}</span>
          <span class="chip ${state.pChip==="Gaps"?"on":""}" style="${state.pChip!=="Gaps"?"border-color:#fde68a; background:#fef9c3; color:#a16207;":""}" onclick="state.pChip='Gaps'; render();">Gaps ${RAW.prompts.length-citedCount}</span>
          <span class="tiny muted" style="margin-left:auto; align-self:center;">by citations in play ↓</span>
        </div>
      </div>
      <div style="flex:1; overflow-y:auto;">${rows}</div>
    </div>
    <div style="flex:1; overflow-y:auto; padding:22px 24px; display:flex; flex-direction:column; gap:16px;">
      <div class="card" style="padding:20px 24px; display:flex; flex-direction:column; gap:12px;">
        <div style="display:flex; align-items:center; gap:10px;">
          <span class="kicker">Prompt ${sel.id}${sel.topic ? " · "+esc(sel.topic) : ""}</span>
          <span class="pill ${sel.latCited?"cited":"gap"}">${sel.latCited ? RAW.domain+" cited" : "never cited"}</span>
        </div>
        <div style="font-size:19px; font-weight:700; line-height:1.35; letter-spacing:-.01em;">${esc(sel.text)}</div>
        <div style="display:flex; gap:24px; border-top:1px solid #eef1f5; padding-top:12px;">
          <div><div style="font-size:20px; font-weight:800;">${sel.cites}</div><div class="tiny muted">citations in play</div></div>
          <div><div style="font-size:20px; font-weight:800;">${sel.nArts}</div><div class="tiny muted">articles cited</div></div>
          <div><div style="font-size:20px; font-weight:800;">${sel.latMentions}/${sel.total}</div><div class="tiny muted">answers mentioning ${esc(YOU)}</div></div>
          <div style="flex:1;"></div>
          <span class="btn" style="align-self:center;" onclick="state.ansPrompt='${sel.id}'; setTab('AI Answers');">View raw answers →</span>
        </div>
      </div>
      ${promptVisChart(sel.id)}
      <div class="card" style="padding:18px 22px; display:flex; flex-direction:column; gap:10px;">
        <div style="display:flex; align-items:baseline; justify-content:space-between;"><span class="h">Who gets cited for this prompt</span><span class="sub">by times cited</span></div>
        ${artRows}
      </div>
      <div style="display:grid; grid-template-columns:1fr 1fr; gap:16px;">
        <div class="card" style="padding:18px 22px; display:flex; flex-direction:column; gap:10px;">
          <span class="h">Brands mentioned in answers</span>
          <div style="display:flex; flex-wrap:wrap; gap:6px;">${chips}</div>
        </div>
        <div class="card" style="padding:18px 22px; display:flex; flex-direction:column; gap:10px;">
          <span class="h">What to do</span>
          <div style="font-size:12.5px; color:#374151; line-height:1.55;">${whatToDo(sel, arts)}</div>
          <a href="#" onclick="setTab('Citations'); return false;" style="font-size:12px; font-weight:600;">See outreach targets →</a>
        </div>
      </div>
    </div>
  </div>`;
}
function renderKeepFocus(input){
  const pos = input.selectionStart;
  render();
  const el = document.querySelector("input.search");
  if (el){ el.focus(); el.setSelectionRange(pos,pos); }
}

/* ---------- Citations ---------- */
function exportCSV(){
  const rows = [["cites","domain","title","url","prompts"]];
  topThird.forEach(a=>rows.push([a.cites, a.domain, a.title.replace(/"/g,"'"), a.url, a.nPrompts]));
  const csv = rows.map(r=>r.map(v=>`"${v}"`).join(",")).join("\n");
  const blob = new Blob([csv], {type:"text/csv"});
  const link = document.createElement("a");
  link.href = URL.createObjectURL(blob); link.download = "outreach-targets.csv"; link.click();
}
function renderCitations(){
  const T = RAW.citTotals;
  let arts = RAW.articles;
  if (state.citChip==="Own") arts = arts.filter(a=>a.type==="own");
  if (state.citChip==="Competitor") arts = arts.filter(a=>a.type==="comp");
  if (state.citChip==="Third-party") arts = arts.filter(a=>a.type==="third");
  const shown = arts.slice(0,80);
  const maxC = RAW.articles[0] ? RAW.articles[0].cites : 1;
  const engParts = Object.entries(RAW.citByEngine).sort((a,b)=>b[1]-a[1]);
  const engMain = engParts.length ? engParts[0][0] : "—";

  const roll = {};
  topThird.forEach(a=>{ roll[a.domain]=(roll[a.domain]||0)+a.cites; });
  const shortlist = Object.entries(roll).sort((a,b)=>b[1]-a[1]).slice(0,6)
    .map(([domain,cites],i)=>`<div style="display:flex; align-items:center; gap:10px; padding:7px 0;" class="rowline">
      <span class="tiny muted" style="width:14px;">${i+1}</span>
      <span style="font-size:12.5px; font-weight:600; flex:1;">${esc(domain)}</span>
      <span style="font-size:12px; font-weight:700; color:#0a6fd1;">${cites}×</span></div>`).join("");

  const ownRows = ownArts.slice(0,6).map(o=>`
    <div style="display:flex; gap:10px; align-items:baseline; padding-bottom:7px;" class="rowline">
      <span style="font-size:12px; font-weight:700; color:#0a6fd1; min-width:26px;">${o.cites}×</span>
      <span class="clamp2" style="font-size:12px; color:#374151; line-height:1.4;"><a href="${esc(o.url)}" target="_blank" rel="noopener">${esc(o.title)}</a></span>
    </div>`).join("") || '<span class="tiny muted">none yet</span>';

  const artRows = shown.map(a=>`
    <div style="display:grid; grid-template-columns:34px 1fr 90px 60px 64px 110px; align-items:center; gap:12px; padding:7px 18px;" class="rowline">
      <span style="font-size:13px; font-weight:700; color:#0a6fd1;">${a.cites}</span>
      <div style="min-width:0;"><div class="ellip" style="font-size:12.5px; color:#374151;"><a href="${esc(a.url)}" target="_blank" rel="noopener">${esc(a.title)}</a></div><div class="tiny muted">${esc(a.domain)}</div></div>
      <span class="bar"><span style="width:${(a.cites/maxC*100).toFixed(0)}%;"></span></span>
      <span style="font-size:12px; color:#5b6572;">${a.nPrompts}</span>
      <span class="tiny muted">${fmtD(a.last)}</span>
      <span class="pill ${a.type}">${typePill(a.type)}</span>
    </div>`).join("");

  const chip = c => `<span class="chip ${state.citChip===c?"on":""}" onclick="state.citChip='${c}'; render();">${c}</span>`;
  return `
  <div style="display:flex; flex-direction:column; gap:16px;">
    <div style="display:grid; grid-template-columns:repeat(4,1fr); gap:14px;">
      <div class="card" style="padding:16px 18px;"><div class="kicker">Total citations</div><div style="font-size:26px; font-weight:800; margin-top:4px;">${T.total.toLocaleString("en-US")}</div><div class="tiny muted" style="margin-top:2px;">across ${T.articles} articles</div></div>
      <div class="card" style="padding:16px 18px; border-color:#cfe3f8;"><div class="kicker" style="color:#0a6fd1;">${esc(RAW.domain)}</div><div style="font-size:26px; font-weight:800; margin-top:4px; color:#0a6fd1;">${T.own}</div><div class="tiny" style="color:#66707a; margin-top:2px;">${(T.own/T.total*100).toFixed(1)}% of all citations</div></div>
      <div class="card" style="padding:16px 18px;"><div class="kicker">Competitor-owned</div><div style="font-size:26px; font-weight:800; margin-top:4px;">${T.comp.toLocaleString("en-US")}</div><div class="tiny muted" style="margin-top:2px;">${(T.comp/T.total*100).toFixed(0)}% — they write the lists</div></div>
      <div class="card" style="padding:16px 18px;"><div class="kicker">Engine coverage</div><div style="font-size:26px; font-weight:800; margin-top:4px; text-transform:capitalize;">${esc(engMain)}</div><div class="tiny muted" style="margin-top:2px;">ChatGPT free tier rarely searches</div></div>
    </div>
    <div style="display:grid; grid-template-columns:1fr 330px; gap:16px; align-items:start;">
      <div class="card" style="overflow:hidden;">
        <div style="padding:14px 18px; border-bottom:1px solid #f1f4f8; display:flex; gap:6px; align-items:center;">
          ${["All","Own","Competitor","Third-party"].map(chip).join("")}
          <span class="tiny muted" style="margin-left:auto;">top ${shown.length} of ${arts.length}</span>
        </div>
        <div style="display:grid; grid-template-columns:34px 1fr 90px 60px 64px 110px; gap:12px; padding:8px 18px; border-bottom:1px solid #eef1f5;" class="kicker">
          <span>Cites</span><span>Source</span><span></span><span>Prompts</span><span>Last seen</span><span>Type</span>
        </div>
        ${artRows}
      </div>
      <div style="display:flex; flex-direction:column; gap:16px;">
        <div class="card" style="padding:18px 20px; display:flex; flex-direction:column; gap:10px;">
          <span class="h">Outreach shortlist</span>
          <span class="tiny muted" style="line-height:1.5; margin-top:-6px;">Third-party publishers ranked by citations — pitch these for inclusion.</span>
          ${shortlist}
          <span class="btn" style="align-self:flex-start;" onclick="exportCSV()">Export as CSV</span>
        </div>
        <div class="card" style="padding:18px 20px; display:flex; flex-direction:column; gap:10px;">
          <span class="h">Your cited articles</span>
          ${ownRows}
        </div>
      </div>
    </div>
  </div>`;
}

/* ---------- AI Answers ---------- */
function toggleAns(key){ state.ansOpen[key] = !state.ansOpen[key]; render(); }
function renderAnswers(){
  let rows = RAW.answers;
  if (state.ansEngine!=="All") rows = rows.filter(r=>r.engine.toLowerCase()===state.ansEngine.toLowerCase());
  if (state.ansOnlyLat) rows = rows.filter(r=>r.status!=="absent");
  if (state.ansPrompt) rows = rows.filter(r=>r.pid===state.ansPrompt);
  const byDate = {};
  rows.forEach(r=>{ (byDate[r.date]=byDate[r.date]||[]).push(r); });
  const dates = Object.keys(byDate).sort().reverse();
  const selPrompt = state.ansPrompt ? RAW.prompts.find(p=>p.id===state.ansPrompt) : null;

  const statusPill = s => s==="recommended" ? '<span class="pill" style="background:#dcfce7; color:#15803d;">Recommended</span>'
    : s==="mentioned" ? '<span class="pill" style="background:#eaf3fd; color:#0a6fd1;">Mentioned</span>'
    : '<span class="pill" style="background:#fee2e2; color:#b91c1c;">Not mentioned</span>';

  const dayBlocks = dates.slice(0,14).map((d,di)=>{
    const dayRows = byDate[d].map((r,i)=>{
      const key = d+"|"+r.pid+"|"+r.engine;
      const open = state.ansOpen[key];
      const srcs = r.sources.slice(0,2).map(s=>`<span style="font-size:10.5px; color:#5b6572; background:#f1f4f8; border-radius:5px; padding:2px 7px;">${esc(s)}</span>`).join("");
      const extra = r.sources.length>2 ? `<span style="font-size:10.5px; color:#a8b0bb;">+${r.sources.length-2}</span>` : "";
      const chips = r.brands.slice(0,4).map(c=>`<span class="bchip ${c===YOU?"you":""}">${esc(c)}</span>`).join("");
      return `
      <div style="cursor:${r.text?"pointer":"default"};" onclick="${r.text?`toggleAns('${key}')`:""}">
        <div style="display:grid; grid-template-columns:88px 1fr 120px 220px 190px; align-items:center; gap:12px; padding:9px 18px;" class="rowline">
          <span style="display:inline-flex; align-items:center; gap:6px; font-size:12px; font-weight:600; color:#374151;"><span style="width:7px; height:7px; border-radius:99px; background:${r.engine==="claude"?"#d97706":"#10a37f"}; display:inline-block;"></span>${r.engine==="claude"?"Claude":"ChatGPT"}</span>
          <span class="clamp2" style="font-size:12.5px; color:#374151; line-height:1.4;">${esc(r.prompt)}</span>
          ${statusPill(r.status)}
          <span style="display:flex; flex-wrap:wrap; gap:4px;">${chips}</span>
          <span style="display:flex; flex-wrap:wrap; gap:4px; align-items:center;">${srcs}${extra}</span>
        </div>
        ${open && r.text ? `<div style="padding:14px 18px 18px; background:#f9fafb; border-bottom:1px solid #eef1f5; font-size:12.5px; color:#374151; line-height:1.6; white-space:pre-wrap;">${esc(r.text)}</div>` : ""}
      </div>`;
    }).join("");
    return `
    <div style="display:flex; flex-direction:column; gap:8px;">
      <div style="display:flex; align-items:baseline; gap:10px;">
        <span style="font-size:14px; font-weight:700;">${fmtD(d)}, ${d.slice(0,4)}</span>
        <span class="tiny muted">${di===0?"latest scan":""} · ${byDate[d].length} answers${dayTotal[d]<RAW.fullDayMin?" · partial scan":""}</span>
      </div>
      <div class="card" style="overflow:hidden;">
        <div style="display:grid; grid-template-columns:88px 1fr 120px 220px 190px; gap:12px; padding:8px 18px; border-bottom:1px solid #eef1f5;" class="kicker">
          <span>Engine</span><span>Prompt</span><span>${esc(YOU)}</span><span>Brands mentioned</span><span>Sources</span>
        </div>
        ${dayRows}
      </div>
    </div>`;
  }).join("");

  const eChip = c => `<span class="chip ${state.ansEngine===c?"on":""}" onclick="state.ansEngine='${c}'; render();">${c==="All"?"All engines":c}</span>`;
  return `
  <div style="display:flex; flex-direction:column; gap:16px;">
    <div class="card" style="padding:10px 18px; display:flex; align-items:center; gap:8px; flex-wrap:wrap;">
      ${["All","ChatGPT","Claude"].map(eChip).join("")}
      <span style="width:1px; height:18px; background:#e5e7eb; margin:0 6px;"></span>
      <span class="chip ${state.ansOnlyLat?"on":""}" style="${!state.ansOnlyLat?"border-color:#cfe3f8; background:#f4f9fe; color:#0a6fd1;":""}" onclick="state.ansOnlyLat=!state.ansOnlyLat; render();">Only answers mentioning ${esc(YOU)}</span>
      ${selPrompt?`<span class="chip on" onclick="state.ansPrompt=null; render();">Prompt ${selPrompt.id}: ${esc(selPrompt.text.slice(0,40))}… ✕</span>`:""}
      <span class="tiny muted" style="margin-left:auto;">newest first · click a row for the full answer (last ${RAW.answers.filter(a=>a.text).length ? "7 days" : "0 days"} embedded)</span>
    </div>
    ${dayBlocks || '<div class="card" style="padding:24px;">No answers match the current filters.</div>'}
  </div>`;
}

/* ---------- shell ---------- */
function render(){
  D = derive(state.range);
  renderTabs();
  const v = document.getElementById("view");
  if (state.tab==="Briefing")   v.innerHTML = renderBriefing();
  if (state.tab==="Prompts")    v.innerHTML = renderPrompts();
  if (state.tab==="Citations")  v.innerHTML = renderCitations();
  if (state.tab==="AI Answers") v.innerHTML = renderAnswers();
}
render();
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
    # </script> inside answer texts would terminate the data block early
    data_json = json.dumps(data).replace("</", "<\\/")

    html = HTML_TEMPLATE.replace("__BRAND__", settings["brand"]["name"]) \
                        .replace("__DATA__", data_json)

    out = output_path or OUTPUT_PATH
    Path(out).write_text(html, encoding="utf-8")
    print(f"Dashboard written to: {out}")
    print(f"Open it in your browser: open {out}")
