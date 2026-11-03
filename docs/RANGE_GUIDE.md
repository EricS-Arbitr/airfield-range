# Blackstone Auxiliary Field — Range Reference

Technical reference for the `blackstone.mil` military-airfield range: topology,
telemetry coverage, deployment procedure and verification.

Audience: event coordinators and range operators. Assumes working knowledge of
Security Onion, Active Directory, Ansible, and the SimSpace platform.

Scope: this document covers the range. Security Onion is the SIEM; every log
source on the range reports to it. (A sibling build adds a distributed Splunk
cluster and a Malcolm OT appliance alongside Security Onion; that variant has
its own reference.)

---

## At a glance

| | |
|---|---|
| **Hosts** | 85 |
| **AD forest** | one forest, two domains — `blackstone.mil` (root) and child `fops.blackstone.mil`; four domain controllers, 44 domain members |
| **Enterprise segments** | Services, Headquarters, IT, Supply (blackstone.mil); DMZ |
| **Flight-ops segments** | Flight-Ops-Services, Flight-Control-Center, Flight-Ops-Users (fops.blackstone.mil) |
| **OT enclave** | Fuel farm behind a dedicated L3.5 boundary — control-room HMI, OpenPLC, field simulator, in-enclave historian and audit DB |
| **Bespoke subsystems** | Air-traffic control (radar/ADS-B replay + controller position) and recorded weather on the Flight-Control-Center |
| **SIEM** | Security Onion 2.4 distributed — manager, search node, four sensors |
| **Network visibility** | Four GRE `gretap` mirrors covering enterprise, flight-ops, OT and the attack path |
| **Endpoint visibility** | Elastic Agent on Windows and Linux hosts (66 endpoints), enrolled into SO's Fleet; Sysmon across the Windows fleet |
| **Device log sources** | pfSense ×2 and VyOS ×5, aggregated on `soc-syslog` and parsed into their own datasets |
| **Analyst positions** | Six analyst workstations plus SIFT, FLARE and OpenVAS tooling on the SOC segment |
| **User activity** | Emulated users generating background traffic across the enterprise and flight-ops segments |

---

## Design constraints

Four properties of the build determine what practitioners can and cannot see.

**Sensor coverage is positional.** Each sensor receives a mirror from exactly
one router, so an event is visible only to the sensor whose router carries it.
Enterprise traffic reaches `soc-sensor-corp`; flight-ops reaches
`soc-sensor-fops`; fuel-farm OT reaches `soc-sensor-ot`; the attack path and
simulated Internet reach `soc-sensor-edge`. There is no aggregation tap.
Correlating an intrusion across segments requires correlating across sensors.

**The OT enclave is segmented at layer 3.** `bs-ops-fw` is a default-deny L3.5
boundary and `bs-modbus-gateway` is the single routed path into the fuel-farm
Purdue segments. OT reaches the enterprise over static routes only.

**Endpoint telemetry follows the Purdue level, not the subnet.** The OT hosts
that *run* the process — the HMI, the PLC, the field simulator — carry no
endpoint agent; the hosts that *monitor* it — the historian and audit DB — do.
The exclusion is deliberate and documented in `hosts`.

**The network is not quiet.** Emulated users generate browsing, file and process
activity continuously across the enterprise and flight-ops segments. Detection
logic that depends on a low-noise baseline will not behave as it does in a lab.

---

## Network architecture

