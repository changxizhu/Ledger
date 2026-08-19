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
config/                         local source configuration example
examples/                       anonymous inputs for testing
scripts/                        repeatable collection and scoring tools
skills/weekly-ledger-review/    weekly orchestration instructions
skills/monthly-ledger-review/   monthly trend and goal review instructions
tests/                          checks for the deterministic calculations
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
