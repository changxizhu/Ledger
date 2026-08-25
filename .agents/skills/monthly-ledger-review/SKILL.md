---
name: monthly-ledger-review
description:
  Review a month of weekly Ledger records and long-term goals. Use when evaluating trends, recurring
  carry-over, theme allocation, planning accuracy, and changes to monthly or long-term priorities in
  Linear.
---

# Monthly Ledger Review

Use completed weekly records as the primary evidence. Do not rescore old weeks unless their source
data was corrected.

## Workflow

1. Run the monthly aggregation:

   ```bash
   python3 .agents/skills/monthly-ledger-review/scripts/aggregate_month.py \
     --month YYYY-MM --runs-dir .ledger/runs \
     --output .ledger/months/YYYY-MM.json
   ```

   This loads every finalized weekly record that overlaps the month.

2. Compare index components separately; a single average can hide weak planning or excessive time
   use.
3. Summarize theme continuity, completed outcomes, repeated carry-over, and focus allocation.
   Separate work themes from personal growth themes.
4. Review long-term goals in Linear. Keep goals outcome-based and move only the next actionable
   steps into weekly planning.
5. Identify at most three priority changes for the next month.
6. Produce a private monthly report and an optional anonymized aggregate.

## Guardrails

- Treat the index as a diagnostic signal, not a target to maximize.
- Do not reward more messages, commits, lines, or hours without outcome evidence.
- Explain missing weeks or incomplete data instead of silently treating them as zero.
- Calendar reflects reserved or actual time; Linear remains the planning record.
