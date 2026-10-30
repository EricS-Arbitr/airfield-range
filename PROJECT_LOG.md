# airfield-range — Project Activity Log

Period: 2026-06-18 → ongoing

## Goal

A working Ansible overlay (`airfield-range`) that provisions the **JCTE vCity Military Airfield** cyber range on top of customer's `range-development-ansible` base, with all role customizations copied into `airfield-range/roles/` per the role-sourcing policy. Five bespoke OT/business systems: weather radar, ATC, access control (Leosac), fuel farm, power grid. Two-tier AD (`vcab.lan` + `flightops.lan`) with cross-forest trust.

## Phase log

### Phase 0 — Scoping & decisions (2026-06-18 → 2026-06-24)

- CLAUDE.md owner-decision table populated (control plane `10.255.240.0/20`; pfSense firewalls; DNP3 power; segmented OT; in-enclave historian; Ubuntu 24.04 baseline; `.2` reserved; OSPF + eBGP-edge + static-at-L3.5; pfSense automation = `pfsensible.core` + `php -r`; pfSense NIC position FIRST; AD trust = vcab.lan TRUSTS flightops.lan).
- Network blueprint `WORK_DIR/ARBITR_MB_011.yml` built and iterated — 80 `VmInstance`s including the `ansible` control node, `bs-modbus-gateway` with full 4-NIC Purdue chain, every host with a `managementInterface` block, name/hostname cleanup.
- Inventory (`hosts`) drafted, 79 production VmInstances accounted for across 52 host-list groups + 14 `:children` roll-ups (the `ansible` host is platform-managed and lives in `[infrastructure]`).
- `group_vars/` and `host_vars/` scaffolded (13 group files, 79 per-host files generated from the blueprint).
- Scaffolding ported from `ss-pp-ab/` (`build_tarball.sh`, `deploy.sh`, `verify_vars.py`, `requirements.yml`, `UPSTREAM_FIXES.md`, this file).
- First roles copied in: `init`, `common`, `vyos`, `handlers` (meta dep of common). All sourced from `range-development-ansible/roles/` per the role-sourcing policy.

### Phase 1 — Network (planned)

00-network: VyOS routers (5) + pfSense firewalls (2). OSPF area 0 IGP across corp links + LAN interfaces; eBGP at `bs-edge-rtr` ↔ `bs-edge-fw`; STATIC-only at `bs-ops-fw` ↔ `bs-modbus-gateway`.

### Phase 2 — Foundation (planned)

10-foundation: NTP (`bs-ntp` once added to blueprint), both AD domains + cross-forest trust, CA, DNS, DHCP.

### Phase 3+ — Enterprise / Flight ops / SOC / PACS / OT / Injection (planned)

Per CLAUDE.md §10 deployment-order tiers.

---

## 2026-08-11 — Security Onion ported in (branch `security-onion`)

Distributed SO 2.4 grid: manager + search + four sensors, one sensor per
mirrored router. Branch is deliberately unmerged; `main` stays Splunk-only.

**Roles copied** from `PowerPlant/ss-pp-ab@security-onion` per the
role-sourcing policy, unmodified except where the range differs:
`so_base`, `so_apt_mirror`, `so_manager`, `so_search`, `so_sensor`,
`vyos_mirror`, `elastic_agent`. The only edit was the topology named in
`elastic_agent`'s preflight failure message (pp-ot-firewall -> bs-ops-fw).
`so_subnet_security` is aliased in group_vars rather than renamed in the
roles, so the next re-copy stays a plain `cp` — see UPSTREAM_FIXES.

**Playbooks** `playbooks/05-time … 75-endpoint`, appended to `site.yml` as
nine `import_playbook` entries rather than interleaved, so a diff against
`main` shows the SO work and nothing else.

Three differ from PowerPlant's versions on purpose:

- **75-endpoint** drops the Sysmon install play. site.yml already ends with
  Sysmon scoped to `hosts: windows`; PowerPlant needed the play because its
  baseline installed Sysmon only on the `[aue]` workstations. The
  verification play stays.
