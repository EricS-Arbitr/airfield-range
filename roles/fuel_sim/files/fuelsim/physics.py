"""
fuelsim/physics.py — process physics loop.

Ticks at configurable Hz (default 5, per fuelsim.yml -> physics.tick_hz).
On each tick:
  * Reads command coils and holding regs from SimState.
  * Computes:
     - Per-rack instantaneous flow (gpm) given pump/valve/interlock state
     - Meter totalizer increment (gal)
     - Source-tank drawdown (gal)
     - Header pressure response to running pumps
     - Tank hi/lo level switch state
     - Pump run feedback (P*_RUN_STS follows cmd + not-tripped)
     - ESD trip enforcement (any ESD_ACTIVE + no fresh reset → force all off)
     - Low-level cutout (T*_LO_LVL blocks flow from that tank)
  * Writes back to SimState:
     - Input registers: T*_LEVEL (%), T*_TEMP (0.1 F), LR*_FLOW (gpm),
       HEADER_PRESS (0.1 PSI), LR*_METER_HI/LO (32-bit)
     - Discrete inputs: P*_RUN_STS, T*_HI_LVL, T*_LO_LVL, ESD_ACTIVE

Physics constants come from cfg['physics']. Everything is stateful (level
persists across ticks) but there's no I/O outside SimState — unit-testable
by handing in a mock SimState.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from modbus import SimState

log = logging.getLogger("fuelsim.physics")


@dataclass
class TankState:
    """Persistent physical state of one storage tank."""
    tank_id: str          # e.g. "T-101"
    capacity_gal: float
    level_gal: float      # current, drawn down as pumps flow
    temp_f: float
    lo_pct: float = 5.0   # low switch trips below this %
    hi_pct: float = 95.0  # high switch trips above this %

    @property
    def level_pct(self) -> float:
        return 100.0 * (self.level_gal / self.capacity_gal) if self.capacity_gal else 0.0

    @property
    def lo_switch(self) -> bool:
        return self.level_pct <= self.lo_pct

    @property
    def hi_switch(self) -> bool:
        return self.level_pct >= self.hi_pct


@dataclass
class RackState:
    """Persistent state of one loading rack position."""
    rack_num: int              # 1 or 2
    active: bool = False       # a load is in progress
    src_tank_id: str | None = None
    active_truck_id: str | None = None
    preset_gal: float = 0.0
    dispensed_this_batch_gal: float = 0.0   # reset per load_start
    totalizer_gal: int = 0                  # 32-bit accumulator across all loads


@dataclass
class Physics:
    """The mutable physical world state + constants."""
    tanks: dict[str, TankState]      # tank_id -> state
    racks: dict[int, RackState]      # 1|2 -> state
    header_press_base_psi: float = 20.0
    header_press_per_pump_psi: float = 15.0
    rack_flow_gpm: float = 600.0
    tank_temp_f: float = 68.0
    esd_latched: bool = False


def build_physics(cfg: dict, tanks_seed: list[dict]) -> Physics:
    """Construct initial Physics from cfg['physics'] + tanks_seed.

    cfg['physics'] block honors:
      tick_hz                     (default 5)
      initial_levels_pct          per-tank overrides, e.g. {"T-101": 90}
      default_initial_level_pct   default 85
      header_press_base_psi       default 20.0
      header_press_per_pump_psi   default 15.0
      rack_flow_gpm               default 600.0
      tank_temp_f                 default 68.0
      lo_switch_pct               default 5.0
      hi_switch_pct               default 95.0
    """
    init_levels = cfg.get("initial_levels_pct") or {}
    default_init_pct = float(cfg.get("default_initial_level_pct", 85.0))
    lo_pct = float(cfg.get("lo_switch_pct", 5.0))
    hi_pct = float(cfg.get("hi_switch_pct", 95.0))
    tank_temp = float(cfg.get("tank_temp_f", 68.0))
    tanks: dict[str, TankState] = {}
    for t in tanks_seed:
        cap = float(t["capacity_gal"])
        pct = float(init_levels.get(t["tank_id"], default_init_pct))
        tanks[t["tank_id"]] = TankState(
            tank_id=t["tank_id"],
            capacity_gal=cap,
            level_gal=cap * pct / 100.0,
            temp_f=tank_temp,
            lo_pct=lo_pct,
            hi_pct=hi_pct,
        )
    racks = {1: RackState(rack_num=1), 2: RackState(rack_num=2)}
    return Physics(
        tanks=tanks,
        racks=racks,
        header_press_base_psi=float(cfg.get("header_press_base_psi", 20.0)),
        header_press_per_pump_psi=float(cfg.get("header_press_per_pump_psi", 15.0)),
        rack_flow_gpm=float(cfg.get("rack_flow_gpm", 600.0)),
        tank_temp_f=tank_temp,
    )


async def run(cfg: dict, sim: "SimState", physics: Physics) -> None:
    """Physics loop coroutine. Runs at cfg['tick_hz'] until cancelled."""
    tick_hz = float(cfg.get("tick_hz", 5.0))
    if not (0 < tick_hz <= 100):
        raise ValueError(f"physics.tick_hz must be in (0, 100], got {tick_hz}")
    dt_s = 1.0 / tick_hz
    log.info("physics loop starting: %.1f Hz (dt=%.3fs)", tick_hz, dt_s)
    # Seed initial values so first Modbus poll sees sane data.
    _write_slow_state(sim, physics)
    _write_totalizers(sim, physics)
    # Guard the tick with try/except so any exception is logged with a
    # traceback instead of silently killing the coroutine. asyncio's
    # gather(return_exceptions=True) at shutdown swallows unhandled task
    # exceptions -- without this guard the July-20 float&int TypeError
    # went undiagnosed for four days while state_machine kept advancing
    # (fooled everyone into thinking the sim was working).
    while True:
        await asyncio.sleep(dt_s)
        try:
            _tick(sim, physics, dt_s)
        except Exception:
            log.exception("physics tick raised — task will exit")
            raise


def _tick(sim: "SimState", physics: Physics, dt_s: float) -> None:
    """One physics step. Kept sync + free of I/O for unit-testability."""
    # ---- Read command state from SimState ---------------------------
    p1_cmd = bool(sim.get("P201_RUN_CMD"))
    p2_cmd = bool(sim.get("P202_RUN_CMD"))
    t101_out = bool(sim.get("T101_OUT_VLV"))
    t102_out = bool(sim.get("T102_OUT_VLV"))
    t103_out = bool(sim.get("T103_OUT_VLV"))
    lr1_valve = bool(sim.get("LR1_LOAD_VLV"))
    lr2_valve = bool(sim.get("LR2_LOAD_VLV"))
    lr1_ground = bool(sim.get("LR1_GROUND_OK"))
    lr2_ground = bool(sim.get("LR2_GROUND_OK"))
    lr1_deadman = bool(sim.get("LR1_DEADMAN"))
    lr2_deadman = bool(sim.get("LR2_DEADMAN"))
    lr1_overfill = bool(sim.get("LR1_OVERFILL"))
    lr2_overfill = bool(sim.get("LR2_OVERFILL"))
    esd_reset = bool(sim.get("ESD_RESET"))

    # ---- ESD latch --------------------------------------------------
    # If ESD_ACTIVE is ever set (by operator or external process), latch
    # it. Only ESD_RESET clears the latch. ESD_RESET is momentary.
    if bool(sim.get("ESD_ACTIVE")):
        physics.esd_latched = True
    if esd_reset:
        physics.esd_latched = False
        sim.set("ESD_RESET", 0)
    sim.set("ESD_ACTIVE", 1 if physics.esd_latched else 0)

    # ---- Source-tank routing per rack -------------------------------
    outlets = (t101_out, t102_out, t103_out)
    lr1_src = _resolve_src_tank(sim, "LR1_SRC_TANK", outlets)
    lr2_src = _resolve_src_tank(sim, "LR2_SRC_TANK", outlets)

    # ---- Interlock evaluation per rack ------------------------------
    lr1_ok = _interlock_ok(physics, lr1_src, lr1_valve, lr1_ground, lr1_deadman, lr1_overfill)
    lr2_ok = _interlock_ok(physics, lr2_src, lr2_valve, lr2_ground, lr2_deadman, lr2_overfill)

    # Pump 1 feeds rack 1, pump 2 feeds rack 2 (simplified from lead/lag).
    p1_actually_runs = p1_cmd and lr1_ok
    p2_actually_runs = p2_cmd and lr2_ok

    # ---- Flow integration -------------------------------------------
    flow_lr1_gpm = physics.rack_flow_gpm if p1_actually_runs else 0.0
    flow_lr2_gpm = physics.rack_flow_gpm if p2_actually_runs else 0.0
    flow_lr1_gal = flow_lr1_gpm * (dt_s / 60.0)
    flow_lr2_gal = flow_lr2_gpm * (dt_s / 60.0)

    # Drain the source tanks.
    if lr1_src is not None and flow_lr1_gal > 0:
        physics.tanks[lr1_src].level_gal = max(
            0.0, physics.tanks[lr1_src].level_gal - flow_lr1_gal
        )
    if lr2_src is not None and flow_lr2_gal > 0:
        physics.tanks[lr2_src].level_gal = max(
            0.0, physics.tanks[lr2_src].level_gal - flow_lr2_gal
        )

    # Increment totalizers (32-bit wraparound handled at set_totalizer).
    # int() cast MUST be inside the & 0xFFFFFFFF -- flow_lr{1,2}_gal is
    # a float (physics.rack_flow_gpm * dt_s/60), so the sum is float, and
    # `float & int` raises TypeError. Old form `int((tot + flow) & MASK)`
    # applied int() to the mask result, but the mask never ran because
    # the & op errored first, silently killing the physics task.
    physics.racks[1].totalizer_gal = int(physics.racks[1].totalizer_gal + flow_lr1_gal) & 0xFFFFFFFF
    physics.racks[2].totalizer_gal = int(physics.racks[2].totalizer_gal + flow_lr2_gal) & 0xFFFFFFFF
    physics.racks[1].dispensed_this_batch_gal += flow_lr1_gal
    physics.racks[2].dispensed_this_batch_gal += flow_lr2_gal

    # ---- Header pressure --------------------------------------------
    running = int(p1_actually_runs) + int(p2_actually_runs)
    header_press = physics.header_press_base_psi + running * physics.header_press_per_pump_psi

    # ---- Write back to SimState -------------------------------------
    for tid, t in physics.tanks.items():
        base = tid.replace("-", "")   # "T-101" → "T101"
        sim.set(f"{base}_LEVEL", t.level_pct)
        if f"{base}_TEMP" in sim.tag_map:
            sim.set(f"{base}_TEMP", t.temp_f)
        if f"{base}_HI_LVL" in sim.tag_map:
            sim.set(f"{base}_HI_LVL", 1 if t.hi_switch else 0)
        if f"{base}_LO_LVL" in sim.tag_map:
            sim.set(f"{base}_LO_LVL", 1 if t.lo_switch else 0)

    sim.set("LR1_FLOW", flow_lr1_gpm)
    sim.set("LR2_FLOW", flow_lr2_gpm)
    sim.set("HEADER_PRESS", header_press)
    sim.set_totalizer(0, physics.racks[1].totalizer_gal)
    sim.set_totalizer(1, physics.racks[2].totalizer_gal)
    sim.set("P201_RUN_STS", 1 if p1_actually_runs else 0)
    sim.set("P202_RUN_STS", 1 if p2_actually_runs else 0)


def _interlock_ok(
    physics: Physics,
    src_tank: str | None,
    valve_open: bool,
    ground_ok: bool,
    deadman_held: bool,
    overfill: bool,
) -> bool:
    """Evaluate the safety interlock chain for one rack.

    All conditions must be TRUE for dispense to be allowed:
      * ESD not latched
      * Rack load valve open
      * Grounding verified
      * Deadman held
      * Not in overfill trip
      * Source tank assigned + not at low-level cutout
    """
    if physics.esd_latched:
        return False
    if not (valve_open and ground_ok and deadman_held):
        return False
    if overfill:
        return False
    if src_tank is None:
        return False
    if physics.tanks[src_tank].lo_switch:
        return False
    return True


def _resolve_src_tank(
    sim: "SimState",
    src_holding_tag: str,
    outlet_valves: tuple[bool, bool, bool],
) -> str | None:
    """Return "T-10X" or None based on the holding reg + outlet valve state.

    state_machine writes the tank number (1/2/3) into LR*_SRC_TANK when
    a load starts. Zero / unset means "no assignment", in which case we
    fall back to the first tank whose outlet valve is open — a loose
    default so scenarios that open valves directly (without going through
    state_machine) still flow.
    """
    reg_val = int(sim.get(src_holding_tag))
    if reg_val in (1, 2, 3):
        return f"T-10{reg_val}"
    for i, open_ in enumerate(outlet_valves, start=1):
        if open_:
            return f"T-10{i}"
    return None


def _write_slow_state(sim: "SimState", physics: Physics) -> None:
    """Seed values physics writes that would otherwise start at 0 on boot."""
    for tid, t in physics.tanks.items():
        base = tid.replace("-", "")
        sim.set(f"{base}_LEVEL", t.level_pct)
        if f"{base}_TEMP" in sim.tag_map:
            sim.set(f"{base}_TEMP", t.temp_f)
    sim.set("HEADER_PRESS", physics.header_press_base_psi)


def _write_totalizers(sim: "SimState", physics: Physics) -> None:
    sim.set_totalizer(0, physics.racks[1].totalizer_gal)
    sim.set_totalizer(1, physics.racks[2].totalizer_gal)