```mermaid
graph TB
  subgraph EXT["External"]
    INET["is-inet / elgg<br/>200.200.200.0/24"]
  end
  subgraph EDGE["Edge"]
    ER["bs-edge-rtr"]
    EFW["bs-edge-fw"]
    DMZ["DMZ 172.31.12.0/24<br/>www · ftp · ns1"]
  end
  subgraph ENT["blackstone.mil enterprise"]
    CR["bs-core-rtr"]
    SVC["Services 172.31.2.0/24<br/>dc01 · dc02 · file · wec · proxy"]
    DEPT["HQ · IT · Supply<br/>172.31.14-16.0/24"]
  end
  subgraph FOPS["fops.blackstone.mil"]
    OR["bs-ops-rtr"]
    FOS["Flight-Ops-Services 172.31.3.0/24<br/>dc01 · dc02 · file · app"]
    FCC["Flight-Control-Center 172.31.5.0/24<br/>ATC · weather · flight wkstns"]
    FOU["Flight-Ops-Users 172.31.6.0/24"]
  end
  subgraph BND["L3.5 boundary + OT"]
    OFW["bs-ops-fw (default-deny)"]
    ENG["Engineering 172.31.8.0/24"]
    MG["bs-modbus-gateway"]
    OT["Fuel farm 172.16.45-46.x<br/>hmi · plc · sim · historian · db"]
  end
  subgraph SEC["SOC 172.31.7.0/24"]
    SOM["soc-so-manager"]
    SOS["soc-so-search"]
    SEN["4 sensors"]
    AN["soc-analyst01..06 + tooling"]
  end
  INET --> ER --> EFW --> DMZ
  EFW --> CR --> SVC
  CR --> DEPT
  CR --> OR --> FOS
  OR --> FCC
  OR --> FOU
  OR --> OFW --> ENG
  OFW --> MG --> OT
  OFW --> SEC
  ER -.mirror.-> SEN
  CR -.mirror.-> SEN
  OR -.mirror.-> SEN
  MG -.mirror.-> SEN
  SEN --> SOM --> SOS
  AN --> SOM
```

### Segments

| Segment | CIDR | Domain | Contents |
|---|---|---|---|
| DMZ | 172.31.12.0/24 | — | `bs-ftp`, `bs-www`, `bs-ns1` |
| Services | 172.31.2.0/24 | blackstone.mil | `bs-dc01`, `bs-dc02`, `bs-file01/02`, `bs-wec`, `bs-proxy` |
| Headquarters | 172.31.14.0/24 | blackstone.mil | 6 workstations |
| IT | 172.31.15.0/24 | blackstone.mil | 5 workstations |
| Supply | 172.31.16.0/24 | blackstone.mil | 5 workstations |
| Flight-Ops-Services | 172.31.3.0/24 | fops.blackstone.mil | `fops-dc01/02`, `fops-file01/02`, `fops-wec01`, `fops-app01..03` |
| Flight-Control-Center | 172.31.5.0/24 | fops.blackstone.mil | `atc-radar`, `atc-weather`, `atc-station`, `fops-flight01..04` |
| Flight-Ops-Users | 172.31.6.0/24 | fops.blackstone.mil | 8 workstations |
| Engineering | 172.31.8.0/24 | blackstone.mil (Win) | `bs-eng01..06`, `bs-ro-hist` |
| SOC | 172.31.7.0/24 | — | SO grid, 6 analyst workstations, SIFT/FLARE/OpenVAS, syslog collector |
| OT · MTU | 172.16.45.0/29 | — | `control-room-hmi` (FUXA) |
| OT · Fuel-Farm PLC | 172.16.45.8/29 | — | `ff-plc-1` (OpenPLC) |
| OT · Fuel-Farm Sim | 172.16.46.0/24 | — | `fuel-farm-sim`, `fuel-hist`, `fuel-db` |
| Internet (sim) | 200.200.200.0/24 | — | `is-inet`, `elgg` |

Physical-Security (`172.31.11.0/24`) is reserved behind `bs-sec-rtr` but holds
no hosts in the current blueprint.

### Routing

| Domain | Protocol | Notes |
|---|---|---|
| Enterprise + flight-ops core | OSPF area 0 | `bs-core-rtr`, `bs-ops-rtr`, `bs-sec-rtr` and both pfSense firewalls, across all transit and LAN interfaces |
| ISP edge | eBGP | `bs-edge-rtr` (AS 65002) ↔ `bs-edge-fw` (AS 65001) — the only BGP session in the fabric |
| L3.5 OT boundary | Static, default-deny | `bs-ops-fw` ↔ `bs-modbus-gateway` is static-only; the OT umbrella `172.16.0.0/16` is a static route that `bs-ops-fw` redistributes into OSPF so the enterprise learns the OT prefixes without OT speaking the IGP |

Both pfSense firewalls run FRR. The L3.5 boundary is default-deny by design but
carries lab "allow any" rules today; blocking ACLs are deferred until every
subsystem is working.

---

## Active Directory

One forest, two domains, four domain controllers:

