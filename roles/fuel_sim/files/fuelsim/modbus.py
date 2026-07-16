"""
fuelsim/modbus.py — pymodbus TCP slave + tag-name-driven SimState.

Exposes the field-bus register map from group_vars/fuel.yml (§4 of the
fuel-farm build sheet) as an asyncio Modbus TCP server on
{modbus_slave.bind}:{modbus_slave.port} (typically 172.16.46.17:502).
ff-plc-1's OpenPLC runtime polls this as a Modbus master to read the
"physical" instruments (tank levels, flow meters, pump run feedback).

Design:
  * TagMap: name -> (block_type, addr, scale) resolved once from the
    fuel_modbus_* lists in group_vars/fuel.yml.
  * SimState: a thin wrapper around a pymodbus ModbusSlaveContext with
    tag-name-driven `.get(name)` / `.set(name, value)`. Handles the
    16-bit scale factor round-trip so callers work in engineering units.
  * 32-bit totalizers are stored as a pair of 16-bit registers; the
    hi/lo split honors the `totalizer_word_order` config knob.
  * The server itself is a coroutine that runs forever until cancelled.

pymodbus 3.6.x is a hard requirement (installed by the fuel_sim role).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from pymodbus.datastore import (
    ModbusServerContext,
    ModbusSlaveContext,
    ModbusSparseDataBlock,
)
from pymodbus.server import StartAsyncTcpServer

log = logging.getLogger("fuelsim.modbus")

# The four Modbus TCP "areas" that a slave can host. Names match the
# group_vars/fuel.yml keys.
COILS = "coils"
DISCRETE = "discrete_inputs"
INPUT = "input_registers"
HOLDING = "holding_registers"


@dataclass
class Tag:
    """One entry from a group_vars/fuel.yml fuel_modbus_* list."""
    name: str
    block: str          # coils / discrete_inputs / input_registers / holding_registers
    addr: int
    scale: float = 1.0
    unit: str = ""
    desc: str = ""


class TagMap:
    """Bidirectional lookup between tag names and (block, addr).

    Built once from the four fuel_modbus_* lists in the config.
    """

    def __init__(self, tag_map_cfg: dict[str, list[dict[str, Any]]]) -> None:
        self._tags: dict[str, Tag] = {}
        for block_key, entries in tag_map_cfg.items():
            for e in entries or []:
                tag = Tag(
                    name=e["tag"],
                    block=block_key,
                    addr=int(e["addr"]),
                    scale=float(e.get("scale", 1.0)),
                    unit=e.get("unit", ""),
                    desc=e.get("desc", ""),
                )
                if tag.name in self._tags:
                    raise ValueError(f"duplicate tag {tag.name!r}")
                self._tags[tag.name] = tag

    def get(self, name: str) -> Tag:
        try:
            return self._tags[name]
        except KeyError as exc:
            raise KeyError(f"unknown tag {name!r}") from exc

    def by_block(self, block: str) -> list[Tag]:
        return [t for t in self._tags.values() if t.block == block]

    def __iter__(self):
        return iter(self._tags.values())

    def __contains__(self, name: str) -> bool:
        return name in self._tags


class SimState:
    """Modbus slave state + tag-name-driven getters/setters.

    The pymodbus ModbusSlaveContext is the source of truth. This wrapper
    resolves tag names to (block, addr), applies scale factors for input/
    holding registers, and provides 32-bit totalizer helpers.
    """

    # 32-bit totalizer pairs (build sheet §4). Order in tuple is (hi, lo)
    # by tag name; word ordering on the wire is set by totalizer_word_order.
    TOTALIZER_PAIRS = [
        ("LR1_METER_HI", "LR1_METER_LO"),
        ("LR2_METER_HI", "LR2_METER_LO"),
    ]

    def __init__(self, tag_map: TagMap, totalizer_word_order: str = "hi_first") -> None:
        self.tag_map = tag_map
        if totalizer_word_order not in ("hi_first", "lo_first"):
            raise ValueError(
                f"totalizer_word_order must be hi_first or lo_first, got {totalizer_word_order!r}"
            )
        self.totalizer_word_order = totalizer_word_order

        # Build one sparse data block per Modbus area, populated with all
        # addresses in that block. pymodbus insists on non-empty blocks.
        blocks: dict[str, ModbusSparseDataBlock] = {}
        for block_key in (COILS, DISCRETE, INPUT, HOLDING):
            tags = tag_map.by_block(block_key)
            if not tags:
                blocks[block_key] = ModbusSparseDataBlock({0: 0})
                continue
            init = {t.addr: 0 for t in tags}
            blocks[block_key] = ModbusSparseDataBlock(init)

        self._slave_ctx = ModbusSlaveContext(
            co=blocks[COILS],
            di=blocks[DISCRETE],
            ir=blocks[INPUT],
            hr=blocks[HOLDING],
            zero_mode=True,   # match §4 register map without off-by-one
        )
        self._server_ctx = ModbusServerContext(slaves=self._slave_ctx, single=True)

    # ---------- Public tag-name-driven interface -------------------------

    def get(self, name: str) -> float:
        """Read a tag by name. Registers apply scale (engineering units)."""
        tag = self.tag_map.get(name)
        raw = self._read_raw(tag.block, tag.addr)
        if tag.block in (COILS, DISCRETE):
            return int(raw)
        return float(raw) * tag.scale

    def set(self, name: str, value: float) -> None:
        """Write a tag by name. Registers convert engineering units → raw counts."""
        tag = self.tag_map.get(name)
        if tag.block in (COILS, DISCRETE):
            raw = 1 if value else 0
        else:
            # Convert engineering units back to raw counts.
            raw = int(round(value / tag.scale)) if tag.scale != 1.0 else int(round(value))
            # Clamp to 16-bit unsigned.
            raw = max(0, min(0xFFFF, raw))
        self._write_raw(tag.block, tag.addr, raw)

    def get_totalizer(self, pair_index: int) -> int:
        """Read a 32-bit totalizer as a single Python int (pair_index 0=LR1, 1=LR2)."""
        hi_name, lo_name = self.TOTALIZER_PAIRS[pair_index]
        hi = int(self._read_raw(INPUT, self.tag_map.get(hi_name).addr))
        lo = int(self._read_raw(INPUT, self.tag_map.get(lo_name).addr))
        if self.totalizer_word_order == "hi_first":
            return (hi << 16) | lo
        return (lo << 16) | hi

    def set_totalizer(self, pair_index: int, value: int) -> None:
        """Write a 32-bit totalizer as two 16-bit register writes."""
        v = int(value) & 0xFFFFFFFF   # clamp to 32 bits
        hi = (v >> 16) & 0xFFFF
        lo = v & 0xFFFF
        hi_name, lo_name = self.TOTALIZER_PAIRS[pair_index]
        self._write_raw(INPUT, self.tag_map.get(hi_name).addr, hi)
        self._write_raw(INPUT, self.tag_map.get(lo_name).addr, lo)

    # ---------- pymodbus server context accessor ------------------------

    @property
    def server_context(self) -> ModbusServerContext:
        return self._server_ctx

    # ---------- Private raw block access --------------------------------

    def _read_raw(self, block: str, addr: int) -> int:
        # getValues/setValues take a function code:
        #   1 = coils, 2 = discrete inputs, 3 = holding regs, 4 = input regs.
        fx = _FX_FOR[block]
        values = self._slave_ctx.getValues(fx, addr, count=1)
        return int(values[0])

    def _write_raw(self, block: str, addr: int, value: int) -> None:
        fx = _FX_FOR[block]
        self._slave_ctx.setValues(fx, addr, [value])


_FX_FOR = {
    COILS: 1,
    DISCRETE: 2,
    HOLDING: 3,
    INPUT: 4,
}


# ---------- Server coroutine -----------------------------------------------

async def serve(cfg: dict[str, Any], sim: SimState) -> None:
    """Run the pymodbus TCP slave forever.

    cfg is the fuelsim.yml `modbus_slave` block: bind, port, unit_id.
    sim is a SimState whose server_context we bind to.
    """
    bind = cfg["bind"]
    port = int(cfg["port"])
    unit_id = int(cfg.get("unit_id", 1))
    log.info("modbus slave binding %s:%d unit_id=%d", bind, port, unit_id)
    # pymodbus 3.6 async server; runs until the containing task is cancelled.
    await StartAsyncTcpServer(
        context=sim.server_context,
        address=(bind, port),
    )
