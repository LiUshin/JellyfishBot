"""Regression smoke for the scheduler runaway guards.

Covers the three protections added after a service task spun in a hot loop,
burning one agent + LLM call per iteration:

  1. ``once`` + empty/"now" schedule fires ASAP on creation but stops after
     its first run (the loop's root cause).
  2. Consecutive failures trip a circuit breaker that disables the task.
  3. A re-schedule that lands too close to the run that produced it is
     clamped to a minimum interval.

Run:  .venv/bin/python scripts/smoke_runaway_guards.py
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services import scheduler as sch  # noqa: E402

_passed = 0
_failed = 0


def check(label: str, actual, expected) -> None:
    global _passed, _failed
    ok = actual == expected
    if ok:
        _passed += 1
        print(f"  PASS  {label}")
    else:
        _failed += 1
        print(f"  FAIL  {label}\n          expected: {expected!r}\n          actual:   {actual!r}")


def once_task(**over):
    task = {"id": "task_test", "enabled": True, "schedule_type": "once",
            "schedule": "", "tz_offset_hours": 8.0, "last_run_at": None}
    task.update(over)
    return task


NOW = datetime.now(timezone.utc)


print("\n[1] once + empty schedule — fire ASAP once, then stop")
fresh = once_task()
first = sch._compute_next_run(fresh, NOW)
check("never-run task gets an immediate next_run_at", first, NOW.isoformat())
already_ran = once_task(last_run_at=NOW.isoformat())
check("already-run task stops", sch._compute_next_run(already_ran, NOW), None)
check('schedule="now" behaves the same',
      sch._compute_next_run(once_task(schedule="now", last_run_at=NOW.isoformat()), NOW),
      None)

print("\n[2] once with explicit times still behaves as before")
check("future ISO schedules",
      sch._compute_next_run(once_task(schedule="2099-01-01T09:00:00+08:00"), NOW),
      "2099-01-01T01:00:00+00:00")
check("past ISO stops",
      sch._compute_next_run(once_task(schedule="2020-01-01T09:00:00+08:00"), NOW),
      None)

print("\n[3] end-to-end: the old infinite loop is gone")
task = once_task()
task["next_run_at"] = sch._compute_next_run(task, NOW)
fire_count = 0
finished = NOW
for _ in range(10):
    if not task["next_run_at"]:
        break
    fire_count += 1
    finished = finished + timedelta(seconds=2)
    task["last_run_at"] = finished.isoformat()
    sch._apply_post_run_schedule(task, "success", finished)
check("a once task fires exactly once", fire_count, 1)

print("\n[4] circuit breaker on consecutive failures")
os.environ["SCHEDULER_MAX_CONSECUTIVE_FAILURES"] = "3"
task = {"id": "task_cb", "enabled": True, "schedule_type": "interval",
        "schedule": "60", "tz_offset_hours": 8.0, "last_run_at": NOW.isoformat()}
reasons = []
for i in range(3):
    reasons.append(sch._apply_post_run_schedule(task, "error", NOW))
check("survives the first two failures", [bool(r) for r in reasons[:2]], [False, False])
check("trips on the third", bool(reasons[2]), True)
check("task is disabled", task["enabled"], False)
check("disabled task is unscheduled", task["next_run_at"], None)
check("failure counter recorded", task["consecutive_failures"], 3)

print("\n[5] a success resets the counter")
task = {"id": "task_cb2", "enabled": True, "schedule_type": "interval",
        "schedule": "60", "tz_offset_hours": 8.0, "last_run_at": NOW.isoformat()}
sch._apply_post_run_schedule(task, "error", NOW)
sch._apply_post_run_schedule(task, "error", NOW)
sch._apply_post_run_schedule(task, "success", NOW)
check("counter back to zero", task["consecutive_failures"], 0)
check("still enabled", task["enabled"], True)

print("\n[6] minimum interval floor")
os.environ["SCHEDULER_MIN_RUN_INTERVAL_S"] = "30"
os.environ["SCHEDULER_MAX_CONSECUTIVE_FAILURES"] = "0"  # isolate this guard
task = {"id": "task_floor", "enabled": True, "schedule_type": "interval",
        "schedule": "1", "tz_offset_hours": 8.0, "last_run_at": NOW.isoformat()}
sch._apply_post_run_schedule(task, "success", NOW)
check("1s interval is clamped to the 30s floor",
      task["next_run_at"], (NOW + timedelta(seconds=30)).isoformat())

task = {"id": "task_nofloor", "enabled": True, "schedule_type": "interval",
        "schedule": "3600", "tz_offset_hours": 8.0, "last_run_at": NOW.isoformat()}
sch._apply_post_run_schedule(task, "success", NOW)
check("an hourly interval is left alone",
      task["next_run_at"], (NOW + timedelta(seconds=3600)).isoformat())

print("\n[7] guards can be switched off")
os.environ["SCHEDULER_MIN_RUN_INTERVAL_S"] = "0"
task = {"id": "task_off", "enabled": True, "schedule_type": "interval",
        "schedule": "1", "tz_offset_hours": 8.0, "last_run_at": NOW.isoformat()}
sch._apply_post_run_schedule(task, "success", NOW)
check("floor disabled by 0",
      task["next_run_at"], (NOW + timedelta(seconds=1)).isoformat())

print(f"\n{'=' * 52}\n{_passed} passed, {_failed} failed\n{'=' * 52}")
sys.exit(1 if _failed else 0)
