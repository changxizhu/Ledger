---
name: weekly-ledger-review
description: Review a week of personal work using local notes, Git activity, plans, focus time, and exported model interactions. Use when collecting weekly evidence, calculating the weekly index, explaining progress, preparing the next Todoist plan, or producing anonymized dashboard data.
---

# Weekly Ledger Review

Use scripts for facts and arithmetic. Use judgment only for grouping evidence into
themes, interpreting progress, and suggesting the next plan.

## Workflow

1. Confirm the requested week, timezone, source paths, and read permissions.
2. Run `scripts/check_sync.py` before collecting evidence. Never auto-resolve a
   dirty or diverged repository.
3. Run `scripts/collect_git.py` for the exact Monday-to-Monday interval. Treat
   Markdown changes, code changes, and interaction records as evidence, not scores.
4. Import completed and incomplete Todoist tasks plus Calendar focus time when
   those integrations are enabled. Do not duplicate every task in Calendar.
5. Group evidence into 2-5 themes. Preserve exploratory reading as exploration
   unless the evidence clearly connects it to a planned outcome.
6. Prepare a normalized weekly input matching `references/weekly-record.md`.
7. Run `scripts/score_week.py`, then `scripts/build_report.py`.
8. Explain what went well, what did not, why, and one concrete adjustment. Review
   the evidence if a score looks surprising; do not optimize behavior for the score.
9. Update Todoist with the next weekly plan. Use Calendar only for the Monday review
   reminder and genuine time blocks.
10. Publish only aggregate, anonymized fields to any public dashboard.

## Safety

- Never commit raw prompts, note contents, API tokens, or machine-local paths.
- Do not use lines changed or interaction frequency as direct productivity points.
- Keep account aliases in private configuration; report one unified identity.
- Ask before reading sources outside the configured allowlist.

Read `references/weekly-record.md` when preparing or validating the normalized input.

