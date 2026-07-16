"""
fuelsim/state_machine.py — logistics state machine + audit DB writer.

Consumes events from replay.py and:
  1. Writes to Postgres: fuel_orders, truck_queue, load_txn, delivery_txn,
     tank_level_snap, events (per §7 schema).
  2. Drives coils/holding registers in SimState so physics.py has the
     command surface it needs (set LR*_ACTIVE_TRUCK/SRC_TANK/PRESET_GAL on
     load_start, open source-tank outlet valve, open load valve, start pump).
     This is "simulated compliant operator" — ground/deadman/no-overfill are
     asserted so the interlock chain passes.
  3. Emits periodic tank_level_snap rows (1 Hz) for reconciliation.

Design:
  * DB writes are queued into an asyncio.Queue and flushed by a dedicated
    coroutine every 1 s. Reduces per-event round-trip overhead.
  * The state machine itself is a coroutine that awaits events from an
    asyncio.Queue populated by replay.py.
  * State per rack is held in-memory (open loads); Postgres is the durable
    log, not the runtime state.
"""
from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, TYPE_CHECKING

import psycopg2

if TYPE_CHECKING:
    from modbus import SimState
    from physics import Physics

log = logging.getLogger("fuelsim.state_machine")


# --------------------------------------------------------------------------
# Event dataclass
# --------------------------------------------------------------------------

@dataclass
class Event:
    """One event decoded from the timeline JSONL.

    `event` is the event name (order_created / truck_arrival / load_start /
    load_end / delivery_start / delivery_end). `payload` carries the
    event-specific kwargs from the JSONL row minus `event`.
    """
    event: str
    payload: dict[str, Any]

    @classmethod
    def from_jsonl(cls, line: str) -> "Event":
        d = json.loads(line)
        name = d.pop("event")
        return cls(event=name, payload=d)


# --------------------------------------------------------------------------
# In-memory rack state
# --------------------------------------------------------------------------

@dataclass
class LoadInProgress:
    """Rack-side transient state during one load transaction."""
    truck_id: str
    tank_id: str
    rack_pos: int
    preset_gal: float
    meter_start_gal: int   # totalizer value at load_start
    start_ts: datetime


# --------------------------------------------------------------------------
# StateMachine
# --------------------------------------------------------------------------

