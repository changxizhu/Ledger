# Weekly record

The scoring script expects one JSON object with:

- `week`: ISO week label.
- `period.start` and `period.end`: evidence interval.
- `target_focus_hours` and `actual_focus_hours`: non-negative numbers.
- `tasks`: title, positive weight, and completion state.
- `themes`: name, positive weight, progress from 0 to 1, evidence count, and a
  short evidence-based summary.

Progress should represent movement toward a defined outcome. A large diff or a long
conversation may support the estimate but does not determine it.

The default index is:

```text
30% plan completion + 45% outcome progress + 15% focus time + 10% evidence coverage
```

Focus time is capped at 100%. Extra hours do not compensate for weak outcomes.

