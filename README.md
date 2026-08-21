# Ledger

Ledger is a personal review system built from small Codex skills and deterministic Python scripts.
It gathers evidence from notes, Git repositories, plans, time records, and exported model
interactions, then produces weekly and monthly reviews.

## Quick start

Run the complete weekly review on the main computer:

```bash
git clone https://github.com/changxizhu/Ledger.git
cd Ledger
python3 -m pip install -r requirements.txt
```

Create the ignored `ledger.toml` from the
[Handbook configuration view](docs/architecture.html#configuration), then validate it:

```bash
python3 scripts/validate_config.py --config ledger.toml
```

Then open the repository in Codex and enter:

```text
$weekly-ledger-review Review 2026-W34
```

Review the generated `evidence-review.md` when Codex pauses, then approve the evidence or identify
what must be corrected. After approval, Codex performs theme analysis, auditing, scoring, and HTML
report generation. The final report is stored at
`.ledger/runs/YYYY-Www/YYYY-MM-week-N.html`.

On every secondary computer, install only the interaction exporter; cloning this repository is not
required. Because Ledger is private, authenticate the GitHub CLI once and download the release
asset through it:

```bash
python3 -m pip install --user pipx
python3 -m pipx ensurepath
gh auth login
download_dir="$(mktemp -d)"
gh release download exporter-v0.1.1 --repo changxizhu/Ledger \
  --pattern "ledger_exporter-0.1.1-py3-none-any.whl" \
  --dir "$download_dir"
pipx install --force "$download_dir/ledger_exporter-0.1.1-py3-none-any.whl"
ledger-export setup
ledger-export run
ledger-export schedule install
ledger-export status
```

The main computer reads the resulting private JSONL files after Google Drive Desktop synchronizes
them. It does not need to contact the secondary computers.

## Design

- **Skills decide:** classify evidence into themes, explain progress, and prepare plans.
- **Scripts measure:** synchronize repositories, collect Git facts, calculate scores, and render
  reports.
- **Linear plans:** weekly cycles, work themes, and long-term goals are the planning source of
  truth.
- **Calendar reserves time:** it contains review reminders and real time blocks, not a duplicate
  task list.
- **Private by default:** raw notes, prompts, paths, and credentials stay outside the repository. A
  future public dashboard receives aggregated, anonymized data only.

Code volume and model-message counts are evidence, not productivity. The weekly index is based on
planned outcomes, progress, time, and evidence coverage.

## Repository layout

```text
.agents/skills/                 Codex skills and their skill-specific scripts
.codex/agents/                 project-scoped subagent roles
scripts/                       shared configuration, source, and sync utilities
schemas/                       private evidence and reviewed-input contracts
tests/                         deterministic checks
```

## Documentation

- [Ledger Handbook](docs/architecture.html): the unified interactive guide for architecture,
  workflow, modules, configuration, identity, permissions, storage, and operations.

The HTML handbook is the single maintained documentation source.

Generated private data belongs in `.ledger/` and is ignored by Git. Do not commit API tokens, raw
conversations, note contents, or machine-specific configuration.

## Main computer configuration

Requirements are Python 3.11 or newer, Git, and Google Drive Desktop when interactions must move
between computers. Codex is the normal weekly interface; Linear and Google Calendar are connected
there through MCP.

1. Install the Python dependency:

   ```bash
   python3 -m pip install -r requirements.txt
   ```

2. Create the ignored `ledger.toml` from the
   [Handbook configuration view](docs/architecture.html#configuration). Set one stable identity,
   repository allowlists, the synchronized interaction path, and connector modes.
3. Validate the configuration and inspect synchronized interaction coverage:

   ```bash
   python3 scripts/validate_config.py --config ledger.toml
   python3 scripts/collect_interactions.py inspect --config ledger.toml --week 2026-W34
   ```

## Secondary computer details

Secondary computers need Python 3.11 or newer, `pipx`, Google Drive Desktop, and an authenticated
GitHub CLI. They do not need a Ledger checkout. Download and install the small wheel from the private
release:

```bash
python3 -m pip install --user pipx
python3 -m pipx ensurepath
gh auth login
download_dir="$(mktemp -d)"
gh release download exporter-v0.1.1 --repo changxizhu/Ledger \
  --pattern "ledger_exporter-0.1.1-py3-none-any.whl" \
  --dir "$download_dir"
pipx install --force "$download_dir/ledger_exporter-0.1.1-py3-none-any.whl"
```

Configure the device, test access, export once, and install the daily task:

```bash
ledger-export setup
ledger-export run
ledger-export schedule install
ledger-export status
```

`setup` detects the standard Codex, Claude Code, and Google Drive Desktop paths and creates a private
device configuration under `~/.config/ledger-exporter/`. Each device receives a unique name and
writes to its own Google Drive subdirectory. Use `ledger-export schedule remove` to remove the
background task. To upgrade, download the new wheel with `gh release download`, then repeat
`pipx install --force` with its local path.

For local package development, install the current checkout instead:

```bash
pipx install . --force
```

Maintainers publish a release by pushing a matching tag, for example `exporter-v0.1.1`. The release
workflow builds a wheel containing only the three exporter modules and attaches it to GitHub
Releases.

The existing script command remains available on the main computer for debugging:

```bash
python3 scripts/export_interactions.py --config ledger.toml --dry-run
python3 scripts/export_interactions.py --config ledger.toml
```

## Manual weekly pipeline

The normal entry point is the Codex dialog described in Quick start. The commands below expose the
same deterministic stages for debugging or unattended collection. They do not replace the Codex
analysis and evidence approval steps.

The commands are retained for debugging and unattended collection:

```bash
python3 scripts/validate_config.py --config ledger.toml

# Fetch remote state without changing any checkout.
python3 scripts/sync_repositories.py --config ledger.toml

# Fast-forward only clean repositories that are strictly behind.
python3 scripts/sync_repositories.py --config ledger.toml --apply

# MCP mode: run after the coordinator saves approved snapshots.
python3 .agents/skills/weekly-ledger-review/scripts/run_weekly.py collect \
  --config ledger.toml --week 2026-W34 --fetch

# Direct mode alternative: refresh snapshots and collect in one command.
# Do not run both collection commands for the same attempt.
python3 .agents/skills/weekly-ledger-review/scripts/run_weekly.py collect \
  --config ledger.toml --week 2026-W34 --fetch --refresh-direct

# After the coordinator and subagents create RUN/weekly-input.json:
python3 .agents/skills/weekly-ledger-review/scripts/run_weekly.py finalize \
  --config ledger.toml --run-dir RUN

python3 -m unittest discover -s tests -v
```

Collection creates a timestamped directory under `.ledger/runs/YYYY-Www/` and never changes its
collected `evidence.json`. It also creates `evidence-review.md`, a private report for checking source
status, commits, working-tree files, Git patches, interactions, Linear tasks, and Calendar events.
Finalization verifies that every theme evidence reference resolves to the collected run, then creates
`weekly-scored.json`, `proposals.json`, and `public-week.json` inside that timestamped directory. The
canonical private report is written beside the timestamped directories as
`.ledger/runs/YYYY-Www/YYYY-MM-week-N.html`, where `N` is the Monday's ordinal position in the month.
It does not write to Linear or Calendar.

To refresh direct-mode snapshots without starting a weekly run:

```bash
python3 .agents/skills/weekly-ledger-review/scripts/refresh_connectors.py \
  --config ledger.toml --week 2026-W34
```

## Codex workflow

1. Open this repository as the Codex project and restart it once so `.codex/config.toml`, skills,
   and subagent roles load.
2. Connect Linear and Google Calendar in Codex/ChatGPT settings when using the normal MCP path.
   Direct read mode is documented in the
   [Handbook identity and access view](docs/architecture.html#ownership).
3. In the Codex dialog, run `$weekly-ledger-review` and state the ISO week, for example
   `Review 2026-W34`.
4. Verify the generated `evidence-review.md` tables and Git patches before allowing theme analysis.
   The coordinator then uses the read-only evidence collector, theme analyst, and review auditor;
   each theme retains exact evidence references in the final report.

The scheduled exporter reads native Codex and Claude transcripts incrementally, redacts common
credential forms, and writes the shared `schemas/interaction-event.schema.json` format. It does not
run continuously and does not require Linear, Calendar, or model credentials. Each run writes
immutable JSONL batches so Google Drive, GVFS, and similar synchronized filesystems do not need to
support append, locking, or permission changes. Retries skip event IDs already present.

## Diagnostic commands

To collect repository activity without running the complete review:

```bash
python3 scripts/collect_git.py --config /path/to/ledger.toml \
  --start 2026-08-17 --end 2026-08-24 --output .ledger/git-events.json
```

The main computer reads all scheduled exports through one `ledger_jsonl` interaction source.
`scripts/collect_interactions.py` filters them to the requested week and deduplicates stable event
IDs; interaction volume remains evidence rather than a score.

Inspect configured interaction paths and date coverage without printing message content:

```bash
python3 scripts/collect_interactions.py inspect --config ledger.toml --week 2026-W34
```

## Current boundary

- Guarded repository inspection and clean fast-forward synchronization, local Git evidence,
  working-tree diffs, Codex/Claude interactions, scoring, reporting, monthly aggregation, update
  proposals, and anonymous exports are implemented.
- Linear and Calendar use approved JSON snapshots. MCP mode keeps credentials in Codex; optional
  direct mode resolves external credentials only while producing the snapshot and verifies the
  expected account first.
- Applying a proposal to Linear or Calendar is intentionally not automatic. The user reviews it
  first, and only the main coordinator performs the approved write.
- A public dashboard can consume `public-week.json`; deployment remains optional.
