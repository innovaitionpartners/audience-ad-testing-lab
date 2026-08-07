"""Dashboard-native HTML projection for an audience panel review."""

from __future__ import annotations

from html import escape
from typing import Any, Mapping

from .common import require_url


def _h(value: Any) -> str:
    return escape(str(value), quote=True)


def _items(values: Any, empty: str) -> str:
    if not isinstance(values, list) or not values:
        return f'<li class="empty">{_h(empty)}</li>'
    return "".join(f"<li>{_h(value)}</li>" for value in values)


def _chips(values: Any, empty: str = "None documented") -> str:
    if not isinstance(values, list) or not values:
        return f'<span class="chip muted-chip">{_h(empty)}</span>'
    return "".join(f'<span class="chip">{_h(value)}</span>' for value in values)


def render_dashboard_panel_review_html(
    *,
    brief: Mapping[str, Any],
    panel: Mapping[str, Any],
    counts: Mapping[str, int | None],
    specificity: Mapping[str, Any],
    full_record_html: str,
    source_links: Mapping[str, str] | None = None,
) -> str:
    """Render the dashboard visual system around the complete record."""

    research = panel["persona_research"]
    provisional = research["status"] == "provisional_no_research"
    coverage_width = {"strong": 100, "moderate": 72, "thin": 42, "empty": 8}
    coverage_rows = "".join(
        f'<div class="coverage-row"><div class="coverage-label"><span>{_h(key.replace("_", " ").title())}</span><strong>{_h(value)}</strong></div><div class="coverage-track"><span style="width:{coverage_width.get(str(value), 8)}%"></span></div></div>'
        for key, value in research["coverage"].items()
    )

    gaps = research["evidence_gaps"]
    gap_cards = (
        "".join(
            f'<article class="gap-card"><h3>{_h(gap["gap"])}</h3><p><strong>Impact</strong> · {_h(gap["impact_on_panel"])}</p><p><strong>Mitigation</strong> · {_h(gap["mitigation"])}</p></article>'
            for gap in gaps
        )
        if gaps
        else '<article class="gap-card"><h3>No documented evidence gaps</h3><p>The brief records no additional gap statements.</p></article>'
    )

    segment_cards = "".join(
        f'''<article class="segment-card">
          <div class="card-kicker">{_h(segment["segment_id"])}</div>
          <h3>{_h(segment["name"])}</h3><p>{_h(segment["description"])}</p>
          <div class="mini-grid">
            <div><h4>Primary needs</h4><ul>{_items(segment["primary_needs"], "Unknown")}</ul></div>
            <div><h4>Primary objections</h4><ul>{_items(segment["primary_objections"], "Unknown")}</ul></div>
            <div><h4>Creative implications</h4><ul>{_items(segment["creative_implications"], "Unknown")}</ul></div>
          </div>
          <div class="card-meta"><span>{_h(segment["origin"])}</span><span>{segment["study_weight"]:.3f} planning weight</span></div>
        </article>'''
        for segment in panel["segments"]
    )

    archetypes = {
        item["persona_archetype_id"]: item
        for item in panel["persona_archetypes"]
    }
    specificity_by_id = {
        row["persona_archetype_id"]: row for row in specificity["profiles"]
    }
    profile_cards: list[str] = []
    for profile in panel["grounded_context_profiles"]:
        archetype = archetypes[profile["persona_archetype_id"]]
        snapshot = profile["profile_snapshot"]
        audit_row = specificity_by_id.get(profile["persona_archetype_id"], {})
        exception = audit_row.get("exception")
        exception_html = (
            f'<div class="exception"><strong>Evidence-specificity exception</strong><p>{_h(exception)}</p></div>'
            if exception
            else ""
        )
        provenance = "".join(
            f'<div class="provenance-row"><span>{_h(row["attribute"])}</span><span>{_h(row["value"])}</span><code>{_h(row["status"])}</code></div>'
            for row in profile["context_attribute_provenance"]
        )
        profile_cards.append(
            f'''<article class="profile-card">
              <div class="profile-head"><div><div class="card-kicker">{_h(profile["segment_id"])}</div><h3>{_h(archetype["display_name"])}</h3></div><span class="strength">{_h(archetype["evidence_strength"])} evidence</span></div>
              <p><strong>Role context</strong> · {_h(snapshot["role_context"])}</p>
              <p><strong>Buying situation</strong> · {_h(snapshot["decision_context"])}</p>
              <div class="profile-columns">
                <div><h4>Motivations</h4><ul>{_items(snapshot["motivations"], "Unknown")}</ul></div>
                <div><h4>Concerns</h4><ul>{_items(snapshot["anxieties"], "Unknown")}</ul></div>
                <div><h4>Proof needs</h4><ul>{_items(snapshot["proof_needs"], "Unknown")}</ul></div>
              </div>
              <div class="inference"><strong>Inference boundary</strong><p>{_h(archetype["inference_boundary"])}</p></div>
              {exception_html}
              <details><summary>Attribute provenance</summary>{provenance}</details>
            </article>'''
        )

    source_cards: list[str] = []
    for index, source in enumerate(brief["evidence_sources"]):
        raw_url = source["source_url"] or (source_links or {}).get(
            source["evidence_id"]
        )
        url = (
            require_url(raw_url, f"brief.evidence_sources[{index}].source_url")
            if raw_url not in (None, "")
            else None
        )
        source_heading = (
            f'<a href="{_h(url)}" target="_blank" rel="noreferrer">{_h(source["source_label"])} <span aria-hidden="true">↗</span></a>'
            if url
            else f'{_h(source["source_label"])} <span class="missing-link">Link not recorded</span>'
        )
        source_url = (
            f'<a class="source-url" href="{_h(url)}" target="_blank" rel="noreferrer">{_h(url)}</a>'
            if url
            else '<span class="source-url missing-link">No direct URL recorded in the approved brief.</span>'
        )
        source_cards.append(
            f'''<article class="source-card" id="source-{_h(source["evidence_id"])}">
              <div class="source-top"><span class="source-type">{_h(str(source["type"]).replace("_", " "))}</span><span class="confidence">{_h(source["confidence"])} confidence</span></div>
              <h3>{source_heading}</h3>
              <div class="source-meta"><code>{_h(source["evidence_id"])}</code><span>{_h(source["date"])}</span><span>{_h(str(source["collection_method"]).replace("_", " "))}</span></div>
              <p class="source-limit"><strong>Limits</strong> · {_h(source["limits"])}</p>
              <div class="source-group"><strong>Usable for</strong><div class="chips">{_chips(source["usable_for"])}</div></div>
              <div class="source-group"><strong>Permitted uses</strong><div class="chips">{_chips(source["permitted_uses"])}</div></div>
              {source_url}
            </article>'''
        )
    source_directory = (
        "".join(source_cards)
        if source_cards
        else '<div class="empty-state"><strong>No research sources</strong><p>This provisional panel has no evidence records or source links.</p></div>'
    )

    tier = panel.get("panel_tier", "tier_1")
    status_label = (
        "Provisional · no research"
        if provisional
        else f"Research approved · {str(tier).replace('_', ' ')} review"
    )
    hero_title = (
        "Planning input without research support"
        if provisional
        else "Built for directional creative review"
    )
    hero_copy = (
        "No research sources or findings support these planning profiles. Unknown fields remain visibly unknown."
        if provisional
        else f"{len(brief['evidence_sources'])} documented sources support {len(brief['findings'])} findings. Use the panel to stress-test creative hypotheses within its recorded boundaries."
    )
    planning_note = (
        "Profile allocations are provisional planning inputs, not evidence-backed prevalence."
        if provisional
        else "Weights are planning allocations, not population prevalence, unless a bound v3 frame explicitly says otherwise."
    )
    source_count_label = (
        "source" if len(brief["evidence_sources"]) == 1 else "sources"
    )
    finding_count_label = "finding" if len(brief["findings"]) == 1 else "findings"
    coverage_copy = (
        "No research was supplied; all seven decision areas remain explicitly empty."
        if provisional
        else "What the approved research could support across seven decision areas."
    )
    segment_label = "segment" if counts["audience_groups"] == 1 else "segments"
    profile_label = "profile" if counts["reusable_profiles"] == 1 else "profiles"
    body_class = "provisional" if provisional else "approved-research"

    css = """
:root{--ip-white:#fff;--ip-black:#111;--ip-blue:#4A63F5;--ip-blue-soft:#93b4ff;--ip-blue-link:#0000ee;--ip-pale-blue:#f1f4ff;--ip-border-soft:#e6e6ee;--ip-text-muted:#6d6d82;--ip-surface-2:#f7f7fa;--ip-mint:#CCFBF1;--ip-mint-dark:#0F766E;--ip-warn:#fff1cc;--font-serif:"DM Serif Display",Georgia,"Times New Roman",serif;--font-sans:"Instrument Sans",system-ui,-apple-system,"Segoe UI",sans-serif;--shadow-card:0 1px 2px rgba(17,17,17,.04),0 8px 24px rgba(17,17,17,.06);--shadow-lift:0 2px 4px rgba(17,17,17,.05),0 16px 40px rgba(17,17,17,.10)}
*{box-sizing:border-box}html{scroll-behavior:smooth}body{margin:0;background:var(--ip-pale-blue);color:var(--ip-black);font:17px/1.62 var(--font-sans);-webkit-font-smoothing:antialiased;text-rendering:optimizeLegibility}a{color:var(--ip-blue-link);text-decoration-thickness:1.5px;text-underline-offset:3px}a:hover{text-decoration-thickness:2.5px}a:focus-visible,summary:focus-visible{outline:3px solid var(--ip-blue-soft);outline-offset:3px;border-radius:3px}.shell{max-width:1440px;margin:24px auto 48px;background:var(--ip-surface-2);border-radius:10px;box-shadow:var(--shadow-lift);overflow:hidden}.brandbar{min-height:76px;background:#000;color:#fff;padding:18px 38px;display:flex;align-items:center;gap:18px}.brand{font-weight:650;font-size:16px;letter-spacing:.01em}.brand span{color:var(--ip-blue-soft)}.brand-divider{width:1px;height:24px;background:rgba(255,255,255,.24)}.product{font-size:15px;color:rgba(255,255,255,.74)}.status{margin-left:auto;display:inline-flex;align-items:center;gap:8px;padding:7px 13px;border:1px solid rgba(255,255,255,.18);border-radius:100px;background:rgba(255,255,255,.08);font-size:13px;letter-spacing:.06em;text-transform:uppercase;color:var(--ip-mint)}.status::before{content:"";width:7px;height:7px;border-radius:50%;background:currentColor}main{max-width:1360px;margin:0 auto;padding:42px 38px 72px}.page-head{display:flex;align-items:flex-end;justify-content:space-between;gap:28px;margin-bottom:26px}.eyebrow,.card-kicker{margin:0 0 7px;font-size:13px;letter-spacing:.13em;text-transform:uppercase;color:var(--ip-text-muted);font-weight:650}h1,h2,h3{font-family:var(--font-serif);font-weight:400}.page-head h1{margin:0;font-size:clamp(44px,5vw,64px);line-height:1.02;letter-spacing:-.025em}.page-context{max-width:440px;margin:0;font-size:16px;line-height:1.55}.hero-grid{display:grid;grid-template-columns:7fr 5fr;gap:22px}.hero{position:relative;overflow:hidden;background:var(--ip-blue);color:#fff;border-radius:10px;padding:30px;box-shadow:var(--shadow-lift)}.hero::before,.hero::after{content:"";position:absolute;border-radius:50%;background:rgba(255,255,255,.07)}.hero::before{width:320px;height:320px;right:-110px;top:-120px}.hero::after{width:190px;height:190px;right:-34px;top:-46px}.hero>*{position:relative}.hero .eyebrow{color:#fff}.hero h2{margin:10px 0 12px;font-size:40px;line-height:1.07;letter-spacing:-.02em}.hero-copy{max-width:62ch;margin:0 0 24px;color:rgba(255,255,255,.84);font-size:17px}.stat-grid{display:grid;grid-template-columns:repeat(4,1fr);gap:11px}.stat{padding:17px 18px;border:1px solid rgba(255,255,255,.24);border-radius:10px;background:rgba(255,255,255,.15)}.stat strong{display:block;font:34px/1 var(--font-serif)}.stat span{display:block;margin-top:7px;color:rgba(255,255,255,.8);font-size:14px;line-height:1.35}.planning-note{margin-top:18px;padding:15px 17px;border-radius:10px;background:var(--ip-mint);color:var(--ip-black);font-size:15px}.coverage-card{background:#fff;border-radius:10px;padding:27px 28px;box-shadow:var(--shadow-card)}.coverage-card h2,.section-title h2{margin:0;font-size:31px;line-height:1.15;letter-spacing:-.01em}.coverage-card>p,.section-title p{margin:6px 0 22px;color:var(--ip-text-muted)}.coverage-row{margin:0 0 14px}.coverage-label{display:flex;justify-content:space-between;gap:14px;margin-bottom:6px;font-size:15px}.coverage-label strong{color:var(--ip-blue);font-size:14px}.coverage-track{height:10px;border-radius:100px;background:var(--ip-surface-2);overflow:hidden}.coverage-track span{display:block;height:100%;border-radius:inherit;background:var(--ip-blue)}.section{margin-top:48px}.section-title{display:flex;align-items:end;justify-content:space-between;gap:24px;margin-bottom:20px}.section-title p{max-width:620px;margin:0;font-size:16px}.gap-grid,.segment-grid,.source-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:18px}.gap-card,.segment-card,.profile-card,.source-card,.record-card{background:#fff;border-radius:10px;box-shadow:var(--shadow-card)}.gap-card{padding:22px 24px;border-left:5px solid var(--ip-blue)}.gap-card h3{margin:0 0 10px;font-size:22px}.gap-card p{margin:7px 0;font-size:15px}.segment-grid{grid-template-columns:repeat(3,minmax(0,1fr))}.segment-card{padding:24px;display:flex;flex-direction:column}.segment-card h3,.profile-card h3,.source-card h3{margin:0 0 10px;font-size:24px;line-height:1.15}.segment-card>p,.profile-card>p{margin:0 0 14px;font-size:15.5px}.mini-grid{display:grid;gap:14px;margin-top:6px}.mini-grid h4,.profile-columns h4{margin:0 0 5px;font-size:14px}ul{margin:0;padding-left:19px}li{margin:4px 0}.mini-grid ul,.profile-columns ul{font-size:15px;line-height:1.5}.empty{color:var(--ip-text-muted);font-style:italic}.card-meta{display:flex;justify-content:space-between;gap:12px;margin-top:auto;padding-top:18px;font-size:13px;color:var(--ip-text-muted)}.profile-stack{display:grid;gap:18px}.profile-card{padding:26px}.profile-head{display:flex;justify-content:space-between;gap:18px;align-items:start}.strength,.source-type,.confidence{display:inline-flex;padding:5px 9px;border-radius:100px;font-size:12.5px;text-transform:uppercase;letter-spacing:.06em;font-weight:650}.strength,.source-type{background:var(--ip-pale-blue);color:var(--ip-blue)}.confidence{background:var(--ip-mint);color:var(--ip-mint-dark)}.profile-columns{display:grid;grid-template-columns:repeat(3,1fr);gap:22px;padding:18px 0;margin:4px 0;border-top:1px solid var(--ip-border-soft);border-bottom:1px solid var(--ip-border-soft)}.inference{margin-top:16px;padding:15px 17px;border-left:3px solid var(--ip-blue);background:var(--ip-pale-blue);font-size:15px}.inference p,.exception p{margin:4px 0 0}.exception{margin-top:12px;padding:16px 18px;border-radius:8px;background:var(--ip-mint);font-size:15px}details{margin-top:16px;border-top:1px solid var(--ip-border-soft);padding-top:13px}summary{cursor:pointer;font-weight:650;font-size:15px}.provenance-row{display:grid;grid-template-columns:180px 1fr auto;gap:16px;padding:10px 0;border-bottom:1px solid var(--ip-border-soft);font-size:14px}.boundary-grid{display:grid;grid-template-columns:1fr 1fr;gap:18px}.boundary{padding:26px;border-radius:10px}.boundary.allowed{background:var(--ip-mint)}.boundary.excluded{background:#000;color:#fff}.boundary h3{margin:0 0 13px;font-size:27px}.boundary ul{font-size:15.5px}.source-card{padding:23px 24px;min-width:0}.source-top{display:flex;justify-content:space-between;gap:10px;margin-bottom:13px}.source-card h3 a{color:var(--ip-black);text-decoration-color:var(--ip-blue)}.source-meta{display:flex;flex-wrap:wrap;gap:8px 15px;margin:0 0 14px;font-size:13.5px;color:var(--ip-text-muted)}.source-limit{margin:0 0 15px;font-size:15px}.source-group{margin-top:12px}.source-group>strong{display:block;margin-bottom:7px;font-size:13px;text-transform:uppercase;letter-spacing:.08em}.chips{display:flex;flex-wrap:wrap;gap:7px}.chip{display:inline-flex;padding:5px 9px;border-radius:100px;background:var(--ip-pale-blue);font-size:13px;line-height:1.35}.muted-chip{background:var(--ip-surface-2);color:var(--ip-text-muted)}.source-url{display:block;margin-top:16px;overflow-wrap:anywhere;font:13px/1.45 ui-monospace,SFMono-Regular,Menlo,monospace}.empty-state{padding:28px;background:#fff;border-radius:10px;box-shadow:var(--shadow-card)}.empty-state p{margin:5px 0 0}.record-intro{display:flex;justify-content:space-between;gap:24px;align-items:end;padding-bottom:18px;border-bottom:8px solid #000}.record-intro p{max-width:620px;margin:0;color:var(--ip-text-muted)}.record-card{padding:28px;margin-top:20px}.full-record h1{display:none}.full-record h2{margin:42px 0 16px;padding:12px 16px;background:#000;color:#fff;border-radius:4px;font-size:28px;line-height:1.18}.full-record h3{margin:30px 0 10px;font-size:24px;color:var(--ip-black)}.full-record h4{margin:22px 0 8px;font-size:16px}.full-record p,.full-record li{font-size:16px;max-width:92ch}.full-record aside{padding:16px 18px;margin:16px 0;border-left:5px solid var(--ip-blue);background:var(--ip-pale-blue)}.provisional .full-record aside{border-color:#b7791f;background:var(--ip-warn)}code{font:13.5px/1.5 ui-monospace,SFMono-Regular,Menlo,monospace;overflow-wrap:anywhere}.table-wrap{overflow:auto;margin:15px 0 25px;border:1px solid var(--ip-border-soft);border-radius:8px}table{width:100%;min-width:760px;border-collapse:collapse;background:#fff}th,td{padding:13px 14px;text-align:left;vertical-align:top;border-bottom:1px solid var(--ip-border-soft);font-size:14.5px;line-height:1.5}th{background:#000;color:#fff;font-size:13px;letter-spacing:.04em;text-transform:uppercase}tr:last-child td{border-bottom:0}.footer{padding:24px 38px;background:#000;color:rgba(255,255,255,.68);font-size:14px}
@media(max-width:980px){.hero-grid,.boundary-grid{grid-template-columns:1fr}.segment-grid{grid-template-columns:1fr 1fr}.stat-grid{grid-template-columns:repeat(2,1fr)}.page-head,.section-title,.record-intro{align-items:start;flex-direction:column}}@media(max-width:680px){body{font-size:16px}.shell{margin:0;border-radius:0}.brandbar,main,.footer{padding-left:20px;padding-right:20px}.brand-divider,.product{display:none}.status{font-size:11px}.page-head h1{font-size:42px}.hero h2{font-size:32px}.hero,.coverage-card,.profile-card,.record-card{padding:22px}.gap-grid,.segment-grid,.source-grid,.profile-columns{grid-template-columns:1fr}.provenance-row{grid-template-columns:1fr}}@media print{body{background:#fff}.shell{margin:0;box-shadow:none}.brandbar,.footer{background:#000!important;-webkit-print-color-adjust:exact;print-color-adjust:exact}.hero,.boundary.excluded,.full-record h2{-webkit-print-color-adjust:exact;print-color-adjust:exact}}
.missing-link{display:inline-block;margin-left:7px;color:var(--ip-text-muted);font:600 12px/1.35 var(--font-sans);letter-spacing:.04em;text-transform:uppercase}
"""

    return f'''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{_h(panel["panel_name"])} panel review</title><link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin><link href="https://fonts.googleapis.com/css2?family=DM+Serif+Display:ital@0;1&amp;family=Instrument+Sans:ital,wght@0,400..700;1,400..700&amp;display=swap" rel="stylesheet"><style>{css}</style></head>
<body class="{body_class}"><div class="shell">
<header class="brandbar"><div class="brand">Innov<span>AI</span>tion Partners</div><div class="brand-divider"></div><div class="product">Audience Ad Testing Lab</div><div class="status">{_h(status_label)}</div></header>
<main>
  <header class="page-head"><div><p class="eyebrow">Audience panel · v{_h(panel["version"])}</p><h1>Who this panel represents</h1></div><p class="page-context">{_h(panel["panel_name"])} · {_h(panel["audience_scope"]["category"])}. {counts["audience_groups"]} {segment_label} and {counts["reusable_profiles"]} {"planning" if provisional else "grounded"} {profile_label}.</p></header>
  <section class="hero-grid"><div class="hero"><p class="eyebrow">{_h(status_label)}</p><h2>{_h(hero_title)}</h2><p class="hero-copy">{_h(hero_copy)}</p><div class="stat-grid"><div class="stat"><strong>{counts["audience_groups"]}</strong><span>{segment_label}</span></div><div class="stat"><strong>{counts["reusable_profiles"]}</strong><span>{"planning" if provisional else "grounded"} {profile_label}</span></div><div class="stat"><strong>{len(brief["evidence_sources"])}</strong><span>{source_count_label}</span></div><div class="stat"><strong>{len(brief["findings"])}</strong><span>{finding_count_label}</span></div></div><div class="planning-note"><strong>Read the weights carefully.</strong> {_h(planning_note)}</div></div><div class="coverage-card"><h2>Evidence coverage</h2><p>{_h(coverage_copy)}</p>{coverage_rows}</div></section>
  <section class="section"><div class="section-title"><div><p class="eyebrow">Known limits</p><h2>What the research could not establish</h2></div><p>Gaps remain visible so a plausible profile is never mistaken for observed prevalence or predicted response.</p></div><div class="gap-grid">{gap_cards}</div></section>
  <section class="section"><div class="section-title"><div><p class="eyebrow">Audience structure</p><h2>The segments</h2></div><p>Each segment records why it exists, what it needs, what it resists, and what that means for creative.</p></div><div class="segment-grid">{segment_cards}</div></section>
  <section class="section"><div class="section-title"><div><p class="eyebrow">Decision contexts</p><h2>{"Planning profiles" if provisional else "Reusable grounded profiles"}</h2></div><p>Each profile combines one archetype with one buying situation. Exceptions are shown where omnibus evidence cannot independently establish the distinction.</p></div><div class="profile-stack">{"".join(profile_cards)}</div></section>
  <section class="section"><div class="section-title"><div><p class="eyebrow">Governance</p><h2>What this panel may and may not support</h2></div></div><div class="boundary-grid"><div class="boundary allowed"><h3>Allowed uses</h3><ul>{_items(panel["governance"]["allowed_uses"], "No allowed uses recorded")}</ul></div><div class="boundary excluded"><h3>Excluded uses</h3><ul>{_items(panel["governance"]["excluded_uses"], "No excluded uses recorded")}</ul></div></div></section>
  <section class="section" id="research-sources"><div class="section-title"><div><p class="eyebrow">Source directory · {len(brief["evidence_sources"])} records</p><h2>Research sources</h2></div><p>Every approved source appears here with a direct link, evidence ID, permitted uses, and its documented limits.</p></div><div class="source-grid">{source_directory}</div></section>
  <section class="section"><div class="record-intro"><div><p class="eyebrow">Canonical projection</p><h2>Full panel record</h2></div><p>Everything above is a reading of what follows. The full record retains every canonical field, provenance status, evidence ID, inference boundary, source link, governance rule, refresh condition, and replicate constraint.</p></div><div class="record-card full-record">{full_record_html}</div></section>
</main><footer class="footer"><strong>Canonical source:</strong> saved-audience-panel.json · Review projection generated for exact manifest binding.</footer></div></body></html>'''
