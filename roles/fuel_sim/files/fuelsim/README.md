# fuelsim

Fuel Farm Simulator (build sheet §8). Five asyncio-driven modules.

- `fuelsim.py` — entry point. Loads YAML config, wires the four sibling
  modules onto one asyncio event loop, handles SIGTERM/SIGINT.
- `modbus.py` — pymodbus TCP slave + `SimState` with tag-name-driven
  `.get()` / `.set()`. Handles 32-bit totalizer split per config.
- `physics.py` — configurable-Hz process physics (default 5 Hz).
  Integrates flow, drains tanks, updates totalizers, enforces the
  interlock chain + ESD latch, drives switch discrete-inputs.
- `state_machine.py` — event dispatcher. Consumes events from
  `replay.py`, writes audit rows to Postgres via a batched 1 Hz
  flusher, drives the "compliant operator" coils/holding-regs so
  physics can dispense. Samples `tank_level_snap` at 1 Hz.
- `replay.py` — reads the JSONL timeline, schedules events by
  `t_offset_s * (1/speed)`, loops on wrap.

Deterministic: `generate_timeline.py --seed X --hours 48` at
`/opt/fuelsim/bin/generate_timeline.py` — same seed → byte-identical
JSONL → byte-identical audit rows.

Talk to fuelsim over `172.16.46.17:502` (field-bus). ff-plc-1's
OpenPLC master polls this address per build sheet §2 remote-I/O topology.
