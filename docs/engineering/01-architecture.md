# 01 — Architecture

## What this range is

Blackstone Auxiliary Field is a simulated military airfield built as a
**defensive cyber range** on the SimSpace NG platform. It exists to train and
assess SOC analysts against a network that behaves like a real base IT/OT
estate: a two-domain Active Directory forest, a DMZ, a segmented fuel-farm OT
enclave running real ICS equipment, a bespoke air-traffic-control subsystem fed
from recorded flight data, continuous emulated-user noise, and a distributed
Security Onion grid watching from four vantage points. One variant additionally
runs a distributed Splunk cluster, with a universal forwarder on every managed
host feeding it alongside the Elastic Agent, plus a Malcolm appliance for
OT deep-packet analysis.

The whole range is **Ansible from a pinned tarball**. It stands up, re-deploys
and resets idempotently so an exercise can be run, torn down and rebuilt clean.

The range is built by a **range-specific overlay** on top of SimSpace's core
Ansible library. The overlay ships inventory, `host_vars`, `group_vars`, custom
roles and range playbooks, then bundles the base roles it uses plus its own
overlays into the deploy tarball.

## Design decisions that shape everything downstream

These five properties are load-bearing — most behaviours only make sense
against them.

1. **Sensor coverage is positional.** Each Security Onion sensor receives a
   layer-2 mirror from exactly one router. An event is visible only to the
   sensor whose router carries it; there is no aggregation tap. Correlating an
   intrusion across segments means correlating across sensors, and *which*
   sensor carries an event is itself a location signal. See the security
   monitoring & telemetry chapter.

2. **The OT enclave is segmented at layer 3.** `bs-ops-fw` is a default-deny
   boundary between the base enterprise and the OT chain, and `bs-modbus-gateway`
   is the single routed path into the fuel-farm Purdue segments. OT reaches the
   enterprise over static routes only; the firewall redistributes the OT
   umbrella into the corporate IGP rather than letting OT speak it.

3. **Endpoint telemetry follows the Purdue level, not the subnet.** An OT host
   gets an endpoint agent only if its job is to *monitor* the process, not to
   *run* it. The control-room HMI, the PLC and the field simulator are
   deliberately left off; the historian and audit DB are instrumented. This is
   expressed as inventory-group membership so "does this host emit endpoint
   telemetry" is a decision someone recorded. See the inventory & roles chapter.

4. **Two domains in one forest.** The base enterprise is `blackstone.mil`
   (forest root) and flight operations is its child `fops.blackstone.mil`.
   The parent/child trust is automatic and transitive, so identity crosses the
   two domains by default — which makes the places it should *not* appear
   meaningful.

5. **The network is not quiet.** Emulated users browse, open documents and move
   files continuously across the enterprise and flight-ops segments. Detection
   logic that assumes a clean baseline will not behave as it does in a lab.

Platform- and library-level decisions (control plane on `10.255.240.0/20`,
pfSense firewalls, the OSPF+eBGP+static routing model, role-sourcing policy,
the Ubuntu 24.04 baseline) are set by the platform and SimSpace's core Ansible
library, and are summarised in the network and build chapters rather than
re-argued here.

## System architecture

```
          is-inet (200.200.200.2) — simulated Internet / mail / elgg
                              │
          bs-edge-rtr (VyOS, AS 65002)
                              │  eBGP  199.252.163.0/30
          bs-edge-fw (pfSense, AS 65001) ── DMZ 172.31.12.0/24 (bs-www, bs-ftp, bs-ns1)
                              │  172.31.1.4/30
          bs-core-rtr (VyOS, OSPF) ── blackstone.mil enterprise:
                              │          Services  172.31.2.0/24
                              │          HQ        172.31.14.0/24
                              │          IT        172.31.15.0/24
                              │          Supply    172.31.16.0/24
                              │  172.31.1.8/30
          bs-ops-rtr (VyOS, OSPF) ── fops.blackstone.mil:
                              │          Flight-Ops-Services   172.31.3.0/24
                              │          Flight-Control-Center 172.31.5.0/24 (ATC, weather)
                              │          Flight-Ops-Users      172.31.6.0/24
                              │  172.31.1.12/30
          bs-ops-fw (pfSense, OSPF + static L3.5 boundary, default-deny)
              │                              │
   172.31.1.20/30                    172.31.1.16/30 (static only)
              │                              │
   bs-sec-rtr (VyOS, OSPF)           bs-modbus-gateway (VyOS, OT chain head)
     SOC         172.31.7.0/24         MTU            172.16.45.0/29  (control-room-hmi, FUXA)
     Phys-Sec    172.31.11.0/24 (—)    Fuel-Farm PLC  172.16.45.8/29  (ff-plc-1, OpenPLC)
                                       Fuel-Farm Sim  172.16.46.0/24  (fuel-sim, historian, audit DB)
          Engineering 172.31.8.0/24 hangs off bs-ops-fw directly (bs-eng*, bs-ro-hist)
```