- **75-endpoint** replaces the first-three-octets subnet comparison with real
  CIDR containment via `ipaddr`. PowerPlant's shortcut is exact only when
  every declared subnet is a /24 and says so in its own comment; this range
  has `172.16.45.0/29` and `172.16.45.8/29` sharing a third octet. Exercised
  three ways before shipping: 69/69 covered on real inventory; both OT hosts
  named when the /29s are removed; and ff-plc-1 alone named when only the
  first /29 is declared — proving the boundary is respected, not rounded.
- **70-analyst** targets `[soc_analysts]`, not `[hunt]`. Here `[hunt]` is
  soc-flare/soc-sift/soc-openvas, two of which are Linux, and every task in
  that playbook is `win_powershell`.

**Also:** `vault_so_web_password` + `vault_so_remote_password` added to the
vault; `ansible.utils` added to requirements.yml (it was already a hard
dependency of `roles/common`, working only because the controller image
happens to ship it).

### Endpoint-agent placement in OT (Eric, 2026-08-11)

An OT host gets an Elastic Agent only if it is used to MONITOR the process,
not to run it. Applied by Purdue level rather than by subnet:

| host | level | agent |
|---|---|---|
| ff-plc-1 | L1 OpenPLC controller | no |
| fuel-farm-sim | L0/1 pymodbus field sim | no |
| control-room-hmi | L2 FUXA operator console | no |
| fuel-hist | L3 historian | yes |
| fuel-db | L3 audit DB | yes |
| bs-eng01-06, bs-ro-hist | engineering, 172.31.8.0/24 | yes (unchanged) |

Enforced in three places that must agree: the `[no_endpoint_agent]` inventory
group, the enroll play's host pattern, and `so_agent_endpoint_subnets` — where
the three OT segments collapse to two /32s, so the firewall permits only what
will actually enroll. 69 targets -> 66.

control-room-hmi is the judgment call and is deliberate. Industry practice
increasingly does put EDR on Level 2 HMIs (Stuxnet, Industroyer and TRITON all
pivoted through operator or engineering consoles), always vendor-approved and
detection-only. Excluding it is the conservative reading, and the better one
for a training range: it leaves a real endpoint-visibility gap in OT that
trainees have to close with network monitoring. soc-sensor-ot mirrors all three
OT segments, so the traffic is still fully visible on the wire.

UNVERIFIED ON A LIVE GRID: that `so-firewall includehost <group> <ip>/32` is
accepted. Inferred from PowerPlant passing /24s to the same command. It fails
loudly if not, in phase 75.

### Syslog into SO — CONFIRMED WORKING 2026-08-12

Verified on the live grid, not inferred:

```
POST /api/fleet/package_policies  -> 201, id 2acf5ef2-06b2-40b5-9e41-473e2f214da9
POST /api/fleet/agents/<id>/reassign -> 200
logs-syslog.remote-so             -> 30+ devices attributed via observer.hostname
```

Confirmed shape, copied from SO's own `zeek-logs` policy:

| field | value |
|---|---|
| package | `filestream` 1.2.0 — NOT `log` (deprecated, unused on this grid) |
| namespace | `so` — NOT `default` |
| input | `{type: filestream, policy_template: filestream}` |
| stream data_stream | `{type: logs, dataset: filestream.generic}` — the package's GENERIC template |
| destination | `data_stream.dataset` **var** = `syslog.remote` |

Routers, firewalls, every Linux host and the SO grid nodes all land with the
originating device in `observer.hostname`, recovered from the file path by the
dissect processor. Splunk continues to tail the same files.

Took four attempts. The three that failed all predicted the schema; the one
that worked copied a policy SO had already built. Full reasoning in
UPSTREAM_FIXES 2026-08-12.

### Observations from the first ingest, not yet acted on

