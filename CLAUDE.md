# CLAUDE.md — Blackstone Auxiliary Field Cyber Range

Cross-cutting contract for building this project with Claude Code. Aligned to the **JCTE vCity Military Base
network map (Baseline DEV v21, domains `blackstone.mil` / `fops.blackstone.mil`)**, with the project owner's decisions
applied (see §3). The map is authoritative for topology/zones/production-addressing; this file encodes that
plus the owner's overrides, conventions, and guardrails. Per-subsystem detail lives in `build-sheets/`.
Authority order: **owner decisions (§3) → map → CLAUDE.md → build sheet**. Build sheets that predate these
decisions need a reconciliation pass (§12).

---

## 1. Project purpose

A virtual military airfield, built as a **cyber range** on a SimSpace-style platform to train and assess
military cyber-defense personnel. Fidelity matters: it must look and behave like a real base IT/OT estate.
Everything **deploys and re-deploys via Ansible**, idempotently, resetting to known-good between exercises.
**Defensive** range — build legitimate target/defense infrastructure, not attack tooling (§11).

---

## 2. Scope

Foundation: Vyatta/VyOS backbone + **pfSense** firewalls, DMZ, range services, the control plane, and a
**multi-tier Active Directory** across `blackstone.mil` and `fops.blackstone.mil` (§4); blue-team SOC instrumentation.

Five bespoke systems (mapped to enclaves in §7):
1. **Weather radar** — 2 days of recorded weather data fed in and visualized (Flight Control Center).
2. **Air Traffic Control** — terminals + 2 days of flight data visualized (Flight Control Center).
3. **Access control** — Leosac with mock door mechanisms and pin pads (Physical Security).
4. **Fuel farm (POL)** — trucks queue → load → deliver to aircraft; PLCs, OT protocol, HMI, **in-enclave
   historian**; truck/tank/withdrawal/aircraft visualization; audit DB (OT / RVIN).
5. **Power grid** — distribution status simulation with HMI, over **DNP3** (OT / RVIN).

---

## 3. Owner decisions (authoritative — applied throughout)