| DC | Address | Domain | Placement |
|---|---|---|---|
| `bs-dc01` | 172.31.2.7 | blackstone.mil | Primary (forest root) |
| `bs-dc02` | 172.31.2.8 | blackstone.mil | Additional |
| `fops-dc01` | 172.31.3.11 | fops.blackstone.mil | Primary (child) |
| `fops-dc02` | 172.31.3.12 | fops.blackstone.mil | Additional |

`fops.blackstone.mil` is a **child domain** of `blackstone.mil` in a single
forest, so the parent/child trust is automatic, bidirectional and transitive.
Cross-domain authentication is expected by default — the point of interest is
where identity traffic appears that should not.

44 hosts are domain members (29 on `blackstone.mil`, 15 on
`fops.blackstone.mil`); every Windows VM in the range is joined. Workstations run
**emulated users** with realistic identities who log in, browse, open documents
and move files — the background traffic that makes detection non-trivial.
`is-inet` hosts mailboxes for both domains.

---

## The OT enclave — fuel farm

The fuel farm is a Purdue-style chain behind the `bs-ops-fw` L3.5 boundary,
reached only through `bs-modbus-gateway` and segmented across three subnets:

| Host | Address | Role |
|---|---|---|
| `control-room-hmi` | 172.16.45.3 | FUXA operator console (MTU / Level 2) — Modbus TCP client |
| `ff-plc-1` | 172.16.45.10 | OpenPLC v4 controller (Level 1) — Modbus TCP `:502` |
| `fuel-farm-sim` | 172.16.46.17 | pymodbus field simulation (Level 0/1) — the plant |
| `fuel-hist` | 172.16.46.18 | Historian — Telegraf + InfluxDB 2.7 + Grafana (Level 3) |
| `fuel-db` | 172.16.46.19 | Audit DB — PostgreSQL 16 + TimescaleDB (Level 3) |

`control-room-hmi` is also a dual-homed Purdue **bridge**, forwarding between its
MTU and Power-PLC NICs (never onto management). The HMI, PLC and field simulator
carry **no endpoint agent** — they run the process. Only the Level 3 historian
and audit DB are agent targets. `soc-sensor-ot` mirrors all three OT segments,
so OT traffic is fully visible on the wire; that network monitoring is the
compensating control for the deliberate endpoint-visibility gap.

The FUXA image ships without the Modbus driver, so the deploy installs
`modbus-serial` into the container from the in-platform Nexus npm group through
the management proxy — the HMI's Modbus device does not connect until it does.

The fuel farm deploys from its own `fuel_farm_playbook.yml` and is checked by
`verify_fuel_farm.sh`.

---

## The ATC subsystem

On the Flight-Control-Center segment (`172.31.5.0/24`, `fops.blackstone.mil`) a
bespoke air-traffic-control subsystem runs on three Linux hosts, with recorded
flight and weather data replayed into it:

| Host | Address | Role |
|---|---|---|
| `atc-radar` | 172.31.5.10 | ADS-B / radar replay backend feeding the scope |
| `atc-weather` | 172.31.5.11 | Recorded-weather feed |
| `atc-station` | 172.31.5.12 | Controller working position / display |