1. **~1.8M docs in the first 15 minutes.** This is BACKFILL, not steady state:
   `/var/log/remote/*/syslog.log` has been accumulating since the range was
   built and `ignore_older` is unset, so filestream read all of it. Expected
   to fall to the real syslog rate once caught up. Measure before deciding
   whether to bound it — `ignore_older: 72h` is the lever, and SO's own zeek
   policy leaves it empty too.
2. **A `localhost` device bucket (~64k).** Something ships syslog without a
   usable HOSTNAME, so rsyslog files it under /var/log/remote/localhost/.
   Pre-existing — the Splunk path has always had this — but it means those
   events are unattributed in BOTH SIEMs. Worth tracking down.
3. **pfSense not visible in the top 30 buckets.** The terms agg was capped at
   30 and returned exactly 30, so bs-edge-fw / bs-ops-fw may simply be below
   the cut. Confirm with a larger `size` before concluding anything.
4. **`total: 10000` is Elasticsearch's track_total_hits cap**, not a count.
   The per-bucket doc_counts are the real numbers.

### Still open

Whether SO's `elasticfleet` salt state leaves the hand-created package policy
alone across a highstate. The playbook's read-back catches a reconcile at
deploy time; it cannot catch one 15 minutes later. Re-check the policy after a
highstate cycle before calling this durable.

### Outstanding before this can deploy

1. `vyos_gre_source_ip` / `so_gre_remote_underlay` per router — drafted and
   marked VERIFY in both files. They must match EXACTLY.
2. Router interface numbering — `show interfaces` on all four.
3. The blueprint's download `ScriptDefinition` still pins a `main` commit.

---

## 2026-08-17 — Both ranges validated cold from the blueprint

airfield-range and PowerPlant/ss-pp-ab each deployed hands-off from their
blueprint, from scratch, succeeding on attempt 2. Full SO stack in both:
manager, search node, sensors (4 here, 3 in PowerPlant), endpoint telemetry,
analyst access, and central syslog into SO on airfield.

This is the first cold, blueprint-driven run with every fix from the
2026-08-11..16 sequence in place. Prior deploys were manual pulls onto an
existing controller, or predated fixes.

### Fixed between the last attempt and this one

| defect | why nothing caught it |
|---|---|
| L3 `gre` mirror — Zeek discarded 100% of frames | Suricata reads cooked capture natively; 60-verify counted packets on tun0, which were genuinely arriving |
| Zeek re-attach race after `netplan apply` | netplan's renderer is NetworkManager; it re-activates the tunnel after the command returns |
| WPAD PAC missing `172.31.*` | `dnsDomainIs(".blackstone.mil")` covers browsing by NAME; only IP-literal access broke, and SOC is reached by IP |
| `common` never set the Linux hostname | soc-splunk booted as `localhost`; its syslog filed under the wrong device in BOTH SIEMs |
| SO grid had no DNS records | Elastic Agent resolves the manager by short name; enrollment used the IP, so it degraded rather than broke |
| additional-DC promotion left a zone-less DNS server SERVFAILing | a down server times out and clients fail over; a SERVFAIL is a *response*, so they do not |

The through-line: every one of these passed a check that measured the
transport rather than the outcome. All six checks now assert the end state.

### Open

1. `so-ansible` still carries the plain-`gre` defect and is the repo both
   ranges were ported from. Fix before building any new range.
2. Three egress dependencies (SO source, airgap content repos, so-setup
   package fetches) pending Nexus access.
3. DC management-plane A records — `bs-dc01`/`bs-dc02` resolve to both their
   production and `10.255.240.x` addresses, so roughly half of Kerberos/LDAP
   traffic crosses the management plane. Diagnosed 2026-08-13, not fixed.
   Violates CLAUDE.md §8.
4. `syslog_source_ip_map` entries for `bs-ops-fw` (172.31.1.14) and `bs-www`
   (FQDN vs short name) — proposed, not applied; changes Splunk's `host=` too.
5. Per-analyst accounts in both SIEMs; everyone shares one admin login.
6. Distributed Splunk + Enterprise Security design, if that goes ahead.

