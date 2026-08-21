# Weekly record

The scoring script expects one JSON object with:

- `week`: ISO week label.
- `period.start` and `period.end`: evidence interval.
- `target_focus_hours` and `actual_focus_hours`: non-negative numbers.
- `tasks`: title, positive weight, and completion state.
- `themes`: name, positive weight, progress from 0 to 1, evidence count, exact evidence references,
  and a short evidence-based summary. `evidence_count` must equal the number of unique
  `evidence_refs`.
- `source_status`: availability and required state copied from `evidence.json`.
- `reflection`: issue, evidence, why it matters, and suggestion records.
- `next_plan.tasks`: proposed tasks for the next week. These remain proposals until the user
  approves an external update.

Progress should represent movement toward a defined outcome. A large diff or a long conversation may
support the estimate but does not determine it.

Each evidence reference has an `event_type`, `source`, and short `reason`. Use the full commit hash
and path for `git_commit`, the path for `git_worktree`, and `event_id` for `interaction`,
`linear_issue`, or `calendar_event`. References must copy exact values from the run's
`evidence.json`; finalization verifies that they resolve.

The default index is:

```text
30% plan completion + 45% outcome progress + 15% focus time + 10% evidence coverage
```

Focus time is capped at 100%. Extra hours do not compensate for weak outcomes.