class StateMachine:
    def __init__(
        self,
        sim: "SimState",
        physics: "Physics",
        db_cfg: dict,
        plc_cfg: dict | None = None,
    ) -> None:
        self.sim = sim
        self.physics = physics
        self.db_cfg = db_cfg
        self.plc_cfg = plc_cfg  # future hook: mirror commands to a real PLC

        # In-memory transient state, per rack (1 or 2)
        self.open_loads: dict[int, LoadInProgress] = {}
        # In-memory active deliveries, by truck_id
        self.open_deliveries: dict[str, dict[str, Any]] = {}
        # DB write queue — (sql, params) tuples; None sentinel to shut down
        self.db_queue: asyncio.Queue = asyncio.Queue()
        # Event input queue populated by replay.py
        self.event_queue: asyncio.Queue = asyncio.Queue()

    # ------------------------------------------------------------------
    # Public entry points
    # ------------------------------------------------------------------

    async def enqueue_event(self, ev: Event) -> None:
        await self.event_queue.put(ev)

    async def run(self) -> None:
        """Dispatch loop — awaits events and applies them."""
        log.info("state_machine dispatcher starting")
        while True:
            ev = await self.event_queue.get()
            if ev is None:
                break
            try:
                await self._apply(ev)
            except Exception:
                log.exception("failed to apply event %s payload=%s", ev.event, ev.payload)

    async def db_flusher(self) -> None:
        """Long-running coroutine that flushes DB ops every 1 s.

        Establishes a persistent psycopg2 connection with autocommit for
        prompt visibility. Batches queue items into a single transaction.
        Reconnects on error.
        """
        log.info(
            "db_flusher starting — %s:%d/%s",
            self.db_cfg["host"], self.db_cfg.get("port", 5432), self.db_cfg["dbname"],
        )
        conn: psycopg2.extensions.connection | None = None
        while True:
            await asyncio.sleep(1.0)
            batch: list[tuple[str, list | dict]] = []
            try:
                while True:
                    item = self.db_queue.get_nowait()
                    if item is None:
                        return  # shutdown sentinel
                    batch.append(item)
            except asyncio.QueueEmpty:
                pass
            if not batch:
                continue
            if conn is None:
                try:
                    conn = _pg_connect(self.db_cfg)
                except Exception:
                    log.exception("db_flusher: connect failed; will retry")
                    continue
            try:
                with conn.cursor() as cur:
                    for sql, params in batch:
                        cur.execute(sql, params)
                conn.commit()
            except Exception:
                log.exception("db flush failed; rolling back and reconnecting")
                try:
                    conn.rollback()
                except Exception:
                    pass
                try:
                    conn.close()
                except Exception:
                    pass
                conn = None

    async def snapshot_loop(self) -> None:
        """Sample tank levels once a second into tank_level_snap."""
        log.info("snapshot_loop starting (1 Hz)")
        while True:
            await asyncio.sleep(1.0)
            ts = datetime.now(tz=timezone.utc)
            for tank_id, t in self.physics.tanks.items():
                await self.db_queue.put((
                    "INSERT INTO tank_level_snap (ts, tank_id, level_gal, temp_f) "
                    "VALUES (%s,%s,%s,%s)",
                    [ts, tank_id, round(t.level_gal, 2), round(t.temp_f, 2)],
                ))

    # ------------------------------------------------------------------
    # Event application (dispatch by naming convention: _on_<event>)
    # ------------------------------------------------------------------

    async def _apply(self, ev: Event) -> None:
        handler = getattr(self, f"_on_{ev.event}", None)
        if handler is None:
            log.warning("no handler for event %s", ev.event)
            return
        await handler(ev.payload)

    async def _on_order_created(self, p: dict) -> None:
        await self.db_queue.put((
            "INSERT INTO fuel_orders (order_id, tail_no, pad_id, requested_gal, status, created_ts) "
            "VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT (order_id) DO NOTHING",
            [
                int(p["order_id"]),
                p["tail_no"],
                p["pad_id"],
                float(p["requested_gal"]),
                "OPEN",
                datetime.now(tz=timezone.utc),
            ],
        ))

    async def _on_truck_arrival(self, p: dict) -> None:
        truck_id = p["truck_id"]
        ts = datetime.now(tz=timezone.utc)
        await self.db_queue.put((
            "UPDATE trucks SET status='QUEUED' WHERE truck_id=%s",
            [truck_id],
        ))
        await self.db_queue.put((
            "INSERT INTO truck_queue (truck_id, state, enqueued_ts, updated_ts) "
            "VALUES (%s,'QUEUED',%s,%s)",
            [truck_id, ts, ts],
        ))

    async def _on_load_start(self, p: dict) -> None:
        truck_id = p["truck_id"]
        tank_id = p["tank_id"]
        rack_pos = int(p["rack_pos"])
        preset = float(p["preset_gal"])
        ts = datetime.now(tz=timezone.utc)
        tank_num = _tank_num(tank_id)   # "T-102" → 2

        # Snapshot the current totalizer as meter_start; reset per-batch counter.
        meter_start = self.physics.racks[rack_pos].totalizer_gal
        self.physics.racks[rack_pos].dispensed_this_batch_gal = 0.0
        self.physics.racks[rack_pos].active = True
        self.physics.racks[rack_pos].src_tank_id = tank_id
        self.physics.racks[rack_pos].active_truck_id = truck_id
        self.physics.racks[rack_pos].preset_gal = preset

        self.open_loads[rack_pos] = LoadInProgress(
            truck_id=truck_id,
            tank_id=tank_id,
            rack_pos=rack_pos,
            preset_gal=preset,
            meter_start_gal=meter_start,
            start_ts=ts,
        )

        # Drive command coils + holding regs so physics can dispense.
        # LR*_ACTIVE_TRUCK is a 16-bit HR; encode as the truck number
        # ("R-03" → 3) so a human can read it on the HMI.
        self.sim.set(f"LR{rack_pos}_ACTIVE_TRUCK", _truck_id_to_int(truck_id))
        self.sim.set(f"LR{rack_pos}_SRC_TANK", tank_num)
        self.sim.set(f"LR{rack_pos}_PRESET_GAL", int(round(preset)))
        # Compliant operator: ground/deadman set.
        self.sim.set(f"LR{rack_pos}_GROUND_OK", 1)
        self.sim.set(f"LR{rack_pos}_DEADMAN", 1)
        # Open source-tank outlet valve + rack load valve + start pump.
        self.sim.set(f"T10{tank_num}_OUT_VLV", 1)
        self.sim.set(f"LR{rack_pos}_LOAD_VLV", 1)
        self.sim.set(f"P20{rack_pos}_RUN_CMD", 1)

        # DB: truck status LOADING; truck_queue row state LOADING.
        await self.db_queue.put((
            "UPDATE trucks SET status='LOADING' WHERE truck_id=%s",
            [truck_id],
        ))
        await self.db_queue.put((
            "UPDATE truck_queue SET state='LOADING', source_tank=%s, rack_position=%s, updated_ts=%s "
            "WHERE truck_id=%s AND state='QUEUED'",
            [tank_id, rack_pos, ts, truck_id],
        ))
        await self._log_event(
            "sim", "info", "LOAD_START",
            f"rack {rack_pos} truck {truck_id} tank {tank_id} preset {preset:.0f} gal",
        )

    async def _on_load_end(self, p: dict) -> None:
        truck_id = p["truck_id"]
        rack_pos = _rack_for_truck(self.open_loads, truck_id)
        if rack_pos is None:
            log.warning("load_end for truck %s but no open load in memory", truck_id)
            return
        load = self.open_loads.pop(rack_pos)
        ts = datetime.now(tz=timezone.utc)
        # Physics-measured actual gallons is authoritative for reconciliation.
        actual_gal = self.physics.racks[rack_pos].dispensed_this_batch_gal
        meter_end = self.physics.racks[rack_pos].totalizer_gal
        tank_num = _tank_num(load.tank_id)

        # Close commands.
        self.sim.set(f"P20{rack_pos}_RUN_CMD", 0)
        self.sim.set(f"LR{rack_pos}_LOAD_VLV", 0)
        self.sim.set(f"LR{rack_pos}_DEADMAN", 0)   # operator lets go
        self.sim.set(f"T10{tank_num}_OUT_VLV", 0)
        self.sim.set(f"LR{rack_pos}_ACTIVE_TRUCK", 0)
        self.sim.set(f"LR{rack_pos}_SRC_TANK", 0)
        self.sim.set(f"LR{rack_pos}_PRESET_GAL", 0)

        # Clear physics-side rack state.
        self.physics.racks[rack_pos].active = False
        self.physics.racks[rack_pos].src_tank_id = None
        self.physics.racks[rack_pos].active_truck_id = None
        self.physics.racks[rack_pos].dispensed_this_batch_gal = 0.0
        self.physics.racks[rack_pos].preset_gal = 0.0

        await self.db_queue.put((
            "INSERT INTO load_txn (truck_id, tank_id, rack_pos, preset_gal, meter_start, meter_end, "
            "gallons, operator, start_ts, end_ts) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            [
                truck_id, load.tank_id, load.rack_pos, load.preset_gal,
                load.meter_start_gal, meter_end,
                round(actual_gal, 2),
                "sim",   # operator field placeholder
                load.start_ts, ts,
            ],
        ))
        await self.db_queue.put((
            "UPDATE trucks SET status='ENROUTE' WHERE truck_id=%s",
            [truck_id],
        ))
        await self.db_queue.put((
            "UPDATE truck_queue SET state='ENROUTE', updated_ts=%s WHERE truck_id=%s AND state='LOADING'",
            [ts, truck_id],
        ))
        await self._log_event(
            "sim", "info", "LOAD_END",
            f"rack {rack_pos} truck {truck_id} {actual_gal:.0f}/{load.preset_gal:.0f} gal",
        )

    async def _on_delivery_start(self, p: dict) -> None:
        truck_id = p["truck_id"]
        ts = datetime.now(tz=timezone.utc)
        self.open_deliveries[truck_id] = {
            "tail_no": p["tail_no"],
            "pad_id": p["pad_id"],
            "order_id": int(p["order_id"]),
            "start_ts": ts,
        }
        await self.db_queue.put((
            "UPDATE trucks SET status='DISPENSING' WHERE truck_id=%s",
            [truck_id],
        ))
        await self.db_queue.put((
            "UPDATE truck_queue SET state='DISPENSING', updated_ts=%s WHERE truck_id=%s AND state='ENROUTE'",
            [ts, truck_id],
        ))

    async def _on_delivery_end(self, p: dict) -> None:
        truck_id = p["truck_id"]
        gallons = float(p["gallons"])
        ts = datetime.now(tz=timezone.utc)
        d = self.open_deliveries.pop(truck_id, None)
        if d is None:
            log.warning("delivery_end for truck %s but no open delivery", truck_id)
            return
        await self.db_queue.put((
            "INSERT INTO delivery_txn (truck_id, tail_no, pad_id, order_id, gallons, start_ts, end_ts) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s)",
            [truck_id, d["tail_no"], d["pad_id"], d["order_id"], gallons, d["start_ts"], ts],
        ))
        await self.db_queue.put((
            "UPDATE fuel_orders SET status='FILLED' WHERE order_id=%s",
            [d["order_id"]],
        ))
        await self.db_queue.put((
            "UPDATE trucks SET status='RETURNING' WHERE truck_id=%s",
            [truck_id],
        ))
        await self.db_queue.put((
            "UPDATE truck_queue SET state='RETURNING', updated_ts=%s WHERE truck_id=%s AND state='DISPENSING'",
            [ts, truck_id],
        ))
        # For MVP, return-to-service is immediate. A cooldown/inspection
        # window can be added later by scheduling the AVAILABLE transition
        # via an asyncio.sleep task if realism demands.
        await self.db_queue.put((
            "UPDATE trucks SET status='AVAILABLE' WHERE truck_id=%s",
            [truck_id],
        ))

    async def _log_event(self, source: str, severity: str, code: str, message: str) -> None:
        await self.db_queue.put((
            "INSERT INTO events (source, severity, code, message) VALUES (%s,%s,%s,%s)",
            [source, severity, code, message],
        ))


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

def _tank_num(tank_id: str) -> int:
    """`T-101` → 1, `T-102` → 2, `T-103` → 3."""
    try:
        return int(tank_id.rsplit("-", 1)[-1]) - 100
    except (ValueError, IndexError):
        raise ValueError(f"unrecognized tank_id {tank_id!r}") from None


def _truck_id_to_int(truck_id: str) -> int:
    """`R-03` → 3. Fits comfortably in a 16-bit unsigned register."""
    try:
        return int(truck_id.rsplit("-", 1)[-1])
    except (ValueError, IndexError):
        return abs(hash(truck_id)) & 0xFFFF


def _rack_for_truck(open_loads: dict[int, LoadInProgress], truck_id: str) -> int | None:
    for rack, load in open_loads.items():
        if load.truck_id == truck_id:
            return rack
    return None


def _pg_connect(db_cfg: dict):
    return psycopg2.connect(
        host=db_cfg["host"],
        port=int(db_cfg.get("port", 5432)),
        dbname=db_cfg["dbname"],
        user=db_cfg["user"],
        password=db_cfg["password"],
        connect_timeout=10,
    )
