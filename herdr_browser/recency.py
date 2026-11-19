"""Recorded use, without inventing historical creation dates."""
import math
import time

GROUPS = ("Last 7 days", "Last 1 month", "Earlier")


def activity_time(row):
    value = row.get("last_activity_at")
    return value if isinstance(value, (int, float)) and math.isfinite(value) and value > 0 else None


def grouped_tasks(tasks, records, key_for_task, now=None):
    now = time.time() if now is None else now
    groups = [[] for _ in GROUPS]
    priority = {"blocked": 0, "done": 1, "working": 2, "idle": 3, "unknown": 4}
    for task in tasks:
        recorded = activity_time(records.get(key_for_task(task), {}))
        age = max(0, now - recorded) if recorded else None
        group = 0 if age is not None and age < 7 * 86400 else (
            1 if age is not None and age < 30 * 86400 else 2)
        groups[group].append((task, recorded))
    return [(name, sorted(rows, key=lambda item: (
        -(item[1] or 0), priority.get(item[0].get("agent_status"), 4),
        item[0].get("label", "").casefold(), item[0]["tab_id"])))
            for name, rows in zip(GROUPS, groups)]
