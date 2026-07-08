# fuelsim

Fuel Farm Simulator scaffold (build sheet §8). Three components:

- `fuelsim.py` — entry point. Launches the three worker loops.
- `modbus.py` (**TODO**) — pymodbus Modbus TCP slave for the field-bus tags.
- `physics.py` (**TODO**) — physics model: flow integration, tank draw,
  totalizer increment, header pressure response, tank low-level cutout.
- `state_machine.py` (**TODO**) — logistics: truck state transitions +
  Postgres `truck_queue` / `load_txn` / `delivery_txn` writes + mirror
  active assignments into PLC holding registers (`LR*_ACTIVE_TRUCK`, etc.).
- `replay.py` (**TODO**) — reads `fuel_ops_timeline.jsonl`, scales by
  `replay.speed`, fires events into the state machine. Loops.

Deterministic: fixed `--seed` in `fuelsim.yml` -> identical audit rows across runs.

Talk to fuelsim over `172.16.46.17:502` (field-bus). It expects the
PLC to poll at that address per build sheet §2 remote-I/O topology.