Four flight display workstations (`fops-flight01..04`) consume the ATC display.
The subsystem deploys from `atc_playbook.yml` and is checked by `verify_atc.sh`
(which uses `wget`, never `curl`, because `curl` does not work through the
controller's proxy).

---

## Security Onion

Distributed 2.4 grid on the SOC segment:

| Node | Address | Role |
|---|---|---|
| `soc-so-manager` | 172.31.7.15 | Manager, SOC web interface, Fleet |
| `soc-so-search` | 172.31.7.20 | Search node |
| `soc-sensor-corp` | 172.31.7.21 | Enterprise traffic |
| `soc-sensor-fops` | 172.31.7.22 | Flight-ops traffic |
| `soc-sensor-ot` | 172.31.7.23 | Fuel-farm OT traffic |
| `soc-sensor-edge` | 172.31.7.24 | Attack path and Internet |

### Mirror implementation

Traffic is copied from router interfaces into `gretap` tunnels with `tc mirred`.

| Property | Value |
|---|---|
| Encapsulation | `gretap` (layer 2) — a layer-3 `gre` tunnel yields `DLT_RAW` and produces zero connection logs while Suricata continues to alert |
| Tunnel MTU | 1500 — raised above the kernel's 1462 `gretap` default; routers clamp TCP MSS to keep mirrored frames within the routed underlay |
| Mechanism | `tc` ingress + root `prio` qdiscs per source interface, `matchall action mirred egress mirror dev tun0`. On `bs-modbus-gateway` the OT process interfaces fan out to a second tunnel as well |
| Persistence | re-applied from the VyOS post-config boot script |
| Exclusions | `10.255.240.0/20` passed ahead of the mirror catch-all |

| Sensor | Mirrors | Coverage |
|---|---|---|
| `soc-sensor-corp` | `bs-core-rtr` | Services, Headquarters, IT, Supply |
| `soc-sensor-fops` | `bs-ops-rtr` | Flight-Ops-Services, Flight-Control-Center, Flight-Ops-Users |
| `soc-sensor-ot` | `bs-modbus-gateway` | MTU, Fuel-Farm PLC, Fuel-Farm Sim |
| `soc-sensor-edge` | `bs-edge-rtr` | simulated Internet and the edge transit |

Because each sensor is fed by exactly one router, sensor identity is a location
signal: an event present on `soc-sensor-ot` and absent from `soc-sensor-fops`
indicates traffic that did not cross the OT boundary. There is no aggregation
point. Verify a mirror with the absolute path — `tc` is not on the `vyos` user's
PATH:

```
/sbin/tc filter show dev eth1 ingress
```

### Endpoint telemetry

Elastic Agents on Windows and Linux endpoints enrol into SO's Fleet — **66
endpoints**: every Windows and Linux host except the SO grid (which runs its own
agent) and the three OT hosts that run the process. **Sysmon runs across the
whole Windows fleet (48 hosts)**. Coverage is driven by inventory-group
membership so that "does this host produce endpoint telemetry" is a recorded
decision.

### Device log sources

The firewalls and routers cannot run an agent, so `soc-syslog` is the central
rsyslog collector and the Fleet integrations parse the stored copies:

| Source | How it reaches Security Onion |
|---|---|
| pfSense ×2 | each firewall sends syslog to `soc-syslog`, which relays the raw datagram to a per-firewall loopback port (`bs-edge-fw` → 9001, `bs-ops-fw` → 9002) where the Elastic pfSense integration parses it — the port carries the device identity |
| VyOS ×5 | the agent tails `soc-syslog`'s `/var/log/remote/<router>/syslog.log`, enumerated per router so the grid nodes' own logs are not read back in |

### Access

SOC web interface: **`https://172.31.7.15`**, admin `admin@blackstone.mil`.
Reach it from an analyst workstation. SOC is configured for IP access, so there
is no hostname to resolve.

---

## The analyst environment

Six analyst workstations on the SOC segment — `soc-analyst01..06`
(`172.31.7.31–.36`) — with the forensic bench alongside:

| Host | Address | Purpose |
|---|---|---|
| `soc-sift` | mgmt-only | SANS Investigative Forensic Toolkit — disk and memory analysis |
| `soc-flare` | mgmt-only | FLARE — Windows malware reverse-engineering |
| `soc-openvas` | 172.31.7.16 | Vulnerability scanning |

The forensic tooling ships from specialized images and does not run the standard
OS baseline. `soc-openvas` sits on the SOC production segment; SIFT and FLARE are
reachable over the management plane.

> **Adjacency — read before running live samples.** The SOC segment is not a
> mirror source, so no sensor captures traffic between the analyst hosts and the
> SO grid. A malware sample with worm-like behaviour on a bench host shares
> layer 2 with the grid and reaches it without traversing a router. This is
> acceptable for static analysis; uncontrolled detonation needs a dedicated
> analysis subnet with no path to the SOC segment.

---

## Standing the range up

```
cd /etc/ansible
wget -q -O airfield_seconion.tgz <tarball URL for the pinned commit>
tar -xzf airfield_seconion.tgz
sudo ./deploy.sh
```

`deploy.sh` makes three attempts: a full run, then failed hosts only, then a
full run again. Several steps depend on state another host reaches
asynchronously, so a first-attempt failure is often ordering rather than fault —
attempt 3 is the confirmation. Expect **two to three hours** for a full build
from a fresh blueprint; the Security Onion manager install alone is 20–30
minutes.

### Where the install artifacts come from

The build pulls its install media from the in-platform Nexus
(`nexus.dev.ng.simspace.lan`), reached through the management proxy
(`10.255.240.1:3128`) rather than the public internet:

| Artifact | Nexus repo |
|---|---|
| Security Onion source snapshot and airgap content | `ng_raw` (`installers/so/…`) |
| Security Onion container images | `nexus-docker` |
| Ansible Galaxy collections | `ng_raw/galaxy` |
| Fuel-farm Python packages | `ng_pypi` |
| HMI `modbus-serial` npm closure | `npm` group |

apt is the exception: base packages and `docker-ce` install from the Ubuntu
archive and `download.docker.com` through the proxy, because the Nexus apt repo
serves several Ubuntu releases from one index and no apt pin can safely prefer
the right one. Nexus presents an internal-CA certificate, so each fetcher that
reaches it is configured to trust or skip-verify that host specifically.

### Verifying

```
sudo ./verify_deployment.sh     # range-wide
sudo ./verify_fuel_farm.sh      # fuel-farm OT acceptance
sudo ./verify_atc.sh            # ATC subsystem
```

The Security Onion grid is verified inside the deploy by the `60-verify` phase
(`so-status`, cluster health, and a real packet count on every sensor's `tun0`).
Then confirm the thing that most often fails silently — that sensors are
*seeing traffic*:

```
sudo ansible soc-sensor-corp,soc-sensor-fops,soc-sensor-ot,soc-sensor-edge -b -m shell \
  -a 'timeout 10 tcpdump -i tun0 -c 20 2>&1 | tail -3'
```

A sensor with Suricata alerting and Zeek producing zero connection logs means
the tunnel is carrying the wrong frame type. Both ends must be `gretap`.

---

## What healthy looks like

| Check | Expected |
|---|---|
| `so-status` on every SO node | all services running |
| Elastic cluster | green, search node present |
| `tcpdump -i tun0` on each sensor | packets flowing |
| Zeek `conn.log` | connection records, not zero |
| Fleet | every endpoint enrolled (66 agent targets) |
| Domain membership | 44 members joined across the two domains |
| Fuel farm / ATC | `verify_fuel_farm.sh` / `verify_atc.sh` clean |

---

## Known behaviours

Things that look like faults and are not.

**SOC health-check failures fetching detection content.** SOC tries GitHub and
the ET rules server every few minutes; the range has no egress and runs in
airgap mode, so these fail continuously. Detection on ingested data is
unaffected.

**Gateway ARP repair messages during deploy.** The `init` role catches and
corrects a platform-level gateway-ARP fault before it can break routing. Seeing
the repair is good.

**Emulated user noise.** Continuous browsing, file and process activity is
intentional. Detections that only work on a quiet network are not detections.

**The L3.5 boundary permits everything today.** `bs-ops-fw` carries lab "allow
any" rules; a cross-boundary reachability test that succeeds is expected for now.

---

## Credentials

> The current range default credentials are configured in `group_vars` (the
> Security Onion and OT secrets in `group_vars/all/vault.yml`, Ansible Vault; the
> OS/device connection accounts in the per-group files). The password values are
> omitted here because this repository is public. **Rotate all defaults before
> any deployment that is not a lab.**

| System | Access | Username | Password |
|---|---|---|---|
| Security Onion SOC | `https://172.31.7.15` | `admin@blackstone.mil` | (vault) |
| Domain admin | both domains | `simspace` | (see `group_vars`) |
| Domain users | both domains | seeded identities | (see `group_vars`) |
| Windows hosts | RDP / WinRM | `simspace` | (see `group_vars`) |
| Linux hosts | SSH | `simspace` | (see `group_vars`) |
| pfSense firewalls | SSH / web | `admin` | (see `group_vars`) |
| VyOS routers | SSH | `vyos` | (see `group_vars`) |
| OpenPLC / FUXA / InfluxDB / fuel audit DB | OT | role accounts | (vault) |
| SO node join secret | — | — | (vault) |

Workstation and server local accounts are the 1.1.x platform image's `simspace`
account on this variant. Using the wrong account for the image generation does
not fail fast; it retries until timeout and looks like an unbooted host.

---

## Appendix — full host inventory

| Host | Address | Segment | Function |
|---|---|---|---|
| `ansible` | 10.10.10.10 | — | Ansible controller |
| `atc-radar` | 172.31.5.10 | Flight-Control-Center (fops) | ATC subsystem |
| `atc-station` | 172.31.5.12 | Flight-Control-Center (fops) | ATC subsystem |
| `atc-weather` | 172.31.5.11 | Flight-Control-Center (fops) | ATC subsystem |
| `bs-core-rtr` | 172.31.1.6 | transit | VyOS router |
| `bs-dc01` | 172.31.2.7 | Services (blackstone.mil) | domain controller (primary) |
| `bs-dc02` | 172.31.2.8 | Services (blackstone.mil) | domain controller (additional) |
| `bs-edge-fw` | 199.252.163.1 | edge transit | pfSense firewall |
| `bs-edge-rtr` | 200.200.200.1 | Internet (sim) | VyOS router (ISP edge) |
| `bs-eng01` | 172.31.8.11 | Engineering | engineering workstation |
| `bs-eng02` | 172.31.8.12 | Engineering | engineering workstation |
| `bs-eng03` | 172.31.8.13 | Engineering | engineering workstation |
| `bs-eng04` | 172.31.8.14 | Engineering | engineering workstation |
| `bs-eng05` | 172.31.8.15 | Engineering | engineering workstation (Linux) |
| `bs-eng06` | 172.31.8.16 | Engineering | engineering workstation (Linux) |
| `bs-file01` | 172.31.2.10 | Services (blackstone.mil) | file server |
| `bs-file02` | 172.31.2.11 | Services (blackstone.mil) | file server |
| `bs-ftp` | 172.31.12.2 | DMZ | DMZ service (ftp) |
| `bs-hq01` | 172.31.14.11 | Headquarters | HQ workstation |
| `bs-hq02` | 172.31.14.12 | Headquarters | HQ workstation |
| `bs-hq03` | 172.31.14.13 | Headquarters | HQ workstation |
| `bs-hq04` | 172.31.14.14 | Headquarters | HQ workstation |
| `bs-hq05` | 172.31.14.15 | Headquarters | HQ workstation |
| `bs-hq06` | 172.31.14.16 | Headquarters | HQ workstation |
| `bs-it01` | 172.31.15.11 | IT | IT workstation |
| `bs-it02` | 172.31.15.12 | IT | IT workstation |
| `bs-it03` | 172.31.15.13 | IT | IT workstation |
| `bs-it04` | 172.31.15.14 | IT | IT workstation |
| `bs-it05` | 172.31.15.15 | IT | IT workstation |
| `bs-modbus-gateway` | 172.31.1.18 | transit / OT head | VyOS router |
| `bs-ns1` | 172.31.12.5 | DMZ | DMZ service (DNS) |
| `bs-ops-fw` | 172.31.1.14 | transit | pfSense firewall (L3.5 boundary) |
| `bs-ops-rtr` | 172.31.1.10 | transit | VyOS router |
| `bs-proxy` | 172.31.2.15 | Services (blackstone.mil) | proxy (squid) |
| `bs-ro-hist` | 172.31.8.5 | Engineering | read-only historian replica |
| `bs-sec-rtr` | 172.31.1.22 | transit | VyOS router |
| `bs-supply01` | 172.31.16.11 | Supply | supply workstation |
| `bs-supply02` | 172.31.16.12 | Supply | supply workstation |
| `bs-supply03` | 172.31.16.13 | Supply | supply workstation |
| `bs-supply04` | 172.31.16.14 | Supply | supply workstation |
| `bs-supply05` | 172.31.16.15 | Supply | supply workstation |
| `bs-wec` | 172.31.2.17 | Services (blackstone.mil) | Windows Event Collector |
| `bs-www` | 172.31.12.3 | DMZ | DMZ service (web) |
| `control-room-hmi` | 172.16.45.3 | OT · MTU | control-room HMI (FUXA), OT bridge |
| `elgg` | — | Internet (sim) | scenario host |
| `ff-plc-1` | 172.16.45.10 | OT · Fuel PLC | OpenPLC controller |
| `fops-app01` | 172.31.3.30 | Flight-Ops-Services (fops) | flight-ops app server |
| `fops-app02` | 172.31.3.31 | Flight-Ops-Services (fops) | flight-ops app server |
| `fops-app03` | 172.31.3.32 | Flight-Ops-Services (fops) | flight-ops app server |
| `fops-dc01` | 172.31.3.11 | Flight-Ops-Services (fops) | domain controller (primary, child) |
| `fops-dc02` | 172.31.3.12 | Flight-Ops-Services (fops) | domain controller (additional) |
| `fops-file01` | 172.31.3.15 | Flight-Ops-Services (fops) | file server |
| `fops-file02` | 172.31.3.16 | Flight-Ops-Services (fops) | file server |
| `fops-flight01` | 172.31.5.81 | Flight-Control-Center (fops) | ATC display workstation |
| `fops-flight02` | 172.31.5.82 | Flight-Control-Center (fops) | ATC display workstation |
| `fops-flight03` | 172.31.5.83 | Flight-Control-Center (fops) | ATC display workstation |
| `fops-flight04` | 172.31.5.84 | Flight-Control-Center (fops) | ATC display workstation |
| `fops-ops01` | 172.31.6.11 | Flight-Ops-Users (fops) | flight-ops workstation |
| `fops-ops02` | 172.31.6.12 | Flight-Ops-Users (fops) | flight-ops workstation |
| `fops-ops03` | 172.31.6.13 | Flight-Ops-Users (fops) | flight-ops workstation |
| `fops-ops04` | 172.31.6.14 | Flight-Ops-Users (fops) | flight-ops workstation |
| `fops-ops05` | 172.31.6.15 | Flight-Ops-Users (fops) | flight-ops workstation |
| `fops-ops06` | 172.31.6.16 | Flight-Ops-Users (fops) | flight-ops workstation |
| `fops-ops07` | 172.31.6.17 | Flight-Ops-Users (fops) | flight-ops workstation |
| `fops-ops08` | 172.31.6.18 | Flight-Ops-Users (fops) | flight-ops workstation |
| `fops-wec01` | 172.31.3.20 | Flight-Ops-Services (fops) | Windows Event Collector |
| `fuel-db` | 172.16.46.19 | OT · Fuel Sim | audit DB (PostgreSQL/TimescaleDB) |
| `fuel-farm-sim` | 172.16.46.17 | OT · Fuel Sim | field simulation (pymodbus) |
| `fuel-hist` | 172.16.46.18 | OT · Fuel Sim | historian (InfluxDB/Telegraf/Grafana) |
| `is-inet` | 200.200.200.2 | Internet (sim) | mail + internet-sim DNS |
| `soc-analyst01` | 172.31.7.31 | SOC | analyst workstation |
| `soc-analyst02` | 172.31.7.32 | SOC | analyst workstation |
| `soc-analyst03` | 172.31.7.33 | SOC | analyst workstation |
| `soc-analyst04` | 172.31.7.34 | SOC | analyst workstation |
| `soc-analyst05` | 172.31.7.35 | SOC | analyst workstation |
| `soc-analyst06` | 172.31.7.36 | SOC | analyst workstation |
| `soc-flare` | — | SOC (mgmt) | FLARE malware-analysis workstation |
| `soc-openvas` | 172.31.7.16 | SOC | vulnerability scanner |
| `soc-sensor-corp` | 172.31.7.21 | SOC | Security Onion sensor |
| `soc-sensor-edge` | 172.31.7.24 | SOC | Security Onion sensor |
| `soc-sensor-fops` | 172.31.7.22 | SOC | Security Onion sensor |
| `soc-sensor-ot` | 172.31.7.23 | SOC | Security Onion sensor |
| `soc-sift` | — | SOC (mgmt) | SIFT forensic workstation |
| `soc-so-manager` | 172.31.7.15 | SOC | Security Onion manager |
| `soc-so-search` | 172.31.7.20 | SOC | Security Onion search node |
| `soc-syslog` | 172.31.7.13 | SOC | syslog collector |
