# 07 — Verification

Verification happens in two places: **build time** (gates in `build_tarball.sh`
that refuse to ship a bad archive) and **runtime** (checks after a deploy that
assert *outcomes*, not exit codes).

## Build-time gates

Covered in the build & deploy chapter. In short, `build_tarball.sh` runs
`verify_shell_args.py`, `verify_so_inventory.py`, `verify_dup_keys.py`,
`verify_handlers.py`, `verify_task_keywords.py`, `verify_defend_filters.py` and
`verify_vars.py`, and aborts the build if a hard gate fails. Run `verify_vars.py`
against the **stage**, not the repo root — the stage includes base roles copied
from SimSpace's core Ansible library, so some vars appear only there and a
count taken at the repo root misleads.

## Runtime verification

The SO grid is verified **in the deploy** by the `60-verify` phase — `so-status`
on the manager, `/_cluster/health` from the manager, and a real packet count on
every sensor's `tun0`. There is deliberately no standalone `verify_so.sh`: it
would duplicate those checks against a hardcoded host list, and the checks that
matter are the ones that run in the deploy.

Three read-only scripts run on the controller after a deploy:

```bash
sudo ./verify_deployment.sh     # range-wide health check
sudo ./verify_fuel_farm.sh      # fuel-farm OT acceptance
sudo ./verify_atc.sh            # ATC subsystem chain
```

`verify_deployment.sh` is the broad one. It checks per-group reachability over
the management plane; asserts the impostor-gateway MAC is absent from every
Windows host's ARP table; counts AD domain-join per group across both domains;
confirms the DCs' DNS forwarders point at `is-inet`; checks that `bs-ops-fw`'s
FRR RIB and the kernel FIB agree on the enterprise prefixes (a zebra-poisoning
guard); and queries Security Onion's Elasticsearch to confirm the device and
host log sources are actually ingesting. It prints `Total / Pass / Fail` and
exits non-zero if anything failed — **the number that matters is `Fail: 0`.**

`verify_fuel_farm.sh` is the fuel-farm acceptance check (historian, audit DB,
PLC and HMI reachability and function). `verify_atc.sh` walks the ATC subsystem
chain; both use `wget`, never `curl`, because `curl` does not work through the
controller's proxy.

Then confirm the thing that most often fails silently — that sensors are
*seeing traffic*, not merely running:

```bash
sudo ansible soc-sensor-corp,soc-sensor-fops,soc-sensor-ot,soc-sensor-edge -b -m shell \
  -a 'timeout 10 tcpdump -i tun0 -c 20 2>&1 | tail -3'
```

A sensor with Suricata alerting but Zeek producing zero connection logs means
the tunnel is carrying the wrong frame type — both ends must be `gretap` (see
the security monitoring & telemetry chapter). Verify a mirror itself with the
absolute path (the `vyos` user has no `tc` on PATH, and a bare invocation
returns "command not found" that a counting grep cannot tell from "no filters"):

```
/sbin/tc filter show dev eth1 ingress
```

## What "healthy" looks like

| Check | Expected |
|---|---|
| `so-status` on every SO node | all services running |
| Elastic cluster | green, search node present |
| `tcpdump -i tun0` on each sensor | packets flowing |
| Zeek `conn.log` | connection records, not zero |
| Fleet | every endpoint enrolled (66 agent targets) |
| Domain membership | 44 members joined across the two domains (48 counting the 4 DCs) |
| Local registry (airgap) | ≥ 22 repos before `so-setup` proceeds |
| Fuel farm | `verify_fuel_farm.sh` clean |
| ATC | `verify_atc.sh` clean |

## Three-layer post-deploy check

### L1 — routing convergence

On each VyOS router:
```
show ip ospf neighbor   # adjacencies Full on the corp routers
show ip bgp summary     # only bs-edge-rtr ↔ bs-edge-fw is Established
show ip route 0.0.0.0/0 # default in FIB
```
On each pfSense (use `sh -c` under tcsh):
```sh
vtysh -c "show ip ospf neighbor"
vtysh -c "show ip route"
netstat -rn -f inet | awk '$1=="default"||$1~/^172|^199|^10/'
```
"The config has the line" is not "the kernel installed the route" — image-baked
junk routes, equal-cost statics and self-loop next-hops all make FRR drop routes
from the FIB. Confirm the FIB, not the config. On `bs-ops-fw` specifically,
confirm the OT umbrella (`172.16.0.0/16`) is a static route that
`redistribute_static` has pushed into OSPF.

### L2 — pfSense config integrity

```sh
xmllint --xpath 'string(//gateways/defaultgw4)' /cf/conf/config.xml; echo
pfctl -vsr | grep -c 'label "USER_RULE'
```

### L3 — end-to-end from a workstation

```powershell
Test-NetConnection -ComputerName 172.31.12.3 -Port 80    # DMZ web reachable
tracert -d 172.16.46.18                                   # OT historian reachable through the L3.5 boundary
Resolve-DnsName bs-dc01 -DnsOnly                           # AD DNS only, not LLMNR
ping bs-dc01                                               # resolves to 172.31.2.7, not a mgmt IP
```

## Verification philosophy

Gates assert what the next step actually needs, and they must block only on
**self-clearing** conditions — a latched flag makes an `until:` unpassable, and
`failed_when: false` does not rescue an exhausted `until` (use `ignore_errors`).
A gate that passed "last time" proves nothing when the thing it guards is
timing-dependent. This is why `so_manager` verifies repos-present and
services-up rather than the `so-setup` exit code, and why the `dns` play gates
on the `DomainDnsZones` partition rather than assuming it exists.
