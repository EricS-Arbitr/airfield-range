# 02 — Network

All IPs, prefixes and interface assignments are owned by the blueprint and by
`host_vars`; this doc is the reconciled map. Re-derive from the platform
blueprint after any topology change.

## Planes

| Plane | Range | Notes |
|---|---|---|
| Production (scenario) | `172.31.0.0/16`, `172.16.45.0/24` + `172.16.46.0/24` (OT), `199.252.163.0/30`, `200.200.200.0/24` | What trainees and sensors see |
| Management (out-of-band) | `10.255.240.0/20`, gw/proxy `10.255.240.1` | Ansible path; hidden from scenario; mirror-excluded |

Every host is dual-homed. The management NIC is in **first** position
(Windows `Ethernet0`, Linux `eth0`, pfSense `vmx0`); the production NIC(s)
follow. The Ansible controller sits on the management plane at `10.255.240.157`.
The management plane shares a data-plane NIC on the mirror-source routers, so it
is explicitly excluded from the mirror fabric (see below) and scrubbed from AD
DNS (see the Active Directory chapter).

## Host-bearing segments

| Segment | CIDR | Domain | Contents |
|---|---|---|---|
| DMZ | `172.31.12.0/24` | — | `bs-ftp` (.2), `bs-www` (.3), `bs-ns1` (.5) |
| Services | `172.31.2.0/24` | blackstone.mil | `bs-dc01` (.7), `bs-dc02` (.8), `bs-file01` (.10), `bs-file02` (.11), `bs-proxy` (.15), `bs-wec` (.17) |
| Headquarters | `172.31.14.0/24` | blackstone.mil | `bs-hq01..06` |
| IT | `172.31.15.0/24` | blackstone.mil | `bs-it01..05` |
| Supply | `172.31.16.0/24` | blackstone.mil | `bs-supply01..05` |
| Flight-Ops-Services | `172.31.3.0/24` | fops.blackstone.mil | `fops-dc01` (.11), `fops-dc02` (.12), `fops-file01` (.15), `fops-file02` (.16), `fops-wec01` (.20), `fops-app01..03` (.30–.32) |
| Flight-Control-Center | `172.31.5.0/24` | fops.blackstone.mil | `atc-radar` (.10), `atc-weather` (.11), `atc-station` (.12), `fops-flight01..04` |
| Flight-Ops-Users | `172.31.6.0/24` | fops.blackstone.mil | `fops-ops01..08` |
| Engineering | `172.31.8.0/24` | blackstone.mil (Win) | `bs-eng01..06`, `bs-ro-hist` (.5) |
| SOC | `172.31.7.0/24` | — | SO grid (.15/.20/.21–.24), `soc-syslog` (.13), `soc-openvas` (.16), `soc-sift`, `soc-analyst01..06`; on the stacked variant also the Splunk cluster (.25–.28) and Malcolm (.30) |
| Physical-Security | `172.31.11.0/24` | — | reserved; no hosts in the blueprint yet |
| OT · MTU | `172.16.45.0/29` | — | `control-room-hmi` (.3, FUXA) |
| OT · Fuel-Farm PLC | `172.16.45.8/29` | — | `ff-plc-1` (.10, OpenPLC) |
| OT · Fuel-Farm Sim | `172.16.46.0/24` | — | `fuel-farm-sim` (.17), `fuel-hist` (.18), `fuel-db` (.19) |
| Internet (sim) | `200.200.200.0/24` | — | `is-inet` (.2), `elgg` |

## Point-to-point transit links (/30)

| Link | CIDR | Endpoints |
|---|---|---|
| edge-rtr ↔ edge-fw | `199.252.163.0/30` | `bs-edge-rtr` .2 ↔ `bs-edge-fw` .1 |
| edge-fw ↔ core-rtr | `172.31.1.4/30` | `bs-edge-fw` .5 ↔ `bs-core-rtr` .6 |
| core-rtr ↔ ops-rtr | `172.31.1.8/30` | `bs-core-rtr` .9 ↔ `bs-ops-rtr` .10 |
| ops-rtr ↔ ops-fw | `172.31.1.12/30` | `bs-ops-rtr` .13 ↔ `bs-ops-fw` .14 |
| ops-fw ↔ modbus-gw | `172.31.1.16/30` | `bs-ops-fw` .17 ↔ `bs-modbus-gateway` .18 |
| ops-fw ↔ sec-rtr | `172.31.1.20/30` | `bs-ops-fw` .21 ↔ `bs-sec-rtr` .22 |

## Routing model

Verified against `host_vars`.

