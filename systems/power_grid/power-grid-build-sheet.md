# Power Grid Status Simulation — Build Sheet

**Subsystem:** Base electrical distribution status & control — utility feed, distribution feeders, backup generation, ATS
**Enclave:** `power` (OT / RVIN) — segmented Purdue chain off the OT gateway: Power-PLC `172.16.47.8/29` · Power-Sim `172.16.49.0/24` · Power-Extra `172.16.48.0/24`
**Management:** all hosts dual-homed onto `10.255.240.0/20` (gw `10.255.240.1`); power mgmt block `10.255.247.0/24`; Ansible reaches every host over its mgmt NIC
**Primary protocol:** DNP3 (IEEE 1815) over TCP/20000 — the authentic SCADA protocol for North American electric power
**Integration bus:** MQTT (Mosquitto) — bridges the DNP3 master to the HMI and historian, since the web HMI does not speak DNP3
**Deployment:** 100% Ansible, idempotent, resettable between exercise iterations

Structure mirrors the fuel-farm sheet: host specs, the DNP3/MQTT topology, the electrical model, the DNP3 point map, the HMI/historian/audit design, the simulator and 48-hour replay harness, the Ansible layout with dual-home connection vars, firewall rules, acceptance tests, and the DNP3-specific attack surface. Build order is at the end.

---

## 1. Host inventory

Dual-homed: `eth0` on a production OT segment, `eth1` on management (`10.255.240.0/20`, power block `10.255.247.0/24`). `ansible_host` is the **mgmt** IP. Standard hosts run `net.ipv4.ip_forward=0`; the chain bridges (`power-plc`, `power-sim`, and the shared `control-room-hmi`) forward between their adjacent OT segments **only**. `bs-modbus-gateway` (VyOS) routes the OT chain up to `bs-ops-fw` (pfSense). All Linux hosts run **Ubuntu 24.04 LTS Server** (headless, `systemd-networkd` renderer — matches the netplan in §9). **Platform constraint:** the `.2` host address is reserved in every subnet — never assign `.2` to any host (lesson learned; the shared `control-room-hmi` therefore sits at `172.16.45.3`, not `.45.2`).

| Host | Role / Purdue | `eth0` (production) | `eth1` (mgmt) | OS | vCPU / RAM / disk | Core package |
|---|---|---|---|---|---|---|
| `power-sim` | pandapower model + replay harness (L0); bridges Power-Sim↔Power-Extra | 172.16.49.18 (Power-Sim /24) + 172.16.48.18 (Power-Extra) | 10.255.247.18 | Ubuntu 24.04 LTS Server | 2 / 4 GB / 20 GB | Python 3.12 venv, pandapower, dnp3-python |
| `power-plc` | DNP3 outstations RTU-1/RTU-2 (L1); bridges Power-PLC↔Power-Sim | 172.16.47.10 (Power-PLC /29) + 172.16.49.10 (Power-Sim) | 10.255.247.10 | Ubuntu 24.04 LTS Server | 2 / 2 GB / 20 GB | dnp3-python (outstation) |
| `power-scada` | DNP3 master + Mosquitto MQTT broker (L2) | 172.16.47.11 (Power-PLC /29) | 10.255.247.11 | Ubuntu 24.04 LTS Server | 2 / 2 GB / 20 GB | DNP3 master (Python), mosquitto |
| `control-room-hmi` | FUXA single-line HMI (L2) — **shared with fuel**, bridge host | 172.16.47.9 (Power-PLC /29) + 172.16.45.3 (MTU) | 10.255.240.173 | Ubuntu 24.04 LTS Server | 2 / 4 GB / 20 GB | FUXA (`frangoteam/fuxa`) |
| `power-hist` | Historian — Telegraf + InfluxDB 2.7 + Grafana (L2, **in-enclave**) | 172.16.49.20 (Power-Sim /24) | 10.255.247.20 | Ubuntu 24.04 LTS Server | 2 / 4 GB / 60 GB | telegraf, `influxdb:2.7`, grafana |
| `power-db` | Control/event audit DB — PostgreSQL 16 + TimescaleDB (L2) | 172.16.49.21 (Power-Sim /24) | 10.255.247.21 | Ubuntu 24.04 LTS Server | 2 / 4 GB / 40 GB | postgresql-16, timescaledb |

