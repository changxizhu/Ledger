# Ledger

Ledger is a personal review system built from small Codex skills and deterministic
Python scripts. It gathers evidence from notes, Git repositories, plans, time
records, and exported model interactions, then produces weekly and monthly reviews.

## Design

- **Skills decide:** classify evidence into themes, explain progress, and prepare plans.
- **Scripts measure:** synchronize repositories, collect Git facts, calculate scores,
  and render reports.
- **Todoist plans:** weekly tasks and long-term goals are the planning source of truth.
- **Calendar reserves time:** it contains review reminders and real time blocks, not a
  duplicate task list.
- **Private by default:** raw notes, prompts, paths, and tokens stay local. A future
  public dashboard receives aggregated, anonymized data only.

Code volume and model-message counts are evidence, not productivity. The weekly
index is based on planned outcomes, progress, time, and evidence coverage.

## Repository layout

```text
.agents/skills/                 Codex-discoverable weekly and monthly skills
.codex/agents/                 project-scoped subagent roles
.codex/hooks.json              project interaction capture
config/                        local source and global hook examples
hooks/                         hook commands
scripts/                       repeatable collection and scoring tools
tests/                         deterministic checks
```

Generated private data belongs in `.ledger/` and is ignored by Git. Do not commit
API tokens, raw conversations, note contents, or machine-specific configuration.

## Try the local pipeline

```bash
python3 scripts/score_week.py examples/weekly-input.json \
  --output .ledger/weekly-scored.json
python3 scripts/build_report.py .ledger/weekly-scored.json \
  --output .ledger/weekly-report.md
python3 -m unittest discover -s tests -v
```

## Use it in Codex

1. Open `/Users/changxi/Coding/Ledger` as the Codex project and restart the
   project once so its `.codex` configuration is loaded.
2. Review and trust the project hooks when Codex prompts. In the CLI, use
   `/hooks`; connected MCP tools can be inspected with `/mcp`.
3. Copy `config/ledger.example.toml` to the ignored `ledger.toml`, then add the
   allowed repositories and all Git name/email aliases that identify you.
4. Invoke `$weekly-ledger-review` and ask it to use the `evidence_collector`,
   `theme_analyst`, and `review_auditor` subagents.
5. Project conversations are captured privately in
   `.ledger/interactions.jsonl`. This file is ignored by Git.
6. Connect Todoist and Google Calendar in the Codex/ChatGPT plugin settings.
   These connectors are the MCP layer; credentials do not belong in Ledger.

The project hook only sees conversations opened inside Ledger. To collect Codex
activity from other repositories, merge `config/codex-hooks.global.example.json`
into `~/.codex/hooks.json`, review it with `/hooks`, and restart Codex. Full
conversation text is stored locally because `--include-content` is enabled;
remove that flag to retain counts and hashes only.

To collect repository activity, copy `config/ledger.example.toml` to a private
location, update its repository paths, then run:

```bash
python3 scripts/collect_git.py --config /path/to/ledger.toml \
  --start 2026-08-17 --end 2026-08-24 --output .ledger/git-events.json
```

## Implementation order

1. Validate the local Git and Markdown pipeline with real repositories.
2. Add Todoist task import and Calendar time import.
3. Add explicit Codex and Claude export adapters; hooks may append metadata to a
   local inbox, but scripts remain the canonical readers.
4. Publish only anonymized weekly aggregates to a separate GitHub Pages dashboard.
