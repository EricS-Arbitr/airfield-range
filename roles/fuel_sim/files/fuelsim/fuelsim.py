#!/usr/bin/env python3
"""
fuelsim.py — Fuel Farm Simulator entrypoint (build sheet §8).

Wires together the four sibling modules under asyncio:

  * modbus.serve          — pymodbus TCP slave on the field-bus
  * physics.run           — configurable-Hz process physics (default 5 Hz)
  * StateMachine.run      — event dispatcher (consumes replay events)
  * StateMachine.db_flusher   — Postgres batch writer (1 Hz flush)
  * StateMachine.snapshot_loop — 1 Hz tank_level_snap sampler
  * replay.run            — timeline reader + event scheduler

Runs under systemd via /etc/systemd/system/fuelsim.service. Graceful
shutdown on SIGTERM/SIGINT.
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import signal
import sys
from pathlib import Path

import yaml

# Sibling imports (script's directory is on sys.path when python invokes this
# file directly; systemd runs `python3 /opt/fuelsim/bin/fuelsim.py ...`).
import modbus
import physics as physics_mod
import replay
from state_machine import StateMachine

log = logging.getLogger("fuelsim")


def load_config(path: Path) -> dict:
    with path.open() as f:
        return yaml.safe_load(f)


async def _amain(cfg: dict) -> int:
    # ---- Register map + sim state ----------------------------------
    tag_map = modbus.TagMap(cfg["tag_map"])
    sim = modbus.SimState(
        tag_map,
        totalizer_word_order=cfg.get("totalizer_word_order", "hi_first"),
    )

    # ---- Physics ----------------------------------------------------
    tanks_seed = cfg.get("physics", {}).get("tanks_seed") or _default_tanks()
    phys_cfg = cfg.get("physics", {})
    phys = physics_mod.build_physics(phys_cfg, tanks_seed)

    # ---- State machine ---------------------------------------------
    sm = StateMachine(
        sim=sim,
        physics=phys,
        db_cfg=cfg["audit_db"],
        plc_cfg=cfg.get("plc"),
    )

    tasks = [
        asyncio.create_task(modbus.serve(cfg["modbus_slave"], sim), name="modbus"),
        asyncio.create_task(physics_mod.run(phys_cfg, sim, phys), name="physics"),
        asyncio.create_task(sm.run(), name="sm_dispatcher"),
        asyncio.create_task(sm.db_flusher(), name="sm_dbflush"),
        asyncio.create_task(sm.snapshot_loop(), name="sm_snap"),
        asyncio.create_task(replay.run(cfg["replay"], sm), name="replay"),
    ]

    # ---- Graceful shutdown -----------------------------------------
    stop = asyncio.Event()
    loop = asyncio.get_event_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)

    log.info("fuelsim tasks started; awaiting SIGTERM/SIGINT")
    await stop.wait()

    log.info("shutting down: cancelling %d tasks", len(tasks))
    for t in tasks:
        t.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    return 0


def _default_tanks() -> list[dict]:
    """Sane defaults if the Ansible template omits tanks_seed.

    The rendered fuelsim.yml normally injects `physics.tanks_seed` from
    group_vars/fuel.yml. This fallback keeps the process bootable in a
    hand-authored config.
    """
    return [
        {"tank_id": "T-101", "product": "JP-8", "capacity_gal": 500000},
        {"tank_id": "T-102", "product": "JP-8", "capacity_gal": 500000},
        {"tank_id": "T-103", "product": "JP-8", "capacity_gal": 500000},
    ]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", type=Path, required=True)
    args = ap.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    cfg = load_config(args.config)
    log.info(
        "fuelsim starting — slave on %s:%d, replay speed %.1fx",
        cfg["modbus_slave"]["bind"], cfg["modbus_slave"]["port"],
        cfg["replay"]["speed"],
    )

    try:
        return asyncio.run(_amain(cfg))
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main())