The audit DB is intentionally lighter than the fuel farm's (status/control, not transactional logistics). Engineering access is from the Engineering Control Center (`172.31.8.0/24`) through `bs-ops-fw`; an optional read-only historian replica lives at Engineering `172.31.8.5`. The RTU host (`power-plc`) reads the live field image from `power-sim` across the Power-Sim segment — a genuine field↔RTU tier below the SCADA bus.

---

## 2. DNP3 + MQTT topology

Unlike the fuel farm's two Modbus tiers, the power grid uses one external industrial tier (DNP3) plus an MQTT integration bus, because no open-source web HMI speaks DNP3 natively.

- **Field tier (L1↔L0):** `power-sim` (pandapower, Power-Sim `/24`) computes bus voltages, line flows, frequency, and generator output each tick and exposes the field image. `power-plc` (the RTU, Power-PLC `/29` + Power-Sim `/24`) reads that image across the Power-Sim segment and maps it to the §4 DNP3 points; RTU-1 and RTU-2 run as two DNP3 link addresses on `power-plc`.
- **SCADA bus — DNP3 (the attack surface):** `power-scada` runs the DNP3 **master** and polls the outstations on `power-plc:20000` over the Power-PLC `/29`. Integrity polls (Class 0) plus event classes 1/2/3, unsolicited responses on change. Controls go master→outstation via CROB (Control Relay Output Block) and analog output blocks; `power-plc` applies them back to the model on `power-sim`.
- **Integration bus — MQTT:** the master republishes all point values to Mosquitto on `power-scada:1883`. `control-room-hmi` (FUXA, same Power-PLC segment) and `power-hist` (Telegraf `mqtt_consumer`, one hop via the `power-plc` bridge) subscribe. Operator controls from FUXA publish to a command topic; the master issues the corresponding DNP3 control. (Same DNP3↔message-bus pattern as VOLTTRON's DNP3 Agent.)

```
pandapower (power-sim, Power-Sim/24) ──field image──> DNP3 outstations RTU-1/2 (power-plc, Power-PLC/29)
                                                              │  DNP3/TCP 20000  (attack surface)
                                                              ▼
                                                       DNP3 master + Mosquitto 1883 (power-scada)
                                                              ▲                 │
                                                control cmds  │                 │ telemetry
                                                              │        ┌────────┴─────────┐
                              FUXA (control-room-hmi) <───────┘                  └──> Telegraf → InfluxDB → Grafana (power-hist)
```

> **DNP3 library choice (decide early — see §13):** primary recommendation is **`dnp3-python` / pydnp3** (Python bindings to opendnp3) so the outstation and master integrate cleanly with the pandapower sim in one language. Caveat: the underlying opendnp3 reached end-of-life in 2022 and is maintenance-only — acceptable for a non-production training range, but flag it. The actively maintained alternative is **Step Function I/O's `dnp3`** (Rust, conformance-tested, supports **TLS** — valuable for a secure-vs-insecure DNP3 exercise) with C/C++/.NET/Java bindings but **no Python**, so it needs an IPC shim to the sim.

> **Fallback if MQTT is unwanted:** have the master republish to Modbus instead and point FUXA at it (FUXA speaks Modbus). Less idiomatic for status telemetry and loses the MQTT attack surface; prefer the MQTT design.

---

## 3. Electrical model

A scaled base distribution system, modeled in pandapower (balanced power flow, nameplate parameters, quasi-static time series):

- **Utility source** → main substation **S1**, main breaker **CB-MAIN** on the main bus (≈13.8 kV).
- **Three distribution feeders** off S1, each with a feeder breaker:
  - **F1 / CB-F1** → airfield & lighting loads
  - **F2 / CB-F2** → fuel farm loads
  - **F3 / CB-F3** → facilities / admin
- **Backup generation plant** (outstation RTU-2): two diesel gensets **G1, G2**, each with a generator breaker (**CB-G1, CB-G2**); an **ATS** transfers critical load to generator on utility loss.
- Measured quantities: per-bus voltage (kV), per-feeder MW/MVAR/amps, system frequency (Hz), generator MW/RPM/fuel %, energy counters (kWh), breaker operation counts.

**Normal → contingency sequence** modeled by the replay (§8): diurnal load on the three feeders; a utility-loss window that drops `UTILITY_AVAIL`, opens CB-MAIN, starts G1/G2, transfers the ATS, holds critical feeders on generator, then restores; plus a planned-maintenance feeder open and an optional fault inject.

---

## 4. DNP3 point map

Points are organized by DNP3 object group, indexed per type per outstation. Use **Analog Input float variations (g30v5)** for direct engineering units, or integer variations with the count scaling noted below. Binary controls use **CROB (g12)**; analog setpoints use **Analog Output Block (g41)**.

### RTU-1 — S1 Main Substation (DNP3 link address 1)

**Binary Inputs (g1):**

| Idx | Point | Description |
|---|---|---|
| 0 | `CB_MAIN_CLOSED` | Utility main breaker closed |
| 1 | `CB_F1_CLOSED` | Feeder 1 (airfield) breaker closed |
| 2 | `CB_F2_CLOSED` | Feeder 2 (fuel farm) breaker closed |
| 3 | `CB_F3_CLOSED` | Feeder 3 (facilities) breaker closed |
| 4 | `UTILITY_AVAIL` | Utility source healthy |
| 5 | `BUS_UNDERVOLT_ALM` | Main bus undervoltage alarm |
| 6 | `BUS_OVERVOLT_ALM` | Main bus overvoltage alarm |

**Analog Inputs (g30):**

| Idx | Point | Units / scaling (if integer) |
|---|---|---|
| 0 | `BUS_V_KV` | 0.01 kV/count |
| 1 | `FREQ_HZ` | 0.01 Hz/count |
| 2 | `MAIN_MW` | 0.1 MW/count |
| 3 | `MAIN_MVAR` | 0.1 MVAR/count |
| 4 | `F1_MW` | 0.1 MW/count |
| 5 | `F2_MW` | 0.1 MW/count |
| 6 | `F3_MW` | 0.1 MW/count |
| 7 | `F1_AMPS` | 1 A/count |

**Counters (g20):** `0 MAIN_KWH` (import energy), `1 CB_MAIN_OPS` (operation count).

**Binary Output / CROB (g12):** `0 CB_MAIN_CTRL`, `1 CB_F1_CTRL`, `2 CB_F2_CTRL`, `3 CB_F3_CTRL` (trip/close).

**Analog Output (g41):** `0 VREG_SETPOINT` (voltage-regulator setpoint).

### RTU-2 — Generation Plant / ATS (DNP3 link address 2)

**Binary Inputs (g1):**

| Idx | Point | Description |
|---|---|---|
| 0 | `G1_RUNNING` | Genset 1 running |
| 1 | `G2_RUNNING` | Genset 2 running |
| 2 | `CB_G1_CLOSED` | Gen 1 breaker closed |
| 3 | `CB_G2_CLOSED` | Gen 2 breaker closed |
| 4 | `ATS_ON_GEN` | ATS transferred to generator |
| 5 | `G1_FAULT` | Gen 1 fault |
| 6 | `G2_FAULT` | Gen 2 fault |

**Analog Inputs (g30):**

| Idx | Point | Units / scaling |
|---|---|---|
| 0 | `G1_MW` | 0.1 MW/count |
| 1 | `G2_MW` | 0.1 MW/count |
| 2 | `G1_FUEL_PCT` | 0.1 %/count |
| 3 | `G2_FUEL_PCT` | 0.1 %/count |
| 4 | `G1_RPM` | 1 rpm/count |
| 5 | `GEN_BUS_V_KV` | 0.01 kV/count |

**Counters (g20):** `0 G1_RUNHOURS`, `1 G2_RUNHOURS`.

**Binary Output / CROB (g12):** `0 G1_START_STOP`, `1 G2_START_STOP`, `2 CB_G1_CTRL`, `3 CB_G2_CTRL`, `4 ATS_TRANSFER_CMD`.

**Analog Output (g41):** `0 G1_MW_SETPOINT`, `1 G2_MW_SETPOINT`.

The full point list, indices, variations, scaling, and per-RTU DNP3 link addresses live in `group_vars/power.yml` as the single source of truth for the outstation config, the master config, the MQTT topic map, and the FUXA/Telegraf bindings.

---

## 5. HMI and status visualization

**A. FUXA — single-line operator HMI** (`control-room-hmi:1881`, MQTT client to `power-scada:1883`). Deploy the `frangoteam/fuxa` Docker image. Screens:
1. **Single-line diagram** — utility source, CB-MAIN, main bus with live kV/Hz, the three feeder breakers with per-feeder MW, the generation plant (G1/G2 run state, MW, fuel %), gen breakers, and ATS position; breaker symbols colored by open/closed, alarms annunciated.
2. **Generation & ATS detail** — genset status, fuel, run hours, start/stop and ATS-transfer controls (control requests published to the MQTT command topic).

**B. Grafana "Power Status" board** (`power-hist:3000`) — trends and history backed by InfluxDB (+ Postgres for the event/control log): bus voltage & frequency over time, per-feeder MW (diurnal curve), generator output, energy counters, and an events/controls table from the audit DB.

FUXA owns the real-time operator picture; Grafana owns trends and the audit view — the same separation of concerns used in the fuel farm.

---

## 6. Historian

**Stack:** Telegraf → InfluxDB 2.7 → Grafana on `power-hist`.

- **Telegraf** `inputs.mqtt_consumer` subscribes to the telemetry topics on `power-scada:1883`, parses JSON point payloads, writes to InfluxDB bucket `power`. (Telegraf has no native DNP3 input, which is why telemetry arrives via MQTT.)
- **InfluxDB 2.7 OSS** — API/UI on 8086; org `airfield`, bucket `power`, token in vault. **Pin `influxdb:2.7`** — the `latest` tag now resolves to InfluxDB 3 Core, a recent-data engine with a ~72-hour query window and partial InfluxQL, which is wrong for a historian.
- **Grafana** on 3000 — provisioned InfluxDB (Flux) + PostgreSQL datasources and the §5 dashboards.

---

## 7. Control & event audit database

**`power-db`:** PostgreSQL 16 + TimescaleDB. The `power_db` role applies the schema and makes the time-series tables hypertables.

```sql
-- Every DNP3 control operation (CROB / analog output), incl. who/where
CREATE TABLE dnp3_control_log (
  id          BIGSERIAL,
  ts          TIMESTAMPTZ NOT NULL DEFAULT now(),
  master_src  TEXT,        -- source IP/identity of the issuing master
  outstation  TEXT,        -- RTU-1 | RTU-2
  point_group INT,         -- 12 = CROB, 41 = analog output
  point_index INT,
  operation   TEXT,        -- TRIP|CLOSE|LATCH_ON|LATCH_OFF|SETPOINT
  value       NUMERIC,
  status      TEXT,        -- SUCCESS|TIMEOUT|DENIED
  requested_by TEXT,       -- hmi-operator | api | unknown
  PRIMARY KEY (id, ts)
);

-- Operational events: outages, ATS transfers, gen start/stop, faults, alarms
CREATE TABLE power_events (
  id       BIGSERIAL,
  ts       TIMESTAMPTZ NOT NULL DEFAULT now(),
  source   TEXT,           -- sim|master|hmi
  severity TEXT,           -- info|warn|alarm
  code     TEXT,
  message  TEXT,
  PRIMARY KEY (id, ts)
);

-- Independent breaker-state record (for reconciliation against the control log)
CREATE TABLE breaker_state_log (
  ts         TIMESTAMPTZ NOT NULL,
  breaker_id TEXT NOT NULL,   -- CB-MAIN|CB-F1|CB-F2|CB-F3|CB-G1|CB-G2
  state      TEXT NOT NULL    -- OPEN|CLOSED
);

SELECT create_hypertable('dnp3_control_log','ts');
SELECT create_hypertable('power_events','ts');
SELECT create_hypertable('breaker_state_log','ts');
```

**Reconciliation / detection:** every breaker state change in `breaker_state_log` should trace to either a logged control in `dnp3_control_log` or a modeled event in `power_events`. A breaker that changes state with no corresponding control or event is the signature of an out-of-band/spoofed command — a strong blue-team exercise, and a deliberate red-team objective.

---

## 8. Simulator & 48-hour replay harness

**`power-sim`** runs `powersim` (Python systemd service); the DNP3 outstations are hosted on **`power-plc`**, reading its field image:

1. **pandapower model** (`power-sim`) — buses, lines, transformers, three feeder loads, two gensets, breakers as switches. Each tick: apply current load setpoints + breaker/gen states, run power flow, publish bus voltages, line MW/MVAR/amps, frequency, generator MW as the field image.
2. **DNP3 outstations** (`power-plc`) — RTU-1 and RTU-2 read the field image and expose it as the §4 points; accept master controls (CROB/AO) and apply them back to the model (e.g., `CB_F2_CTRL=TRIP` opens the F2 switch → that load de-energizes on the next solve).
3. **Replay engine** (`power-sim`) — reads the 48-hour timeline and drives load setpoints and discrete events on a clock.

**Replay dataset.** `generate_power_timeline.py` (fixed `--seed`) emits `power_ops_timeline.jsonl` over 48 h: a diurnal per-feeder load curve with morning/evening peaks, plus discrete events. Event schema (one JSON object per line):

```json
{"t_offset_s": 0,     "event": "load_setpoint", "feeder": "F1", "mw": 1.8}
{"t_offset_s": 0,     "event": "load_setpoint", "feeder": "F2", "mw": 0.9}
{"t_offset_s": 28800, "event": "utility_loss"}
{"t_offset_s": 28815, "event": "gen_start", "gen": "G1"}
{"t_offset_s": 28820, "event": "ats_transfer", "to": "GEN"}
{"t_offset_s": 32400, "event": "utility_restore"}
{"t_offset_s": 32420, "event": "ats_transfer", "to": "UTILITY"}
{"t_offset_s": 54000, "event": "breaker_open", "breaker": "CB-F3", "reason": "planned_maint"}
```

`powersim` replay mode scales `t_offset_s` by `replay.speed`, fires each event into the model + outstation points, and logs operational events to Postgres. The historian captures everything via the master→MQTT→Telegraf path as if live. **Loopable** (offset timestamps by 48 h on wrap); deterministic with a fixed seed for repeatable assessment.

---

## 9. Ansible

**Inventory group** `power` → these hosts (the shared `control-room-hmi` also belongs to the fuel chain); `ansible_host` = mgmt IP. `group_vars/power.yml` holds the three OT segment CIDRs + gateways, the mgmt block, the §4 point map with per-RTU link addresses and scaling, MQTT topic map, InfluxDB org/bucket, `replay.speed`, the electrical model parameters, and vault references for all credentials (FUXA admin, InfluxDB token, Grafana admin, Postgres roles, MQTT creds). Per-host production IP/prefix/gateway live in `host_vars`.

**Roles:**
- `common` — dual-home netplan template, `ip_forward=0` (bridges excepted), NTP to the range time source, base hardening, Wazuh agent.
- `power_db` — PostgreSQL 16 + TimescaleDB, apply §7 schema.
- `power_sim` — Python env, pandapower; deploy `powersim` + replay as a systemd unit; place `power_ops_timeline.jsonl`; set `replay.speed`.
- `power_plc` — `dnp3-python` outstations (RTU-1/RTU-2) as a systemd unit, reading the `power-sim` field image.
- `power_scada` — DNP3 master service (polls RTU-1/RTU-2, republishes to MQTT, subscribes to the command topic); Mosquitto broker with auth.
- `power_hmi` — FUXA (`frangoteam/fuxa`) container (shared `control-room-hmi`); import the single-line project + MQTT device config; set admin auth.
- `power_historian` — Telegraf `mqtt_consumer` config; InfluxDB `influxdb:2.7` with org/bucket/token bootstrap; Grafana datasources + dashboards.

**Dual-home netplan (in `common`, templated per host):**

```yaml
network:
  version: 2
  ethernets:
    eth0:                      # production OT segment (/29 Power-PLC, /24 Power-Sim/Extra)
      addresses: [ "{{ prod_ip }}/{{ prod_prefix }}" ]
      routes:
        - to: default
          via: "{{ prod_gw }}"     # OT segment gateway (bs-modbus-gateway / chain bridge)
    eth1:                      # management — reaches ONLY the mgmt supernet
      addresses: [ "{{ mgmt_ip }}/20" ]
      routes:
        - to: 10.255.240.0/20
          via: 10.255.240.1
```

Plus a sysctl drop-in `net.ipv4.ip_forward=0` on standard hosts; the chain bridges (`power-plc`, `power-sim`, `control-room-hmi`) enable forwarding between their adjacent OT segments only, never onto management. `ansible_host = {{ mgmt_ip }}`. On Ubuntu cloud images the `common` role must neutralize cloud-init networking (`/etc/netplan/50-cloud-init.yaml`) so it doesn't override this dual-home config; netplan itself is native on Ubuntu, so no extra package is needed.

**Intra-enclave deploy order** (runs after the network + foundation tiers in `site.yml`):
`power_db` → `power_sim` (field image up) → `power_plc` (outstations read the sim) → `power_scada` (master needs outstations reachable; broker up) → `power_historian` (Telegraf needs the broker) → `power_hmi` (FUXA needs the broker) → Grafana dashboard provisioning.

---

## 10. Firewall & segment ACLs

Two enforcement points: `bs-ops-fw` (pfSense) for IT↔OT at the L3.5 boundary, and `bs-modbus-gateway` (VyOS) for intra-OT segment ACLs. Default-deny; allow only:

| Source | Destination | Port/proto | Enforced at | Purpose |
|---|---|---|---|---|
| Engineering WS (`172.31.8.0/24`) | `power-plc` | 20000/tcp | ops-fw + gw | DNP3 outstation test/diag |
| Engineering WS (`172.31.8.0/24`) | `power-scada` | 1883/tcp | ops-fw + gw | MQTT / master admin |
| Engineering WS (`172.31.8.0/24`) | `control-room-hmi` | 1881/tcp | ops-fw + gw | FUXA admin |
| Engineering WS (`172.31.8.0/24`) | `power-hist` | 3000/tcp, 8086/tcp | ops-fw + gw | Grafana / InfluxDB |
| Engineering WS (`172.31.8.0/24`) | `power-db` | 5432/tcp | ops-fw + gw | DB admin |
| `power-hist` | RO historian replica (`172.31.8.5`) | 8086/tcp | ops-fw | Optional replication up |
| `power-scada` | `power-plc` :20000 | 20000/tcp | gw | DNP3 master↔outstation (Power-PLC /29) |
| `power-plc` | `power-sim` field image | sim iface | gw | RTU reads model (Power-Sim /24) |
| `control-room-hmi`, `power-hist` | `power-scada` :1883 | 1883/tcp | gw | MQTT telemetry / control bus |
| — | enterprise / flight ops | any | ops-fw | **Denied** (no flat path into OT) |
| — | other OT subsystems (fuel, lighting, PACS) | any | gw | **Denied** except the shared HMI bridge |

DNP3 (20000) and MQTT (1883) stay intra-OT. The mgmt plane (`10.255.240.0/20`) is reachable only from the Ansible control node, enforced on the platform management network, never exposed to a scenario.

---

## 11. Acceptance tests

- **DNP3 baseline:** master integrity poll (Class 0) returns all RTU-1/RTU-2 points; event classes 1/2/3 and unsolicited responses fire on change.
- **Control path:** issue CROB `CB_F2_CTRL=TRIP` → outstation opens the switch → pandapower shows F2 de-energized → MQTT/FUXA/historian reflect it → operation appears in `dnp3_control_log` and the open state in `breaker_state_log`.
- **Contingency sequence:** `utility_loss` → CB-MAIN opens, gens start, ATS transfers, critical feeders held; verify the full sequence in HMI, historian, and `power_events`.
- **Tracking:** bus voltage and frequency respond to the diurnal load curve; generator MW follows load while on generator.
- **Replay:** 48 h timeline completes, loops cleanly, and a fixed-seed run reproduces identical events and audit rows.
- **Reconciliation:** energy counters track integrated MW; every `breaker_state_log` change maps to a control or event (no orphan state changes).

---

## 12. Training value & attack surface

- **Unauthenticated DNP3 (20000).** DNP3 has no security by default; anyone who can speak master to the outstation can issue CROB controls — trip CB-MAIN or a feeder, start/stop gensets, force an ATS transfer — with real consequence (drop the airfield feeder, black-start scenarios). The canonical OT-attack centerpiece for this subsystem.
- **Telemetry/data injection.** Spoofed unsolicited responses or forged analog values can mask an outage or fake a voltage/frequency excursion to provoke an unnecessary operator action.
- **Master impersonation / MITM** on the DNP3 TCP session.
- **MQTT bus abuse.** If the broker is left unauthenticated (a deliberate scenario knob), an attacker can publish fake telemetry (poison HMI + historian) or publish to the command topic to drive controls without touching DNP3 at all.
- **Web credential attacks** on FUXA (1881) and Grafana (3000).
- **Audit tampering.** Deleting rows from `dnp3_control_log` to hide who tripped a breaker — detectable via the three-record divergence (sim ground truth ↔ historian ↔ control log vs `breaker_state_log`).
- **Secure-vs-insecure contrast.** Stand a scenario up on plain DNP3, then on DNP3-over-TLS (Step Function I/O library) to teach the difference. Zeek/Malcolm DNP3 parsers in the SOC enclave detect anomalous function codes (e.g., a CROB from an unexpected source).

---

## 13. Build-time version checklist

- **OS baseline:** Ubuntu 24.04 LTS Server — PostgreSQL 16 is native (no PGDG repo); point the InfluxData, Grafana, and TimescaleDB apt repos and Docker at the `noble` codename. The `common` role must neutralize cloud-init networking (override/remove `/etc/netplan/50-cloud-init.yaml`) so the dual-home config doesn't collide; netplan itself is native on Ubuntu.
- **DNP3 library** — the real decision for this subsystem:
  - `dnp3-python` / pydnp3 (Python bindings to opendnp3): easiest integration with the pandapower sim; **opendnp3 is EOL/maintenance-only since 2022** — fine for a training range, flag it. On Ubuntu 24.04 (Python 3.12) verify a 3.12 wheel exists or build from source; if not, run the DNP3 hosts (`power-plc`, `power-scada`, `power-sim`) on a Python 3.11 venv (deadsnakes/pyenv). This is the main friction point of the 24.04 move for this subsystem.
  - Step Function I/O `dnp3` (Rust): actively maintained, conformance-tested, **TLS support** for secure-DNP3 scenarios; bindings C/C++/.NET/Java, **no Python** (needs an IPC shim to the sim).
  - FreyrSCADA DNP3 (outstation + master simulators, incl. Python): quick to stand up; confirm licensing before relying on it.
  Pick based on whether single-language Python integration or maintenance/TLS conformance matters more for your scenarios.
- **pandapower** — confirm current release and the time-series module; pin the version.
- **FUXA** — deploy the Docker image; it has **no DNP3 driver** (hence the MQTT gateway); confirm the MQTT device-config import format for the release pulled.
- **Mosquitto** — enable authentication by default; an unauthenticated broker should be a deliberate scenario choice, not an accident.
- **InfluxDB** — pin `influxdb:2.7`; do **not** use `:latest` (now InfluxDB 3 Core; ~72 h query window, partial InfluxQL).
- **Telegraf** — confirm `mqtt_consumer` topic/JSON field parsing matches the master's publish schema; there is no native DNP3 input.
- **pfSense automation** — the §10 rules assume the pfSense automation path settled earlier (community collection vs templated `config.xml`).
