---
name: weekly-ledger-review
description:
  Review a week of personal work using local notes, Git activity, Linear plans, and exported model
  interactions (i.e., codex and Claude Code). Use when collecting weekly evidence, calculating the
  weekly index, explaining progress, preparing the next Linear cycle, or producing anonymized
  dashboard data.
---

# Weekly Ledger Review

Use scripts for facts and arithmetic. Use judgment only for grouping evidence into themes, interpreting progress, and suggesting the next plan.

## Workflow

1. Confirm the ISO week and use the private `ledger.toml` profile. Run
   `python3 scripts/validate_config.py --config ledger.toml`.
2. Run `python3 scripts/sync_repositories.py --config ledger.toml` to fetch and inspect current upstream state without changing a checkout. Use `--apply` only after reviewing the result; it can only fast-forward a clean repository.
3. If Linear or Calendar is enabled, inspect each integration's `auth_mode`. For `mcp`, use the main
   coordinator's authorized connector to read the exact Monday-to-Monday interval and save the
   approved private snapshot, including account metadata when `expected_user` is configured. For
   `direct`, run:

   ```bash
   python3 .agents/skills/weekly-ledger-review/scripts/refresh_connectors.py \
     --config ledger.toml --week YYYY-Www
   ```

   credentials must be external `env:` or `keychain:` references and the script must verify
   `expected_user`. Subagents never call connectors or receive secrets.

4. Run:

   ```bash
   python3 .agents/skills/weekly-ledger-review/scripts/run_weekly.py collect \
     --config ledger.toml --week YYYY-Www --fetch
   ```

   For unattended direct-mode collection, step 3 and 4 may be combined by adding `--refresh-direct`.
   Never auto-resolve a dirty, behind, or diverged repository. The command prints the timestamped
   run directory and creates both `evidence.json` and the human-readable `evidence-review.md` table.

5. Show `evidence-review.md` in the Codex dialog and pause for the user's evidence verification.
   Record corrections or exclusions and do not start theme analysis until the user approves the
   evidence scope. The private report includes collapsible Git patches for file-level verification;
   interaction text remains only in `evidence.json`.
6. Delegate to `evidence_collector`. Give it only that run's `evidence.json`. Ask for a factual
   inventory and missing-source report.
7. Delegate to `theme_analyst`. Give it the evidence bundle, collector result, and task snapshot.
   Ask for 2-5 themes, outcome progress, uncertainty, reflections, and a proposed next plan.
   Require every theme to include structured `evidence_refs` copied from the exact evidence
   locators. Set `evidence_count` to the number of unique references; finalization rejects missing,
   duplicate, or invented references.
   Preserve exploratory reading as exploration unless evidence connects it to an outcome.
8. As the main coordinator, create `weekly-input.json` in the run directory using
   `references/weekly-record.md`. Preserve source statuses. Missing evidence is unknown, never zero.
9. Run:

   ```bash
   python3 .agents/skills/weekly-ledger-review/scripts/run_weekly.py finalize \
     --config ledger.toml --run-dir RUN
   ```

   Finalization validates the reviewed input and creates the score, report, proposals, and
   anonymized public export. It does not call external services.

10. Delegate to `review_auditor`. Give it only the run artifacts. Resolve any unsupported claim,
    calculation error, privacy leak, or missing-data error, then finalize again if the reviewed
    input changed.
11. Show the report and proposals to the user. Only the main coordinator may apply approved Linear
    changes or the single Monday Calendar reminder. Dry runs stop before this step.
12. Publish only `public-week.json`, never raw evidence or private reports.

## Safety

- Never commit raw prompts, note contents, API tokens, raw credentials, or machine-local paths.
  Configuration may contain only credential references.
- Do not use lines changed or interaction frequency as direct productivity points.
- Keep account aliases in private configuration; report one unified identity.
- Ask before reading sources outside the configured allowlist.
- Never let a subagent perform connector writes. MCP credentials remain in Codex. Direct credentials
  remain in environment variables or macOS Keychain and are not copied into Ledger files.

Read `references/weekly-record.md` when preparing or validating the normalized input.
