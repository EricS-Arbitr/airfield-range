"""
fuelsim/replay.py — timeline reader + event scheduler.

Reads the JSONL timeline (produced by generate_timeline.py), sorts by
`t_offset_s`, and dispatches each event to state_machine at the right
elapsed time (scaled by replay.speed). Loops indefinitely if replay.loop
is truthy — each iteration starts from the current wall clock, so audit
rows across iterations get distinct timestamps.

Timeline JSONL schema (one object per line):
    {"t_offset_s": <int seconds from timeline start>,
     "event": <order_created|truck_arrival|load_start|load_end|
               delivery_start|delivery_end>,
     ...event-specific kwargs...}

The event names must match state_machine._on_<name> handlers exactly.
Unknown events are logged and skipped by state_machine (defense in depth).
"""
from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from state_machine import StateMachine

log = logging.getLogger("fuelsim.replay")


async def run(cfg: dict, sm: "StateMachine") -> None:
    """Read timeline, schedule events, feed them into the state_machine.

    cfg is the fuelsim.yml `replay:` block: {timeline, seed, speed, loop}.
    Note: `seed` is metadata only (used by generate_timeline.py, not here);
    surfaced for logging so a running instance can identify the dataset.
    """
    from state_machine import Event  # local import: avoid circular at module load

    path = Path(cfg["timeline"])
    speed = float(cfg.get("speed", 1.0))
    loop_mode = bool(cfg.get("loop", True))

    if speed <= 0:
        raise ValueError(f"replay.speed must be > 0, got {speed}")

    events = _load_timeline(path)
    if not events:
        log.error("replay: timeline %s is empty; nothing to replay", path)
        return
    log.info(
        "replay: %d events loaded from %s, speed=%.1fx loop=%s seed=%s",
        len(events), path, speed, loop_mode, cfg.get("seed", "?"),
    )

    iteration = 0
    loop = asyncio.get_event_loop()
    while True:
        wall_t0 = loop.time()
        for ev in events:
            offset_s = float(ev["t_offset_s"]) / speed
            due = wall_t0 + offset_s
            wait_s = due - loop.time()
            if wait_s > 0:
                await asyncio.sleep(wait_s)
            elif wait_s < -1.0:
                log.debug(
                    "replay behind schedule by %.2fs on event %s",
                    -wait_s, ev.get("event"),
                )
            payload = {k: v for k, v in ev.items() if k != "event"}
            await sm.enqueue_event(Event(event=ev["event"], payload=payload))
        iteration += 1
        if not loop_mode:
            log.info("replay: single-pass mode, exiting after %d events", len(events))
            return
        log.info("replay: iteration %d complete, wrapping", iteration)


def _load_timeline(path: Path) -> list[dict[str, Any]]:
    """Parse one JSON object per line. Empty/comment lines are skipped."""
    events: list[dict[str, Any]] = []
    if not path.exists():
        log.error("replay: timeline path does not exist: %s", path)
        return events
    with path.open() as f:
        for lineno, raw in enumerate(f, start=1):
            raw = raw.strip()
            if not raw or raw.startswith("#") or raw.startswith("{#"):
                continue
            try:
                events.append(json.loads(raw))
            except json.JSONDecodeError as e:
                log.warning("replay: skipping bad line %d: %s", lineno, e)
    events.sort(key=lambda e: float(e.get("t_offset_s", 0)))
    return events