Every host is additionally dual-homed onto the **management plane**
(`10.255.240.0/20`, gateway/proxy `10.255.240.1`). Ansible connects over
management; the data plane never routes to it. The Ansible controller is on the
management plane at `10.255.240.157` and is **not** in `[linux]`.

Full addressing and routing detail is in the network chapter.

## The bespoke subsystems

- **Base enterprise** — `blackstone.mil`, the forest root: Services (DCs, file,
  WEC, proxy), Headquarters, IT and Supply workstations, and the DMZ. See the
  Active Directory chapter.
- **Flight operations** — `fops.blackstone.mil`, the child domain: its own DCs,
  file and app servers, flight-ops user workstations, and the Flight-Control
  Center that hosts the ATC and weather subsystem.
- **Air-traffic control** — a Linux subsystem on the Flight-Control-Center
  segment: a radar/ADS-B replay feeding a controller working position and a set
  of flight display workstations, with a recorded-weather feed alongside. Built
  and verified by its own playbook.
- **Fuel farm (OT)** — a Purdue-style enclave behind `bs-ops-fw`, reached only
  through `bs-modbus-gateway`: an OpenPLC controller, a pymodbus field
  simulator, a FUXA operator HMI, an in-enclave historian (Telegraf + InfluxDB
  2.7 + Grafana) and a PostgreSQL/TimescaleDB audit DB. Modbus TCP is the
  process protocol.
- **Security / SOC** — the Security Onion grid, analyst workstations and
  forensic tooling on the SOC segment. On the Splunk-stacked variant a
  distributed Splunk cluster and a Malcolm OT appliance also live here.

## Telemetry / data flows

| Source | Path into monitoring | Dataset |
|---|---|---|
| Network (enterprise / flight-ops / OT / edge) | layer-2 `gretap` mirrors → the four sensors → Zeek + Suricata | Zeek `conn`/`dns`/`http`/`files`, Suricata alerts |
| Windows + Linux endpoints | Elastic Agent → SO Fleet | endpoint (Defend/osquery/system) + Sysmon on the Windows fleet |
| nginx / Squid / app hosts | agent tails the host's logs | parsed host datasets |
| pfSense ×2 / VyOS ×5 | devices → `soc-syslog` (rsyslog) → local agent | `logs-pfsense.*`, `logs-vyos-*` |
| Fuel-farm OT process traffic | second `gretap` tunnel → Malcolm (stacked variant) | Malcolm (Arkime/Zeek ICS parsers) |

Everything lands in one monitoring platform, parsed into its own dataset, so
correlating a flight-ops login with the process that produced it and the
firewall rule that allowed it is one investigation, not three.

**Splunk-stacked variant.** Every managed host additionally runs a Splunk
universal forwarder. Windows event logs and Sysmon, Linux syslog, and the
device syslog aggregated on `soc-syslog` are forwarded to the distributed
Splunk cluster in parallel with the Security Onion path above. The two
monitoring stacks are independent — the same sources feed both — so analysts
can be trained and assessed on either.

## Artifact sourcing (air-gapped)

The range has no general internet egress. Install artifacts come from the
in-platform **Nexus** (`nexus.dev.ng.simspace.lan`), reached through the
management proxy: the Security Onion source + container registry, Malcolm's
source + images, the Ansible Galaxy collections, the Splunk packages (on the
stacked variant), the fuel-farm Python packages, and the HMI's npm closure.
**apt is the exception** — base packages and `docker-ce` install from the Ubuntu
archive and `download.docker.com` through the proxy, because the Nexus apt repo
serves several Ubuntu releases from one index and no pin can safely prefer the
right one. Detail in the build & deploy chapter.