| Domain | Protocol | Notes |
|---|---|---|
| ISP edge | **eBGP** | `bs-edge-rtr` (AS 65002) ↔ `bs-edge-fw` (AS 65001). The **only** BGP session in the fabric. |
| Enterprise + flight-ops core | **OSPF area 0** | `bs-core-rtr`, `bs-ops-rtr`, `bs-sec-rtr` and both pfSense firewalls run OSPF across all transit links and LAN interfaces. |
| L3.5 OT boundary | **Static, default-deny** | `bs-ops-fw` ↔ `bs-modbus-gateway` is static-only; `bs-modbus-gateway` runs no OSPF. The OT umbrella `172.16.0.0/16` is a static route on `bs-ops-fw` via `172.31.1.18`, and `bs-ops-fw` `redistribute_static` pushes it into corporate OSPF so the enterprise learns the OT prefixes without OT speaking the IGP. |

`bs-edge-fw` carries the eBGP session to the ISP; the two pfSense firewalls run
**FRR**. `bs-modbus-gateway` is a VyOS router at the head of the OT chain; it is
not the same set as the mirror-source routers, though it is one of them.

> The L3.5 boundary is **default-deny by design but permit-any today** —
> `bs-ops-fw` carries lab "allow any" rules per interface, with blocking ACLs
> deferred until every subsystem is working. When the ACLs land, the two OT
> hosts that enrol an Elastic Agent (see the telemetry chapter) need an explicit
> rule permitting them to reach the SOC subnet on Fleet's ports; the agent
> preflight fails loudly if that rule is missing rather than opening the path
> itself.

### Firewalls and routers

| Host | Type | Role in the fabric |
|---|---|---|
| `bs-edge-fw` | pfSense | perimeter; eBGP peer; fronts the DMZ |
| `bs-ops-fw` | pfSense | OSPF inner core **and** the static-only, default-deny L3.5 OT boundary; gateways the Engineering `/24` |
| `bs-edge-rtr` | VyOS | the ISP edge (AS 65002); faces the simulated Internet |
| `bs-core-rtr` | VyOS | gateway for the four enterprise `/24`s |
| `bs-ops-rtr` | VyOS | gateway for the three flight-ops `/24`s |
| `bs-sec-rtr` | VyOS | gateway for SOC and Physical-Security |
| `bs-modbus-gateway` | VyOS | head of the OT chain; gateways the three fuel-farm segments |

pfSense specifics (admin-user requirement, `php -r` escape hatches, FRR runtime
dir, NIC ordering) are in the operations & troubleshooting chapter and the
`pfsense_firewall` role.

## DNS

- AD-integrated zones for `blackstone.mil` and the child `fops.blackstone.mil`
  on their respective domain controllers.
- The `dns` role creates zones/records but does **not** set forwarders; a
  baseline play points the DCs' forwarders at `is-inet`'s simulated public
  resolvers so scenario names resolve while the range stays self-contained.
- `bs-ns1` serves the DMZ; `global_dns` provisions scenario name resolution on
  `is-inet` (the internet simulator's unbound resolver) for the simulated
  Internet.
- Management-plane A records are scrubbed from AD DNS so `ping <host>` does not
  resolve to a `10.255.240.x` address.

## The GRE mirror fabric

Traffic is copied from router interfaces into `gretap` tunnels with `tc mirred`
and delivered to the sensors (and, on the stacked variant, to Malcolm). This is
the backbone of positional sensor coverage.

| Property | Value |
|---|---|
| Encapsulation | `gretap` (layer 2). A layer-3 `gre` tunnel yields `DLT_RAW`, which makes Zeek produce **zero** connection logs while Suricata keeps alerting — the classic "sensor looks up but sees nothing" failure. Both ends must be `gretap`. |
| Tunnel MTU | 1500 (raised above the kernel's 1462 `gretap` default); routers clamp TCP MSS so mirrored frames stay within the routed underlay. |
| Mechanism | `tc` ingress + root `prio` qdiscs per source interface, `matchall action mirred egress mirror dev tun0`. On `bs-modbus-gateway` the OT process interfaces also fan out to `tun1`, feeding Malcolm alongside the sensor. |
| Persistence | re-applied from the VyOS post-config boot script. |
| Management exclusion | `10.255.240.0/20` carried by `action pass` filters at lower priority than the `mirred` catch-all, on each source interface. |

| Sensor | Mirror source | Coverage |
|---|---|---|
| `soc-sensor-corp` | `bs-core-rtr` | Services, Headquarters, IT, Supply |
| `soc-sensor-fops` | `bs-ops-rtr` | Flight-Ops-Services, Flight-Control-Center, Flight-Ops-Users |
| `soc-sensor-ot` | `bs-modbus-gateway` | MTU `/29`, Fuel-Farm PLC `/29`, Fuel-Farm Sim `/24` |
| `soc-sensor-edge` | `bs-edge-rtr` | the simulated Internet and the edge transit link |

Verify a mirror with the absolute path (`tc` is not on the `vyos` user's PATH):
`/sbin/tc filter show dev eth1 ingress` — expect one `mirred` filter and the
management-exclusion `action pass` filters per source interface. The GRE
underlay endpoint each sensor decapsulates from is a per-router fact in
`host_vars` (the sensors sit behind `bs-sec-rtr`/`bs-ops-fw`, so the path
differs per router and cannot be derived) — do not default it to the sensor's
own gateway.
