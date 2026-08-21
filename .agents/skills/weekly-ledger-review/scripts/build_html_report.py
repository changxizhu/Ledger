#!/usr/bin/env python3
"""Render a standalone private HTML dashboard from a scored weekly record."""

from __future__ import annotations

from datetime import date, timedelta
from html import escape
from string import Template
from typing import Any


COMPONENTS = (
    ("plan", "Plan completion"),
    ("progress", "Theme progress"),
    ("focus", "Focus time"),
    ("evidence", "Evidence coverage"),
)


def _text(value: Any, fallback: str = "-") -> str:
    if value is None or value == "":
        return fallback
    return escape(str(value))


def _percent(value: Any) -> float:
    try:
        numeric = float(value or 0)
    except (TypeError, ValueError):
        numeric = 0.0
    return max(0.0, min(100.0, numeric))


def _number(value: Any, fallback: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return fallback


def _reference_identifier(reference: dict[str, Any]) -> Any:
    return reference.get("commit") or reference.get("event_id") or "-"


def _date_label(value: date) -> str:
    return f"{value.day} {value.strftime('%b %Y')}"


def _period_label(period: dict[str, Any]) -> str:
    try:
        start = date.fromisoformat(str(period["start"]))
        end = date.fromisoformat(str(period["end"])) - timedelta(days=1)
    except (KeyError, TypeError, ValueError):
        return "Period unavailable"
    if start.year == end.year and start.month == end.month:
        return f"{start.day}-{end.day} {end.strftime('%b %Y')}"
    return f"{_date_label(start)} - {_date_label(end)}"


def _score_state(score: float) -> tuple[str, str, str]:
    if score >= 80:
        return "Strong week", "strong", "Execution and evidence are aligned."
    if score >= 60:
        return "Mixed progress", "steady", "Good movement with visible gaps."
    if score >= 40:
        return "Needs attention", "attention", "Progress exists, but the operating baseline is weak."
    return "At risk", "risk", "Core planning or evidence signals are missing."


def _component_rows(index: dict[str, Any]) -> tuple[str, str, str]:
    components = index.get("components", {})
    weights = index.get("weights", {})
    values = [(key, label, _percent(components.get(key))) for key, label in COMPONENTS]
    strongest = max(values, key=lambda item: item[2])
    weakest = min(values, key=lambda item: item[2])
    rows = []
    for key, label, value in values:
        weight = _number(weights.get(key))
        contribution = value * weight
        context = (
            f"{weight * 100:.0f}% weight &middot; {contribution:.1f} pts"
            if weight
            else "Score component"
        )
        rows.append(
            f"""
            <div class="component-row component-{key}">
              <div class="component-copy">
                <span>{escape(label)}</span>
                <small>{context}</small>
              </div>
              <div class="component-bar" role="img" aria-label="{escape(label)} {value:.1f} out of 100">
                <i style="width:{value:.1f}%"></i>
              </div>
              <strong>{value:.1f}</strong>
            </div>"""
        )
    return "\n".join(rows), strongest[1], weakest[1]


def _theme_tone(progress: float) -> str:
    if progress >= 80:
        return "high"
    if progress >= 60:
        return "medium"
    return "low"


def _theme_section(theme: dict[str, Any], number: int) -> str:
    progress = round(_percent(_number(theme.get("progress")) * 100))
    references = theme.get("evidence_refs", [])
    rows = "\n".join(
        """
          <tr>
            <td><span class="evidence-type">{event_type}</span></td>
            <td>{source}</td>
            <td><code>{identifier}</code></td>
            <td><code>{path}</code></td>
            <td>{reason}</td>
          </tr>""".format(
            event_type=_text(reference.get("event_type")),
            source=_text(reference.get("source")),
            identifier=_text(_reference_identifier(reference)),
            path=_text(reference.get("path")),
            reason=_text(reference.get("reason")),
        )
        for reference in references
    )
    if not rows:
        rows = '<tr><td colspan="5" class="empty-cell">No evidence references recorded.</td></tr>'
    evidence_label = f"{len(references)} evidence reference"
    if len(references) != 1:
        evidence_label += "s"
    return """
      <article class="theme-card tone-{tone}">
        <div class="theme-index">{number:02d}</div>
        <div class="theme-content">
          <div class="theme-heading">
            <div>
              <p class="eyebrow">Work theme</p>
              <h3>{name}</h3>
            </div>
            <div class="theme-progress"><strong>{progress}</strong><span>%</span></div>
          </div>
          <div class="theme-track" role="img" aria-label="Theme progress {progress}%">
            <i style="width:{progress}%"></i>
          </div>
          <p class="theme-summary">{summary}</p>
          <details class="evidence-details">
            <summary>{evidence_label}</summary>
            <div class="table-wrap">
              <table class="evidence-table">
                <thead>
                  <tr>
                    <th>Type</th>
                    <th>Source</th>
                    <th>Reference</th>
                    <th>Path</th>
                    <th>Support</th>
                  </tr>
                </thead>
                <tbody>{rows}</tbody>
              </table>
            </div>
          </details>
        </div>
      </article>""".format(
        tone=_theme_tone(progress),
        number=number,
        name=_text(theme.get("name"), "Unnamed theme"),
        progress=progress,
        summary=_text(theme.get("summary"), "No summary recorded."),
        evidence_label=evidence_label,
        rows=rows,
    )


def _task_list(tasks: list[dict[str, Any]], empty: str, state: str) -> str:
    if not tasks:
        return f'<p class="empty-state">{escape(empty)}</p>'
    return '<ul class="task-list">' + "".join(
        f'<li class="task-{state}"><span></span>{_text(task.get("title"))}</li>'
        for task in tasks
    ) + "</ul>"


def _source_detail(source: dict[str, Any]) -> str:
    details: list[str] = []
    category = source.get("category")
    if category:
        details.append(str(category).replace("_", " ").title())
    if source.get("sync_state"):
        details.append(f"Sync: {source['sync_state']}")
    if source.get("dirty"):
        details.append("Local changes")
    for key, label in (("event_count", "events"), ("item_count", "items"), ("file_count", "files")):
        if key in source:
            details.append(f"{source[key]} {label}")
            break
    return " / ".join(details) or "No source details"


def _source_rows(source_status: list[dict[str, Any]]) -> str:
    rows = []
    allowed = {"available", "stale", "disabled", "error", "unavailable"}
    for source in source_status:
        raw_status = str(source.get("status", "unknown"))
        status_class = raw_status if raw_status in allowed else "unknown"
        rows.append(
            """
            <tr>
              <td><strong>{name}</strong><small>{detail}</small></td>
              <td><span class="status status-{status_class}"><i></i>{status}</span></td>
              <td>{requirement}</td>
            </tr>""".format(
                name=_text(source.get("name"), "Unknown source"),
                detail=_text(_source_detail(source)),
                status_class=status_class,
                status=_text(raw_status.replace("_", " ").title()),
                requirement="Required" if source.get("required") else "Optional",
            )
        )
    if not rows:
        return '<tr><td colspan="3" class="empty-cell">No source status recorded.</td></tr>'
    return "\n".join(rows)


def _reflection_items(reflection: list[dict[str, Any]]) -> str:
    if not reflection:
        return '<p class="empty-state">No reflection recorded.</p>'
    return "\n".join(
        """
        <article class="action-item">
          <div class="action-number">{number:02d}</div>
          <div class="action-body">
            <p class="eyebrow">Issue</p>
            <h3>{issue}</h3>
            <div class="recommendation">
              <strong>Next action</strong>
              <p>{suggestion}</p>
            </div>
            <details>
              <summary>Review evidence and impact</summary>
              <dl>
                <div><dt>Evidence</dt><dd>{evidence}</dd></div>
                <div><dt>Why it matters</dt><dd>{why}</dd></div>
              </dl>
            </details>
          </div>
        </article>""".format(
            number=number,
            issue=_text(item.get("issue"), "Review point"),
            suggestion=_text(item.get("suggestion"), "Not recorded"),
            evidence=_text(item.get("evidence"), "Not recorded"),
            why=_text(item.get("why_it_matters"), "Not recorded"),
        )
        for number, item in enumerate(reflection, start=1)
    )


def render_report(data: dict[str, Any]) -> str:
    index = data["weekly_index"]
    score = _percent(index.get("score"))
    state_label, state_class, state_copy = _score_state(score)
    component_rows, strongest_component, weakest_component = _component_rows(index)
    tasks = data.get("tasks", [])
    themes = data.get("themes", [])
    reflection = data.get("reflection", [])
    source_status = data.get("source_status", [])
    done = [task for task in tasks if task.get("completed")]
    open_tasks = [task for task in tasks if not task.get("completed") and not task.get("cancelled")]
    evidence_count = sum(len(theme.get("evidence_refs", [])) for theme in themes)
    sources_available = sum(source.get("status") == "available" for source in source_status)
    source_warnings = sum(source.get("status") not in {"available", "disabled"} for source in source_status)
    source_coverage = round(sources_available / len(source_status) * 100) if source_status else 0

    theme_sections = "\n".join(_theme_section(theme, number) for number, theme in enumerate(themes, start=1))
    if not theme_sections:
        theme_sections = '<p class="empty-state">No themes recorded.</p>'

    plan_status = data.get("plan_status")
    plan_notice = ""
    if plan_status:
        raw_status = str(plan_status.get("status", "unknown"))
        title = "Planning baseline available"
        if raw_status not in {"available", "ready", "complete"}:
            title = "Planning baseline unavailable"
        plan_notice = """
        <aside class="plan-alert">
          <div><span>Planning signal</span><strong>{title}</strong></div>
          <p>{reason}</p>
        </aside>""".format(title=title, reason=_text(plan_status.get("reason"), "No reason recorded."))

    html = Template("""<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <meta name="robots" content="noindex,nofollow">
    <title>Ledger Weekly Review $week</title>
    <style>
      :root {
        color-scheme: light;
        --canvas: #f3f5f4; --paper: #ffffff; --ink: #18201d; --muted: #65706b;
        --line: #d8dfdb; --line-strong: #bcc8c2; --teal: #147a68; --teal-soft: #e4f2ee;
        --blue: #356fa8; --blue-soft: #e9f0f8; --amber: #b66e0b; --amber-soft: #fff2d9;
        --red: #a9443d; --red-soft: #f8e8e6; --charcoal: #24302b;
      }
      * { box-sizing: border-box; }
      html { scroll-behavior: smooth; }
      body { margin: 0; background: var(--canvas); color: var(--ink); font-family: Inter, ui-sans-serif, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; font-size: 15px; line-height: 1.55; letter-spacing: 0; }
      a { color: inherit; }
      h1, h2, h3, p { margin-top: 0; }
      h1 { margin-bottom: 8px; font-size: 34px; line-height: 1.12; }
      h2 { margin-bottom: 6px; font-size: 22px; line-height: 1.25; }
      h3 { margin-bottom: 8px; font-size: 17px; line-height: 1.35; }
      .top-rule { height: 5px; background: var(--charcoal); }
      .page-header { background: var(--paper); border-bottom: 1px solid var(--line); }
      .header-inner, .page, .page-footer { width: min(1220px, calc(100% - 48px)); margin: 0 auto; }
      .header-inner { min-height: 62px; display: flex; align-items: center; justify-content: space-between; gap: 24px; }
      .brand { display: flex; align-items: baseline; gap: 10px; font-weight: 760; }
      .brand span { color: var(--muted); font-size: 12px; font-weight: 650; }
      nav { display: flex; gap: 22px; }
      nav a { color: var(--muted); font-size: 13px; text-decoration: none; }
      nav a:hover { color: var(--ink); }
      .page { padding: 38px 0 64px; }
      .title-row { display: flex; justify-content: space-between; align-items: end; gap: 24px; margin-bottom: 24px; }
      .eyebrow { margin-bottom: 5px; color: var(--teal); font-size: 11px; font-weight: 760; text-transform: uppercase; letter-spacing: 0; }
      .period { margin: 0; color: var(--muted); }
      .privacy { color: var(--muted); font-size: 12px; white-space: nowrap; }
      .overview-grid { display: grid; grid-template-columns: 330px minmax(0, 1fr); border-top: 1px solid var(--line-strong); border-bottom: 1px solid var(--line-strong); background: var(--paper); }
      .score-panel { display: grid; grid-template-columns: 166px minmax(0, 1fr); align-items: center; gap: 18px; padding: 30px 26px; border-right: 1px solid var(--line); }
      .score-gauge { --score-angle: 0deg; --score-color: var(--amber); width: 156px; aspect-ratio: 1; display: grid; place-items: center; position: relative; border-radius: 50%; background: conic-gradient(var(--score-color) var(--score-angle), #e2e7e4 0); }
      .score-gauge::before { content: ""; position: absolute; inset: 12px; border-radius: 50%; background: var(--paper); }
      .score-value { position: relative; text-align: center; line-height: 1; }
      .score-value strong { display: block; font-size: 42px; font-variant-numeric: tabular-nums; }
      .score-value span { color: var(--muted); font-size: 12px; }
      .score-copy strong { display: block; margin-bottom: 6px; font-size: 18px; }
      .score-copy p { margin: 0; color: var(--muted); font-size: 13px; }
      .score-strong .score-gauge { --score-color: var(--teal); }
      .score-steady .score-gauge { --score-color: var(--blue); }
      .score-attention .score-gauge { --score-color: var(--amber); }
      .score-risk .score-gauge { --score-color: var(--red); }
      .component-panel { padding: 26px 30px; }
      .panel-heading { display: flex; justify-content: space-between; gap: 20px; margin-bottom: 19px; }
      .panel-heading strong { font-size: 14px; }
      .panel-heading span { color: var(--muted); font-size: 12px; }
      .component-row { display: grid; grid-template-columns: 142px minmax(120px, 1fr) 46px; align-items: center; gap: 16px; min-height: 43px; }
      .component-copy span { display: block; font-size: 13px; font-weight: 680; }
      .component-copy small { color: var(--muted); font-size: 11px; }
      .component-bar { height: 9px; overflow: hidden; background: #e4e9e6; border-radius: 4px; }
      .component-bar i { display: block; height: 100%; background: var(--blue); border-radius: 4px; }
      .component-progress .component-bar i { background: var(--teal); }
      .component-focus .component-bar i { background: var(--amber); }
      .component-evidence .component-bar i { background: var(--charcoal); }
      .component-row > strong { text-align: right; font-size: 14px; font-variant-numeric: tabular-nums; }
      .summary-stats { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); background: var(--paper); border-bottom: 1px solid var(--line); }
      .summary-stat { min-width: 0; padding: 17px 22px; border-right: 1px solid var(--line); }
      .summary-stat:last-child { border-right: 0; }
      .summary-stat strong { display: block; font-size: 20px; font-variant-numeric: tabular-nums; }
      .summary-stat span { color: var(--muted); font-size: 12px; }
      .plan-alert { display: grid; grid-template-columns: 245px minmax(0, 1fr); gap: 24px; margin-top: 14px; padding: 15px 18px; background: var(--amber-soft); border-left: 4px solid var(--amber); }
      .plan-alert span { display: block; color: var(--amber); font-size: 11px; font-weight: 760; text-transform: uppercase; }
      .plan-alert strong { font-size: 14px; }
      .plan-alert p { margin: 0; color: #624817; font-size: 13px; }
      .section-block { padding-top: 46px; scroll-margin-top: 16px; }
      .section-heading { display: flex; justify-content: space-between; align-items: end; gap: 24px; margin-bottom: 18px; }
      .section-heading p { margin: 0; color: var(--muted); font-size: 13px; }
      .theme-list { display: grid; gap: 12px; }
      .theme-card { display: grid; grid-template-columns: 54px minmax(0, 1fr); gap: 20px; padding: 23px 24px; background: var(--paper); border: 1px solid var(--line); border-left: 4px solid var(--blue); border-radius: 6px; }
      .theme-card.tone-high { border-left-color: var(--teal); }
      .theme-card.tone-low { border-left-color: var(--amber); }
      .theme-index { color: var(--line-strong); font-size: 25px; font-weight: 760; font-variant-numeric: tabular-nums; }
      .theme-heading { display: flex; justify-content: space-between; align-items: start; gap: 24px; }
      .theme-heading h3 { max-width: 800px; margin-bottom: 0; font-size: 18px; }
      .theme-progress { display: flex; align-items: baseline; color: var(--blue); white-space: nowrap; }
      .tone-high .theme-progress { color: var(--teal); }
      .tone-low .theme-progress { color: var(--amber); }
      .theme-progress strong { font-size: 29px; font-variant-numeric: tabular-nums; }
      .theme-progress span { font-size: 13px; }
      .theme-track { height: 8px; margin: 14px 0 15px; overflow: hidden; background: #e5eae7; border-radius: 4px; }
      .theme-track i { display: block; height: 100%; background: var(--blue); }
      .tone-high .theme-track i { background: var(--teal); }
      .tone-low .theme-track i { background: var(--amber); }
      .theme-summary { max-width: 960px; margin-bottom: 15px; color: #46514c; }
      details { border-top: 1px solid var(--line); }
      summary { padding: 11px 0 0; color: var(--blue); cursor: pointer; font-size: 12px; font-weight: 700; }
      details[open] summary { margin-bottom: 12px; }
      .table-wrap { width: 100%; overflow-x: auto; }
      table { width: 100%; border-collapse: collapse; font-size: 12px; }
      th, td { padding: 10px 11px; border-bottom: 1px solid var(--line); text-align: left; vertical-align: top; }
      th { color: var(--muted); background: #f4f6f5; font-weight: 700; }
      td code { overflow-wrap: anywhere; }
      code { font-family: "SFMono-Regular", Consolas, monospace; font-size: 11px; }
      .evidence-type { color: var(--blue); font-weight: 700; }
      .operations-grid { display: grid; grid-template-columns: minmax(0, 1.2fr) minmax(300px, .8fr); gap: 32px; }
      .source-summary { display: flex; justify-content: space-between; align-items: end; gap: 20px; margin-bottom: 13px; }
      .source-summary strong { font-size: 22px; }
      .source-summary span { color: var(--muted); font-size: 12px; }
      .coverage-track { height: 7px; margin-bottom: 16px; background: #e1e6e3; border-radius: 3px; overflow: hidden; }
      .coverage-track i { display: block; height: 100%; background: var(--teal); }
      .source-table { background: var(--paper); border: 1px solid var(--line); }
      .source-table td:first-child strong, .source-table td:first-child small { display: block; }
      .source-table td:first-child small { margin-top: 2px; color: var(--muted); }
      .status { display: inline-flex; align-items: center; gap: 6px; font-size: 12px; font-weight: 700; }
      .status i { width: 8px; height: 8px; border-radius: 50%; background: var(--muted); }
      .status-available { color: var(--teal); } .status-available i { background: var(--teal); }
      .status-stale { color: var(--amber); } .status-stale i { background: var(--amber); }
      .status-error, .status-unavailable { color: var(--red); }
      .status-error i, .status-unavailable i { background: var(--red); }
      .task-board { display: grid; grid-template-columns: 1fr 1fr; background: var(--paper); border-top: 1px solid var(--line-strong); border-bottom: 1px solid var(--line-strong); }
      .task-column { padding: 20px 22px; }
      .task-column + .task-column { border-left: 1px solid var(--line); }
      .task-column h3 { margin-bottom: 13px; font-size: 14px; }
      .task-list { margin: 0; padding: 0; list-style: none; }
      .task-list li { display: grid; grid-template-columns: 16px minmax(0, 1fr); gap: 9px; padding: 7px 0; }
      .task-list li span { width: 14px; height: 14px; margin-top: 4px; border: 2px solid var(--line-strong); border-radius: 50%; }
      .task-list .task-done span { background: var(--teal); border-color: var(--teal); box-shadow: inset 0 0 0 3px var(--paper); }
      .empty-state { margin: 0; color: var(--muted); font-style: italic; }
      .empty-cell { color: var(--muted); font-style: italic; }
      .action-list { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
      .action-item { display: grid; grid-template-columns: 42px minmax(0, 1fr); gap: 14px; min-width: 0; padding: 20px; background: var(--paper); border: 1px solid var(--line); border-radius: 6px; }
      .action-number { color: var(--red); font-weight: 760; font-variant-numeric: tabular-nums; }
      .action-body h3 { font-size: 16px; }
      .recommendation { margin: 14px 0 13px; padding: 12px 14px; background: var(--blue-soft); border-left: 3px solid var(--blue); }
      .recommendation strong { display: block; margin-bottom: 3px; color: var(--blue); font-size: 11px; text-transform: uppercase; }
      .recommendation p { margin: 0; font-size: 13px; }
      .action-item dl { margin: 0; }
      .action-item dl div { display: grid; grid-template-columns: 112px minmax(0, 1fr); gap: 12px; padding: 7px 0; }
      .action-item dt { color: var(--muted); font-size: 12px; font-weight: 700; }
      .action-item dd { margin: 0; font-size: 13px; }
      .page-footer { padding: 22px 0 36px; color: var(--muted); font-size: 12px; }
      @media (max-width: 900px) {
        .overview-grid { grid-template-columns: 1fr; }
        .score-panel { border-right: 0; border-bottom: 1px solid var(--line); }
        .operations-grid { grid-template-columns: 1fr; }
        .action-list { grid-template-columns: 1fr; }
      }
      @media (max-width: 680px) {
        .header-inner, .page, .page-footer { width: min(100% - 28px, 1220px); }
        .header-inner { align-items: flex-start; flex-direction: column; justify-content: center; padding: 13px 0; }
        nav { width: 100%; gap: 16px; overflow-x: auto; }
        .page { padding-top: 28px; }
        .title-row, .section-heading { align-items: flex-start; flex-direction: column; gap: 10px; }
        h1 { font-size: 29px; }
        .privacy { white-space: normal; }
        .score-panel { grid-template-columns: 132px minmax(0, 1fr); padding: 22px 18px; }
        .score-gauge { width: 124px; }
        .score-value strong { font-size: 34px; }
        .component-panel { padding: 21px 18px; }
        .component-row { grid-template-columns: 118px minmax(80px, 1fr) 40px; gap: 10px; }
        .component-copy small { display: none; }
        .summary-stats { grid-template-columns: 1fr 1fr; }
        .summary-stat:nth-child(2) { border-right: 0; }
        .summary-stat:nth-child(-n+2) { border-bottom: 1px solid var(--line); }
        .plan-alert { grid-template-columns: 1fr; gap: 7px; }
        .theme-card { grid-template-columns: 1fr; gap: 7px; padding: 19px 17px; }
        .theme-index { font-size: 16px; }
        .theme-heading { gap: 14px; }
        .theme-heading h3 { font-size: 16px; }
        .theme-progress strong { font-size: 24px; }
        .task-board { grid-template-columns: 1fr; }
        .task-column + .task-column { border-left: 0; border-top: 1px solid var(--line); }
        .action-item { grid-template-columns: 30px minmax(0, 1fr); padding: 17px 15px; }
        .action-item dl div { grid-template-columns: 1fr; gap: 2px; }
      }
      @media print {
        body { background: var(--paper); font-size: 12px; }
        .top-rule, nav { display: none; }
        .header-inner, .page, .page-footer { width: 100%; }
        .theme-card, .action-item { break-inside: avoid; }
      }
    </style>
  </head>
  <body>
    <div class="top-rule"></div>
    <header class="page-header">
      <div class="header-inner">
        <div class="brand">Ledger <span>Weekly review</span></div>
        <nav aria-label="Report sections"><a href="#overview">Overview</a><a href="#themes">Themes</a><a href="#operations">Evidence</a><a href="#actions">Actions</a></nav>
      </div>
    </header>
    <main class="page">
      <section id="overview">
        <div class="title-row">
          <div><p class="eyebrow">Private weekly brief</p><h1>$week</h1><p class="period">$period</p></div>
          <span class="privacy">Evidence-backed &middot; Local only</span>
        </div>
        <div class="overview-grid score-$state_class">
          <div class="score-panel">
            <div class="score-gauge" style="--score-angle:$score_angle" role="img" aria-label="Weekly index $score out of 100"><div class="score-value"><strong>$score</strong><span>out of 100</span></div></div>
            <div class="score-copy"><strong>$state_label</strong><p>$state_copy</p></div>
          </div>
          <div class="component-panel">
            <div class="panel-heading"><strong>Index components</strong><span>Strongest: $strongest &middot; Gap: $weakest</span></div>
            $component_rows
          </div>
        </div>
        <div class="summary-stats">
          <div class="summary-stat"><strong>$theme_count</strong><span>work themes</span></div>
          <div class="summary-stat"><strong>$evidence_count</strong><span>evidence references</span></div>
          <div class="summary-stat"><strong>$done_count / $task_count</strong><span>tasks completed</span></div>
          <div class="summary-stat"><strong>$source_warnings</strong><span>sources need review</span></div>
        </div>
        $plan_notice
      </section>
      <section id="themes" class="section-block">
        <div class="section-heading"><div><p class="eyebrow">Outcome view</p><h2>Theme progress</h2></div><p>Progress estimates with auditable references.</p></div>
        <div class="theme-list">$theme_sections</div>
      </section>
      <section id="operations" class="section-block">
        <div class="section-heading"><div><p class="eyebrow">Operating signals</p><h2>Evidence and commitments</h2></div><p>Source health and planned work.</p></div>
        <div class="operations-grid">
          <div>
            <div class="source-summary"><div><strong>$sources_available of $source_count</strong><span> sources available</span></div><span>$source_coverage% direct availability</span></div>
            <div class="coverage-track" role="img" aria-label="Source availability $source_coverage%"><i style="width:$source_coverage%"></i></div>
            <div class="table-wrap source-table"><table><thead><tr><th>Source</th><th>Status</th><th>Role</th></tr></thead><tbody>$source_rows</tbody></table></div>
          </div>
          <div class="task-board"><div class="task-column"><h3>Completed</h3>$done_tasks</div><div class="task-column"><h3>Carry forward</h3>$open_tasks</div></div>
        </div>
      </section>
      <section id="actions" class="section-block">
        <div class="section-heading"><div><p class="eyebrow">Review output</p><h2>Actions for next week</h2></div><p>Recommendations first; evidence expands on demand.</p></div>
        <div class="action-list">$reflection_items</div>
      </section>
    </main>
    <footer class="page-footer">Private Ledger artifact. Keep this report with its local evidence bundle.</footer>
  </body>
</html>
""")
    return html.substitute(
        week=_text(data.get("week"), "Unknown week"),
        period=_text(_period_label(data.get("period", {}))),
        score=f"{score:.1f}",
        score_angle=f"{score * 3.6:.1f}deg",
        state_label=state_label,
        state_class=state_class,
        state_copy=state_copy,
        strongest=escape(strongest_component),
        weakest=escape(weakest_component),
        component_rows=component_rows,
        theme_count=len(themes),
        evidence_count=evidence_count,
        done_count=len(done),
        task_count=len([task for task in tasks if not task.get("cancelled")]),
        source_warnings=source_warnings,
        plan_notice=plan_notice,
        theme_sections=theme_sections,
        sources_available=sources_available,
        source_count=len(source_status),
        source_coverage=source_coverage,
        source_rows=_source_rows(source_status),
        done_tasks=_task_list(done, "No completed tasks recorded.", "done"),
        open_tasks=_task_list(open_tasks, "Nothing to carry forward.", "open"),
        reflection_items=_reflection_items(reflection),
    )
