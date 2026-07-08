#!/usr/bin/env python3
"""
fuelsim.py -- Fuel Farm Simulator (build sheet §8).

Three concurrent components:
  1. Field-bus Modbus slave (pymodbus) -- serves the physical
     instruments to ff-plc-1 (OpenPLC master).
  2. Physics model -- integrates flow when pumps run + load valve open;
     updates meter totalizers + source-tank levels + header pressure.
  3. Logistics state machine + replay -- walks trucks through
     QUEUED -> LOADING -> ENROUTE -> DISPENSING -> RETURNING per the
     48h timeline, writes audit rows to PostgreSQL, mirrors active
     assignments into the SCADA-bus PLC holding registers.

This is a scaffold. Real code goes in the modules under this dir.
Run under systemd via /etc/systemd/system/fuelsim.service.
"""
from __future__ import annotations

import argparse
import logging
import signal
import sys
import time
from pathlib import Path

import yaml

log = logging.getLogger("fuelsim")


def load_config(path: Path) -> dict:
    with path.open() as f:
        return yaml.safe_load(f)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", type=Path, required=True)
    args = ap.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    cfg = load_config(args.config)
    log.info("fuelsim starting -- slave on %s:%d, replay speed %.1fx",
             cfg["modbus_slave"]["bind"], cfg["modbus_slave"]["port"],
             cfg["replay"]["speed"])

    # TODO: launch three threads / asyncio tasks:
    #   - modbus.serve(cfg["modbus_slave"], tag_map=cfg["tag_map"])
    #   - physics.run(cfg["physics"], tag_map=cfg["tag_map"])
    #   - replay.run(cfg["replay"], audit_db=cfg["audit_db"],
    #                plc=cfg["plc"], tag_map=cfg["tag_map"])
    #
    # For the initial scaffold: sleep-loop with a graceful SIGTERM so
    # systemd sees the service as "running".

    stop = False
    def _handle(*_):
        nonlocal stop
        stop = True
    signal.signal(signal.SIGTERM, _handle)
    signal.signal(signal.SIGINT, _handle)
    while not stop:
        time.sleep(1)
    log.info("fuelsim stopping")
    return 0


if __name__ == "__main__":
    sys.exit(main())
