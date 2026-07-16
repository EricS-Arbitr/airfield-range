#!/usr/bin/env python3
"""
generate_timeline.py — deterministic 48h fuel operations timeline generator.

Given a fixed seed and duration in hours, emits a JSONL file of events that
fuelsim's replay module will consume. Determinism (same seed + same hours →
byte-identical output → identical audit rows across runs) enables
scenario repeatability for training assessment.

Ops-tempo profile (build sheet §8):
  * Baseline: 8 sorties/hour (order_created events)
  * Two surge windows (hours 8-10 and 32-34) at 4x baseline (32/hr)
  * Trucks assigned round-robin among the 6-truck fleet
  * Aircraft type drives requested_gal: C-17 4500-5500, KC-135 9000-11000,
    F-16 1200-1800
  * Load time ≈ preset_gal / 600 gpm  (converted to seconds)
  * ENROUTE 3-5 min · DISPENSING 5-10 min · RETURN 4-6 min
  * Source tank round-robin across T-101 / T-102 / T-103
  * Rack alternates 1 / 2

Usage:
    python3 generate_timeline.py --seed 20260101 --hours 48 \\
        --output fuel_ops_timeline.jsonl
"""
from __future__ import annotations

import argparse
import json
import random
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# --------------------------------------------------------------------------
# Seed data mirrors group_vars/fuel.yml — keep them in sync.
# --------------------------------------------------------------------------

TRUCKS = ["R-01", "R-02", "R-03", "R-04", "R-05", "R-06"]
TANKS = ["T-101", "T-102", "T-103"]
PADS = ["PAD-A1", "PAD-A2", "PAD-B1", "PAD-B2"]
AIRCRAFT = [
    ("AF-2231", "C-17"),
    ("AF-2232", "C-17"),
    ("AF-4118", "KC-135"),
    ("AF-4119", "KC-135"),
    ("AF-6602", "F-16"),
]
FUEL_BY_TYPE = {
    "C-17":   (4500, 5500),
    "KC-135": (9000, 11000),
    "F-16":   (1200, 1800),
}
RACK_FLOW_GPM = 600
SURGE_WINDOWS = [(8, 10), (32, 34)]   # (start_hr_inclusive, end_hr_exclusive)
BASELINE_RATE_PER_HOUR = 8
SURGE_RATE_PER_HOUR = 32


@dataclass
class Sortie:
    """One end-to-end refuel cycle: order → arrival → load → deliver → return."""
    order_id: int
    tail_no: str
    ac_type: str
    pad_id: str
    truck_id: str
    tank_id: str
    rack_pos: int
    preset_gal: int
    t0_s: int   # order_created timestamp offset

    def emit(self, rng: random.Random) -> list[dict[str, Any]]:
        """Emit all six events for this sortie, in order.

        `rng` is passed in so timing jitter is drawn from the same seeded
        stream as sortie construction — reproducibility across runs.
        """
        load_s = int(self.preset_gal / RACK_FLOW_GPM * 60)   # gal → seconds at 600 gpm
        arrive_dt = rng.randint(30, 90)
        pre_load_dt = rng.randint(30, 60)          # rack positioning
        enroute_dt = rng.randint(180, 300)          # taxi to pad
        dispense_dt = rng.randint(300, 600)         # aircraft refuel time

        t_created = self.t0_s
        t_arrive = t_created + arrive_dt
        t_load_start = t_arrive + pre_load_dt
        t_load_end = t_load_start + load_s
        t_deliver_start = t_load_end + enroute_dt
        t_deliver_end = t_deliver_start + dispense_dt

        return [
            {
                "t_offset_s": t_created,
                "event": "order_created",
                "order_id": self.order_id,
                "tail_no": self.tail_no,
                "pad_id": self.pad_id,
                "requested_gal": self.preset_gal,
            },
            {
                "t_offset_s": t_arrive,
                "event": "truck_arrival",
                "truck_id": self.truck_id,
            },
            {
                "t_offset_s": t_load_start,
                "event": "load_start",
                "truck_id": self.truck_id,
                "tank_id": self.tank_id,
                "rack_pos": self.rack_pos,
                "preset_gal": self.preset_gal,
            },
            {
                "t_offset_s": t_load_end,
                "event": "load_end",
                "truck_id": self.truck_id,
                "gallons": self.preset_gal,
            },
            {
                "t_offset_s": t_deliver_start,
                "event": "delivery_start",
                "truck_id": self.truck_id,
                "tail_no": self.tail_no,
                "pad_id": self.pad_id,
                "order_id": self.order_id,
            },
            {
                "t_offset_s": t_deliver_end,
                "event": "delivery_end",
                "truck_id": self.truck_id,
                "gallons": self.preset_gal,
            },
        ]


def _rate_at(hour: int) -> int:
    for a, b in SURGE_WINDOWS:
        if a <= hour < b:
            return SURGE_RATE_PER_HOUR
    return BASELINE_RATE_PER_HOUR


def build_sorties(hours: int, seed: int) -> list[Sortie]:
    """Produce sortie kick-off times per the ops tempo profile."""
    rng = random.Random(seed)
    sorties: list[Sortie] = []
    order_id = 1000       # matches build-sheet example range
    truck_rr = 0
    tank_rr = 0
    rack_rr = 0

    for hour in range(hours):
        rate_ph = _rate_at(hour)
        # Space slots evenly within the hour with jitter of ±25% of the base
        # spacing so the timeline doesn't feel mechanical.
        base_spacing = 3600 // rate_ph
        for slot in range(rate_ph):
            jitter = rng.randint(-base_spacing // 4, base_spacing // 4)
            t0 = hour * 3600 + slot * base_spacing + jitter
            t0 = max(0, min(t0, hours * 3600 - 1))
            tail, ac_type = AIRCRAFT[rng.randrange(len(AIRCRAFT))]
            lo, hi = FUEL_BY_TYPE[ac_type]
            preset = rng.randint(lo, hi)
            truck = TRUCKS[truck_rr % len(TRUCKS)]
            truck_rr += 1
            tank = TANKS[tank_rr % len(TANKS)]
            tank_rr += 1
            rack = (rack_rr % 2) + 1
            rack_rr += 1
            pad = PADS[rng.randrange(len(PADS))]
            sorties.append(Sortie(
                order_id=order_id,
                tail_no=tail,
                ac_type=ac_type,
                pad_id=pad,
                truck_id=truck,
                tank_id=tank,
                rack_pos=rack,
                preset_gal=preset,
                t0_s=t0,
            ))
            order_id += 1
    return sorties


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--seed", type=int, required=True, help="Deterministic seed")
    ap.add_argument("--hours", type=int, required=True, help="Timeline duration (hours)")
    ap.add_argument("--output", "-o", type=Path, required=True, help="Output JSONL path")
    args = ap.parse_args()

    # One RNG stream for sortie construction + timing so seed is the sole
    # entropy source. (Sortie.emit could take a per-sortie RNG derived from
    # the master, but a single stream keeps the output ordering stable.)
    sorties = build_sorties(args.hours, args.seed)
    events: list[dict[str, Any]] = []
    rng = random.Random(args.seed ^ 0xEE)   # a distinct sub-stream for emit()
    for s in sorties:
        events.extend(s.emit(rng))
    events.sort(key=lambda e: (e["t_offset_s"], e["event"]))

    with args.output.open("w") as f:
        for e in events:
            f.write(json.dumps(e, sort_keys=True))
            f.write("\n")
    print(
        f"wrote {len(events)} events across {len(sorties)} sorties to {args.output}",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