| # | Decision | Effect |
|---|---|---|
| 1 | **Control plane = `10.255.240.0/20`** | Every host's management NIC + Ansible live here (gw `10.255.240.1`). The map's `10.10.0.0/16` SimSpace control net is **superseded** for our build. |
| 2 | **Firewalls = pfSense** | `bs-edge-fw` (perimeter) and `bs-ops-fw` (L3.5 boundary) are pfSense. Routers (`edge/core/ops/sec-rtr`) and `bs-modbus-gateway` stay VyOS/Vyatta. |
| 3 | **Power = DNP3** | Power OT uses DNP3 (+ MQTT bridge to HMI/historian), overriding the map's Modbus-only OT. |
| 4 | **OT = segmented** | Keep the map's Purdue chain: `/29` control segments + `/24` sim segments + dual-homed bridge hosts. Chosen for realism (the range's purpose) and map-consistency. |
| 5 | **Historian in-enclave** | Primary historian (Telegraf + InfluxDB 2.7 + Grafana) lives in each OT subsystem's sim `/24`. The Engineering `bs-ro-hist 172.31.8.5` becomes an **optional** read-only boundary replica. |
| 6 | **Linux baseline = Ubuntu 24.04 LTS Server** | All non-Windows VMs run Ubuntu 24.04 LTS Server, headless (replaces Debian 12; supported to 2029; `NetworkManager` renderer (driven via netplan)). PostgreSQL 16 is native; netplan is native; the `common` role neutralizes cloud-init networking; third-party apt repos (PGDG-not-needed, InfluxData, Grafana, TimescaleDB, Docker) target the `noble` codename. Run the Python `dnp3-python` hosts on a 3.11 venv if no 3.12 wheel (power sheet §13). |
| 7 | **`.2` reserved platform-wide** | The platform reserves the `.2` host address in every subnet — never assign `.2` to any host (lesson learned). All hosts the map placed on `.2` have been shifted off it (§6); future build sheets must avoid `.2`. |
| 8 | **Routing model = OSPF + eBGP-edge + static-at-L3.5** | Matches PowerPlant's post-shift architecture. OSPF area 0 is the internal IGP across all corp links + LAN interfaces; eBGP only at `bs-edge-rtr` ↔ `bs-edge-fw` (AS 65002 ↔ AS 65001); STATIC-ONLY at the L3.5 boundary `bs-ops-fw` ↔ `bs-modbus-gateway` (default-deny, OT umbrella `172.16.0.0/16` via `172.31.1.18`). `bs-ops-fw` `redistribute_static` into OSPF so the umbrella is reachable upstream. |
| 9 | **pfSense automation = `pfsensible.core` + `php -r` escape hatches** | Reuse PowerPlant's `roles/pfsense_firewall` verbatim (it carries the escape hatches for `defaultgw4`, NAT outbound mode, `installedpackages/frr`, `syslog`). Copy it into `airfield-range/roles/` per the role-sourcing policy. |
| 10 | **pfSense NIC position = FIRST** | Mgmt on `vmx0`; data-plane on `vmx1..vmxN` (matches PowerPlant's pfSense 2.8.1 image after the platform mgmt-NIC fix). Both `bs-edge-fw` and `bs-ops-fw` host_vars + the blueprint reflect this. |

---

## 4. Multi-tier Active Directory design

Two domains, separated by function:

| Domain | Role | DCs | Member enclaves |
|---|---|---|---|
| `blackstone.mil` | Enterprise / base | `bs-dc01` `172.31.2.7`, `bs-dc02` `172.31.2.8` | Services, Headquarters, IT, Supply; (Engineering, Security/SOC — best-judgment, confirm) |
| `fops.blackstone.mil` | Flight operations | `bs-ops-dc01` `172.31.3.10`, `bs-ops-dc02` `172.31.3.12` | Flight-Ops-Services, Flight-Control-Center, Flight-Ops-Users |

- **Trust** between the domains is automatic and bidirectional — `fops.blackstone.mil` is a **child domain** in the same AD forest as `blackstone.mil` (forest root). No manual cross-forest trust setup needed; parent/child trust is built-in and transitive. Users in either domain can authenticate to resources in the other by default.
- **Tiered admin overlay (recommended):** Tier 0 DCs/identity, Tier 1 member servers, Tier 2 workstations.
  Windows Event Collectors (`bs-wec1 172.31.2.17`, `bs-ops-wec1 172.31.3.17`) forward to the SOC SIEM — keep
  that the audited log path.
- **OT is not domain-joined** (ICS practice); engineering workstations / control-room HMI may be — default off.

---

## 5. Network topology & zones

```
INTERNET ──199.252.163.0/30── bs-edge-rtr(VyOS) ── bs-edge-fw(pfSense) ──┬── DMZ (172.31.12.0/24)
                                                                         └──172.31.1.4/30── bs-core-rtr(VyOS)
   bs-core-rtr ─┬─ Services / Headquarters / IT / Supply                       [blackstone.mil ENTERPRISE CORE]
                └─172.31.1.8/30─ bs-ops-rtr(VyOS)
   bs-ops-rtr ──┬─ Flight-Ops-Services / Flight-Control-Center / Flight-Ops-Users   [fops.blackstone.mil]
                └─172.31.1.12/30─ bs-ops-fw(pfSense)         ← L3.5 boundary, default-deny
   bs-ops-fw ───┬─ Engineering-Control-Center (172.31.8.0/24)                  [ENGINEERING]
                ├─172.31.1.20/30─ bs-sec-rtr(VyOS) ─┬─ Physical-Security (172.31.11.0/24)
                │                                    └─ SOC (172.31.7.0/24)    [SECURITY / SOC]
                └─172.31.1.16/30─ bs-modbus-gateway(VyOS) ─ OT chain (172.16.x)   [OT / AIRFIELD · RVIN]
```

- **`bs-edge-fw` (pfSense)** = perimeter; **`bs-ops-fw` (pfSense)** = the Purdue L3.5 boundary (default-deny;
  only brokered jump access + historian replication cross it).
- **`bs-modbus-gateway` (VyOS)** = IT↔OT routing/boundary gateway and head of the OT chain. (Name is a baseline
  artifact; it routes both the fuel Modbus segments and the power DNP3 segments.)

---

## 6. Addressing plan

**Point-to-point /30 links:** `199.252.163.0/30` (edge-rtr↔edge-fw), `172.31.1.4/30` (edge-fw↔core-rtr),
`172.31.1.8/30` (core-rtr↔ops-rtr), `172.31.1.12/30` (ops-rtr↔ops-fw), `172.31.1.16/30` (ops-fw↔modbus-gw),
`172.31.1.20/30` (ops-fw↔sec-rtr).

**Production subnets (`172.31.0.0/16`):**

| Enclave | CIDR | Domain | Key hosts |
|---|---|---|---|
| DMZ | `172.31.12.0/24` | — | ftp .6, www .3, smtp .4, ns1 .5 |
| Services | `172.31.2.0/24` | blackstone.mil | dc01 .7, dc02 .8, mail01 .6, file01 .10, file02 .11, wec1 .17 |
| Headquarters | `172.31.14.0/24` | blackstone.mil | 13× Win10 (.3–.15) |
| IT | `172.31.15.0/24` | blackstone.mil | 5× Win10 (.3–.7) |
| Supply | `172.31.16.0/24` | blackstone.mil | 7× Win10 (.3–.9) |
| Flight-Ops-Services | `172.31.3.0/24` | fops.blackstone.mil | dc01 .10, dc02 .12, file01/02, wec1 .17, app01–03 |
| Flight-Control-Center | `172.31.5.0/24` | fops.blackstone.mil | atc-radar .10, atc-weather .11, atc-station .14, flight01–05 |
| Flight-Ops-Users | `172.31.6.0/24` | fops.blackstone.mil | 12× Win10 (.3–.14) |
| Engineering-Control-Center | `172.31.8.0/24` | (TBD) | 5× wkstns, RO historian replica `bs-ro-hist` .5 (optional, §3.5) |
| Physical-Security | `172.31.11.0/24` | (TBD) | access points 1–4, access-ctlr .48 |
| SOC | `172.31.7.0/24` | (TBD) | siem .15, syslog .13, openvas .16, analyst1–10 |

**OT / Airfield (RVIN) — segmented Purdue chain (`172.16.0.0/16`):**

| Segment | CIDR | Members | Gateway / bridge |
|---|---|---|---|
| MTU (control room) | `172.16.45.0/29` | control-room-hmi (FUXA) `.45.3` | modbus-gateway `.45.1` |
| Fuel Farm PLC | `172.16.45.8/29` | ff-plc-1 (OpenPLC) `.45.10` | modbus-gateway `.45.9` |
| Fuel Farm Sim | `172.16.46.0/24` | fuel-sim `.46.17`; **[+] historian `.46.18`, audit DB `.46.19`** | modbus-gateway `.46.16` |
| Power PLC | `172.16.47.8/29` | power-plc / DNP3 outstation `.47.10`, hmi `.47.9` | control-room-hmi bridges |
| Power Sim | `172.16.49.0/24` | power-sim `.49.18`, power-plc `.49.10`; **[+] DNP3 master/MQTT, historian, audit DB** | power-plc bridges |
| Power Extra Sim | `172.16.48.0/24` | power-sim `.48.18` | power-sim bridges |

Chain: `ops-fw(pfSense) → modbus-gateway → {MTU/HMI, Fuel-PLC, Fuel-Sim}`, then
`control-room-hmi → Power-PLC → power-plc host → Power-Sim → power-sim host → Power-Extra`. Bridge hosts are
dual-homed across adjacent levels and forward between them on purpose (§8). `[+]` = additions for the
in-enclave historian/audit DB; exact host IPs finalize in the reconciled build sheets.

**Control plane (`10.255.240.0/20`, gw `10.255.240.1`):** every host has a management NIC here; Ansible control
node at `10.255.240.157` (per blueprint). Mgmt IPs are assigned **sequentially in blueprint order** starting at
`10.255.240.100` — fuel + power OT hosts are interleaved with the rest of the project's mgmt allocation rather
than living in dedicated `/24` blocks. (Earlier drafts proposed per-enclave `.245.x` / `.247.x` carve-ups; the
sequential assignment supersedes them.) Exact per-host control IPs live in `host_vars`.

**Platform reservation (lesson learned):** the `.2` host address is reserved by the platform in **every** subnet (production, OT, and management) — never assign `.2` to any host. Baseline-map hosts that the map placed on `.2` (DMZ `ftp`, `mail01`, and the Win10 workstation ranges, plus the shared `control-room-hmi`) have been shifted off `.2` above; honor this in all future build sheets.

---

## 7. Bespoke systems — enclave mapping & addressing

`[map]` = host defined in the map; `[+]` = best-judgment addition inside the map's subnets.

**Weather radar** — `fops.blackstone.mil`, Flight-Control-Center `172.31.5.0/24`:
`bs-atc-weather` **[map]** `172.31.5.11` (NEXRAD replay + METAR/TAF); `[+]` feed/replay injector `.5.12`;
displays on `atc-station .14` + flight wkstns `.3–.7`. Control `10.255.242.x`.

**Air Traffic Control** — same enclave `172.31.5.0/24`:
`bs-atc-radar` **[map]** `.5.10` (ADS-B replay → scope), `bs-atc-station` **[map]** `.5.14`, flight01–05
**[map]** `.5.3–.5.7`; `[+]` ADS-B 48 h injector `.5.13`. Control `10.255.242.x`.

**Access control (Leosac)** — Physical-Security `172.31.11.0/24`:
`bs-access-ctlr` **[map]** `.11.48` (Leosac), door points 1–4 **[map]** `.11.3–.11.6`; `[+]` reader/pin-pad
sims `.11.10–.11.13`, mock door-mechanism sim `.11.20`. Control `10.255.243.x`.

**Fuel farm (POL)** — OT, segmented, Modbus TCP:
`ff-plc-1` **[map]** `172.16.45.10` (OpenPLC); `fuel-sim` **[map]** `172.16.46.17` (pymodbus);
`control-room-hmi` **[map]** `172.16.45.3` (FUXA, shared); `[+]` historian (Telegraf/InfluxDB 2.7/Grafana)
`172.16.46.18`, audit DB (PostgreSQL/TimescaleDB) `172.16.46.19`. Optional RO replica `172.31.8.5`.
Control: sequential within `10.255.240.0/20` (see fuel build sheet §1 for exact mgmt IPs).

**Power grid** — OT, segmented, **DNP3 (+ MQTT)**:
`power-plc` **[map]** `172.16.47.10` (DNP3 outstation/controller), hmi `.47.9` (shared control-room-hmi);
`power-sim` **[map]** `172.16.49.18` (pandapower + DNP3 outstation), extra sim `172.16.48.18`; `[+]` DNP3
master + Mosquitto broker, historian, and event/audit DB in the Power-Sim/Extra `/24`s. DNP3 :20000 and MQTT
:1883 stay intra-OT; the historian collects via the master→MQTT→Telegraf path. Control: sequential within `10.255.240.0/20` (per-host mgmt IPs to be assigned when power hosts are added to the blueprint).

---

## 8. Dual-homing & control-plane contract

Every host is dual-homed: a **production** NIC (its enclave subnet) and a **management** NIC on
`10.255.240.0/20`. Ansible connects over management (`ansible_host` = management IP).

- Standard endpoints: **`net.ipv4.ip_forward=0`** — no bridging between planes.
- **OT bridge hosts are the exception:** `bs-modbus-gateway`, `bs-control-room-hmi`, `bs-power-plc`,
  `bs-power-sim` carry multiple production NICs and **forward between their adjacent OT segments on purpose**
  (the Purdue chain). Enable forwarding only on these, only between their listed segments, never onto management.
- Management plane is out-of-band and **hidden from trainees** — never expose `10.255.240.0/20` to a data-plane
  service or scenario; never route between production and management.

```yaml
# common role netplan (production prefix is /29 or /24 per §6)
network:
  version: 2
  ethernets:
    eth0:                                # production — default route
      addresses: [ "{{ prod_ip }}/{{ prod_prefix }}" ]
      routes: [ { to: default, via: "{{ prod_gw }}" } ]
    eth1:                                # management
      addresses: [ "{{ mgmt_ip }}/20" ]
      routes: [ { to: 10.255.240.0/20, via: 10.255.240.1 } ]
```

netplan is native on Ubuntu 24.04, so no extra package is needed; the `common` role must neutralize the
cloud-image default (`/etc/netplan/50-cloud-init.yaml`) so it doesn't override this dual-home config.

---

## 9. Tech stack (verified; pin at build time)

| Layer | Component | Notes |
|---|---|---|
| Linux baseline | **Ubuntu 24.04 LTS Server** | all non-Windows VMs, headless (`NetworkManager` renderer (driven via netplan)); PG16 native, netplan native; repos target `noble`; `.2` host address reserved platform-wide |
| Routers + OT gateway | **VyOS / Vyatta** | `vyos.vyos` collection |
| Firewalls | **pfSense** | `bs-edge-fw`, `bs-ops-fw`; automation via `pfsensible.core` **or** templated `config.xml` (verify coverage — §12) |
| Enterprise | Windows + AD (2 domains) | `ansible.windows`, `microsoft.ad`; WEC → SOC |
| OT controller | **OpenPLC v4** | Modbus TCP :502 (fuel) / DNP3 outstation (power); web :8080, rotate `openplc/openplc` |
| OT field sim | pymodbus (fuel), **pandapower** (power) | feed the controllers |
| Power protocol | **DNP3** :20000 + **Mosquitto MQTT** :1883 bridge | master ↔ HMI/historian; DNP3 lib choice open (§12): `dnp3-python` (Python, EOL opendnp3) vs Step Function I/O `dnp3` (Rust, TLS) |
| HMI | **FUXA** (`frangoteam/fuxa`) :1881 | shared `control-room-hmi` (AIRFIELD-HMI) |
| Historian | **InfluxDB 2.7** + Telegraf + Grafana | **in-enclave** (sim `/24`); pin `influxdb:2.7` (never `:latest` = v3 Core, ~72 h window); optional RO replica at Engineering `172.31.8.5` |
| Audit DB | PostgreSQL 16 + TimescaleDB | in the OT sim `/24` segments |
| PACS | Leosac | controller `.48`, door points, pin pads, mock door sim |
| SOC / SIEM | **Splunk Enterprise** (`splunk` + `splunk-es` from PowerPlant), syslog, OpenVAS | Splunk universal forwarder on every Linux/Windows host; Elastic / Security Onion under evaluation as future add-on stacks |
| Replay | Python harnesses | 48 h deterministic, loopable, seeded datasets |

---

## 10. Repository layout & deployment

```
airfield-range/
├── CLAUDE.md
├── ansible.cfg                   # ansible_host = management IP (10.255.240.0/20)
├── requirements.yml              # vyos.vyos, pfsensible.core, ansible.windows, microsoft.ad, community.*
├── site.yml                      # imports tier playbooks IN ORDER
├── hosts                         # INI inventory at top level (ss-pp-ab convention);
│                                  #   groups per enclave (see below); ansible_host = mgmt IP per host_vars
├── group_vars/
│   ├── all.yml  vault.yml  net.yml
│   ├── blackstone.yml fops.yml dmz.yml eng.yml soc.yml pacs.yml
│   └── fuel.yml power.yml weather.yml atc.yml
├── host_vars/                    # exact production + management IPs per host
├── playbooks/
│   ├── 00-network.yml            # VyOS routers + pfSense firewalls FIRST
│   ├── 10-foundation.yml         # NTP, both AD domains + trust, CA, DNS, DHCP
│   ├── 20-enterprise.yml         # blackstone: Services/HQ/IT/Supply, DMZ
│   ├── 30-fops.yml               # fops: Services/FCC/Users (incl. ATC, weather)
│   ├── 40-soc.yml                # SOC sensors, WEC subscriptions, Splunk indexer + UF rollout
│   ├── 50-pacs.yml               # Leosac + door sims
│   ├── 60-ot.yml                 # modbus-gw → MTU/HMI → PLCs → sims → in-enclave historians (fuel, power)
│   └── 70-injection.yml          # replay harnesses (weather, ATC, fuel, power)
├── roles/                        # one role per component (common, net_vyos, net_pfsense, ad_dc,
│                                 #   fuxa_hmi, openplc, dnp3_outstation, historian, leosac, power_sim, ...)
├── build-sheets/                 # AUTHORITATIVE per-subsystem specs (reconcile to §3 — see §12)
│   ├── fuel-farm-build-sheet.md  # ✓ (re-address to segmented)
│   └── power-grid-build-sheet.md # ✓ (re-address to segmented; DNP3 retained)
├── sim-data/                     # 48 h datasets: weather/ atc/ fuel/ power/
└── tests/acceptance/
```

**Inventory groups:** `dmz`, `blackstone_services`, `blackstone_hq`, `blackstone_it`, `blackstone_supply`,
`fops_services`, `flight_control_center`, `fops_users`, `engineering`, `physical_security`, `soc`, `ot_mtu`, `ot_fuel`,
`ot_power`, `net`, `range`.

**Deployment order** (`site.yml`): network → foundation (time, both AD domains + trust, CA, DNS, DHCP) →
enterprise → flight ops (incl. ATC/weather) → SOC → PACS → OT (gateway → MTU/HMI → PLCs → sims → in-enclave
historians) → injection harnesses. In OT, deploy the chain head-to-tail so each bridge's downstream segment
exists first.

```bash
ansible-galaxy collection install -r requirements.yml
ansible-lint && ansible-playbook site.yml --syntax-check
ansible-playbook site.yml --check --diff      # dry run
ansible-playbook site.yml                       # full build
ansible-playbook playbooks/60-ot.yml --limit ot_fuel
ansible-vault edit group_vars/vault.yml
```

---

## 11. Conventions & guardrails

- **`group_vars`/`host_vars` are the single source of truth** — the §6 IP plan, AD design, tag/point maps,
  seed data. No hardcoded IPs/creds in tasks or templates.
- **Pin every version** — especially `influxdb:2.7` (never `:latest`).
- **Secrets via `ansible-vault`;** rotate all vendor defaults (e.g., OpenPLC `openplc/openplc`).
- **Open source by default;** baseline exceptions are Windows systems/servers, **pfSense**, and VyOS/Vyatta.
- **Defensive scope** — no malware/exploits/offensive tooling. Intentional weaknesses (unauthenticated
  Modbus/DNP3/MQTT, weak creds, flat paths) are **explicit, parameterized scenario knobs** that default secure.
- **Respect segmentation** — `bs-ops-fw` (pfSense) is the default-deny L3.5 boundary; the IT↔OT path runs only
  through `bs-modbus-gateway`. Forwarding is enabled only on the designated OT bridge hosts, only between their
  listed segments. Ask before adding any cross-boundary rule.
- **Management plane invisible** — never expose `10.255.240.0/20` to a scenario or data-plane service.
- **Verify upstream versions/maintenance at build time**; flag before substituting a different major version.

---

## 12. Resolved decisions & remaining reconciliations

**Resolved (§3):** control plane `10.255.240.0/20`; firewalls pfSense; power DNP3; OT segmented; historian
in-enclave; Ubuntu 24.04 baseline; `.2` reserved; **routing model OSPF + eBGP + static (§3.8)**;
**pfSense automation = `pfsensible.core` + `php -r` escape hatches, port `roles/pfsense_firewall` from
PowerPlant (§3.9)**; **pfSense NIC position FIRST (§3.10)**; **AD trust direction = blackstone.mil TRUSTS
fops.blackstone.mil, asymmetric access (CLAUDE.md §4 / `group_vars/fops.yml`)**; **role sourcing = copy
into `airfield-range/roles/` (memory `project_airfield_role_sourcing`)**.

**Still to confirm / do:**
- **DNP3 library** — `dnp3-python`/pydnp3 (Python, on EOL opendnp3) vs Step Function I/O `dnp3` (Rust, TLS,
  no Python). Pick before the power build.
- **AD details still TBD** — domain membership of Engineering and Security/SOC (best-judgment blackstone.mil);
  AD trust **type** (external vs forest — `group_vars/fops.yml` currently set to external, incoming);
  cross-domain access-control model (default is transitive parent/child, so any restriction is an explicit deny at the resource ACL layer);
  whether the tiered-admin overlay is in scope.
- **`--tags reset` scope** — data-only (TRUNCATE audit DB, wipe historian buckets, reset replay state) vs
  also-config (DROP+CREATE database, regenerate certs, re-init OpenPLC programs).
- **EDR choice** — both CrowdStrike and SentinelOne installers staged in `group_vars/{linux,windows}.yml`.
  Pick per host class (e.g., CS for AE targets, S1 for AUE workstations) or single product everywhere.
- **Blueprint still missing**: `bs-ntp` (NTP server, planned `172.31.2.6`), `ansible` control node (referenced
  by deploy/download `ScriptDefinition`s), power-grid hosts (`power-plc`, `power-sim`, extra-sim), and
  Physical-Security hosts (Leosac controller, door points, pin pads, mock door sim).
- **Build sheets still missing**: weather radar, ATC, Leosac access control. Fuel and power sheets exist.
- **Scaffold port from `ss-pp-ab`**: `build_tarball.sh`, `deploy.sh`, `verify_vars.py`, `requirements.yml`,
  `UPSTREAM_FIXES.md`, `PROJECT_LOG.md`, plus the `roles/` directory with the first roles copied in (`init`
  is already referenced by `site.yml` play #1).
- **Build-sheet reconciliation pass (next action):**
  - `fuel-farm-build-sheet.md` → re-address from flat `172.16.45.0/24` to the segmented chain
    (MTU `172.16.45.0/29`, Fuel-PLC `172.16.45.8/29`, Fuel-Sim `172.16.46.0/24`); HMI = shared control-room-hmi;
    historian/audit DB into `172.16.46.x` (in-enclave); management onto `10.255.240.0/20`.
  - `power-grid-build-sheet.md` → re-address to `172.16.47.8/29` (PLC) + `172.16.49.0/24` (sim) +
    `172.16.48.0/24` (extra); keep DNP3 + MQTT; historian/audit DB in-enclave; management onto `10.255.240.0/20`.

---

## 13. Subsystem status

| Subsystem | Enclave | Protocol | Subnet(s) | Build sheet |
|---|---|---|---|---|
| Network backbone | net | — | `172.31.1.x/30`, `199.252.163.0/30` | pending |
| AD / enterprise (blackstone.mil) | Services/HQ/IT/Supply | — | `172.31.2/14/15/16.0/24` | pending |
| Flight ops (fops.blackstone.mil) | FOS/FCC/Users | — | `172.31.3/5/6.0/24` | pending |
| SOC / NSM | SOC | — | `172.31.7.0/24` | pending |
| **Weather radar** | Flight Control Center | NEXRAD/METAR replay | `172.31.5.0/24` | pending |
| **ATC / flight data** | Flight Control Center | ADS-B replay | `172.31.5.0/24` | pending |
| **Access control (Leosac)** | Physical Security | OSDP/Wiegand | `172.31.11.0/24` | pending |
| **Fuel farm** | OT / RVIN (segmented) | Modbus TCP | `172.16.45.x` + `172.16.46.0/24` | ✓ (re-address) |
| **Power grid** | OT / RVIN (segmented) | DNP3 + MQTT | `172.16.47/49/48.x` | ✓ (re-address) |

Reconcile the fuel and power sheets to §3, then build OT first. For the rest, generate a build sheet (same
structure as the two existing sheets) before implementing.
