# Distributed Splunk + Enterprise Security — Build Sheet

**Subsystem:** SIEM — indexer cluster, dedicated ES search head, management tier
**Enclave:** `soc` — SOC `172.31.7.0/24` (gw `172.31.7.1`, `bs-sec-rtr`)
**Management:** all hosts dual-homed onto `10.255.240.0/20` (gw `10.255.240.1`); `ansible_host` is the mgmt IP
**Deployment:** 100% Ansible, idempotent, blueprint-driven and hands-off
**Supersedes:** the single-instance `soc-splunk` (indexer + search head on one VM)

Written for the stated target: **more than 10 concurrent analysts, with Enterprise Security added.** Either of those alone would not justify this; together they do, and the reasoning for each boundary is given in §2 so the decision can be revisited rather than inherited.

**This sheet is a plan, not a record.** Nothing in it is built. §8 is a gating dependency that should be settled before any role is written.

---

## 1. Host inventory

Four Splunk hosts, one of which already exists and changes role. All Linux hosts are Ubuntu 22.04.5; the ES search head keeps the Desktop image only if analysts continue to RDP into it (see §10 — the recommendation is that they stop).

| Host | Role | SOC (production) | mgmt | vCPU / RAM / disk | Status |
|---|---|---|---|---|---|
| `soc-splunk` | **ES search head** (dedicated) | 172.31.7.19 | 10.255.240.180 | 32 / 64 GB / 300 GB | **exists** — role changes, address unchanged |
| `soc-splunk-idx01` | Indexer, cluster peer | 172.31.7.25 | 10.255.240.187 | 16 / 32 GB / 1 TB | new |
| `soc-splunk-idx02` | Indexer, cluster peer | 172.31.7.26 | 10.255.240.188 | 16 / 32 GB / 1 TB | new |
| `soc-splunk-cm` | Cluster manager + license manager + deployment server + monitoring console | 172.31.7.27 | 10.255.240.189 | 8 / 16 GB / 100 GB | new |

Plus six additional analyst workstations to reach twelve (see §12, open decision 1):

| Host | SOC | mgmt |
|---|---|---|
| `soc-analyst07`..`soc-analyst12` | 172.31.7.37 – .42 | 10.255.240.190 – .195 |

**Addressing.** `.25`–`.30` and `.37`–`.45` are free in the SOC `/24`. Note that `soc-sift` (`.17`) and `soc-flare` (`.18`) declare **no `network_interfaces`** — they are specialised images whose addresses appear only in comments, so any script enumerating used addresses will report them free. They are not. The same trap is documented for the VyOS routers in `docs/security-onion/blueprint-additions.yml`.

`soc-splunk` keeps `172.31.7.19` deliberately: it is the analyst-facing address, it is already `[splunk]` in the inventory, and `splunk_server_ip` is referenced in several places. What changes is what that address *means* — see §5, because the meaning change is the riskiest part of this build.

---

## 2. Why this topology

**A dedicated ES search head.** Splunk does not support running Enterprise Security alongside other apps on a shared search head at this scale, and ES ships 100+ correlation searches that compete directly with analyst ad-hoc search. The current single instance is indexer *and* search head *and* would be ES — three roles whose resource profiles conflict.

**Two indexers, RF=2 / SF=2.** Replication is the smaller reason. The larger one is that **CIM data model acceleration runs on the indexers** and is typically the dominant load in an ES deployment — more than ingest. Two peers spread that and give real replication; a third becomes worthwhile if daily ingest passes a few hundred GB, which this range is nowhere near.

**One management node carrying four roles.** Cluster manager, license manager, deployment server and monitoring console are all light and all singletons. Splitting them buys nothing here and costs three more VMs that must come up unattended.

**No search head cluster, and this is the decision most likely to be questioned.** The textbook answer at >10 analysts is a 3-member SHC. Against it:

- ES on SHC requires the deployer, and ES app deployment through it is genuinely fiddly
- three ES-sized search heads is 96 cores and 192 GB for search alone
- it adds captain election and artifact replication to a deploy that has to run with nobody at a keyboard

A search head failure in a training range is a restart, not a business outage. **Revisit when** investigations must survive a search head failure, or concurrency passes ~20 analysts.

**Concurrency arithmetic**, which is what actually sizes the search head. Splunk allows `base_max_searches (6) + max_searches_per_cpu (1) × cores` concurrent historical searches, and `max_searches_perc` reserves 50% for scheduled work:

| cores | total | scheduled (ES) | ad-hoc | analysts at ~1.5 concurrent |
|---|---|---|---|---|
| 16 | 22 | 11 | 11 | 7 — **too few** |
| 24 | 30 | 15 | 15 | 10 — tight |
| **32** | **38** | **19** | **19** | **12–13 comfortable** |

32 cores is therefore the number, not a round-up. If the analyst count lands at 16, this needs revisiting rather than absorbing.

---

## 3. Blueprint additions

Three new Splunk VMs plus six workstations. `soc-splunk` needs `cpuCount` and `memory` **added** — the current blueprint entry specifies neither, so it inherits the image default, which is how a box serving every analyst ended up unsized.

```yaml
# MODIFY — soc-splunk becomes the dedicated ES search head
- type: "range.resource.primitive.VmInstance::1.0.0"
  name: "soc-splunk"
  properties:
    image: "global/RDP_Ubuntu_Desktop_22.04.5:1.1.0"   # see §10 re: Desktop vs Server
    cpuCount: 32                                        # ADDED — was unspecified
    memory: "65536"                                     # ADDED — was unspecified
    networkInterfaces:
    - name: "SOC"
      ipAddress: "172.31.7.19"                          # unchanged
      prefix: 24
    managementInterface:
      ipAddress: "10.255.240.180"                       # unchanged
      position: "FIRST"

# NEW — indexer cluster peers
- type: "range.resource.primitive.VmInstance::1.0.0"
  name: "soc-splunk-idx01"
  properties:
    image: "global/RDP_Ubuntu_Server_24.04:1.1.0"
    cpuCount: 16
    memory: "32768"
    networkInterfaces:
    - name: "SOC"
      ipAddress: "172.31.7.25"
      prefix: 24
    managementInterface:
      ipAddress: "10.255.240.187"
      position: "FIRST"

- type: "range.resource.primitive.VmInstance::1.0.0"
  name: "soc-splunk-idx02"
  properties:
    image: "global/RDP_Ubuntu_Server_24.04:1.1.0"
    cpuCount: 16
    memory: "32768"
    networkInterfaces:
    - name: "SOC"
      ipAddress: "172.31.7.26"
      prefix: 24
    managementInterface:
      ipAddress: "10.255.240.188"
      position: "FIRST"

# NEW — management tier
- type: "range.resource.primitive.VmInstance::1.0.0"
  name: "soc-splunk-cm"
  properties:
    image: "global/RDP_Ubuntu_Server_24.04:1.1.0"
    cpuCount: 8
    memory: "16384"
    networkInterfaces:
    - name: "SOC"
      ipAddress: "172.31.7.27"
      prefix: 24
    managementInterface:
      ipAddress: "10.255.240.189"
      position: "FIRST"
```

**Indexer disk is retention-driven and currently a guess.** 1 TB is a placeholder until a retention window is chosen (§12, open decision 2). The measured syslog rate alone was ~29 events/sec steady-state after backfill; endpoint telemetry, Windows events and Sysmon are on top of that.

---

## 4. Ports

| Flow | Port | Notes |
|---|---|---|
| UF → indexers | 9997/tcp | receiving, on both peers |
| Search head → indexers | 8089/tcp | distributed search |
| Peers ↔ cluster manager | 8089/tcp | replication control |
| Peer ↔ peer | 9887/tcp | index replication |
| Analyst browser → search head | 8000/tcp | Splunk Web |
| Deployment clients → CM | 8089/tcp | if the deployment server is used for UF management |

All intra-SOC, so no firewall change is required — `172.31.7.0/24` is one segment behind `bs-sec-rtr`. The only cross-boundary flow is the existing UF traffic from every other enclave, which already works.

---

## 5. Inventory, group_vars, host_vars

**`splunk_server_ip` changes meaning, and that is the sharpest edge in this build.** Today it is `172.31.7.19` and means "the indexer UFs send to". After this it must mean "the indexers", plural, and `.19` becomes the search head — which receives nothing.

Two consumers make that dangerous:

- `roles/splunk-forwarder/templates/outputs.conf.j2` renders `server = {{ splunk_server_ip }}:{{ splunk_forwarder_port }}` — a single target
- `group_vars/all/main.yml:179` has **`wazuh_manager_ip: "{{ splunk_server_ip }}"`**, which silently follows any change to it

Recommended shape:

```yaml
# group_vars/all/main.yml
splunk_indexers:                     # NEW — the receiving tier
  - "172.31.7.25"
  - "172.31.7.26"
splunk_forwarder_port: "9997"
splunk_search_head_ip: "172.31.7.19" # NEW — analyst-facing, receives nothing
splunk_cluster_manager_ip: "172.31.7.27"
splunk_replication_port: "9887"
splunk_rf: 2
splunk_sf: 2
# splunk_server_ip: RETIRED — every consumer must be migrated to one of the
# three above, deliberately, so nothing inherits the old meaning by accident.
```

**Prefer indexer discovery over a static list.** With the cluster manager present, forwarders can learn peers from it, so adding a third indexer later needs no forwarder change. The static list above is the fallback if discovery proves awkward on this platform.

New inventory groups:

```ini
[splunk_search_head]
soc-splunk

[splunk_indexer]
soc-splunk-idx01
soc-splunk-idx02

[splunk_cluster_manager]
soc-splunk-cm

[splunk_cluster:children]
splunk_search_head
splunk_indexer
splunk_cluster_manager
```

The existing `[splunk]` group is currently used as a forwarder **exclusion** (`linux:!splunk:!so_all`). It must expand to cover all four hosts, or the new Splunk servers will get universal forwarders pointed at themselves.

---

## 6. Roles

| Role | Runs on | Does |
|---|---|---|
| `splunk_cluster_manager` | `splunk_cluster_manager` | installs Splunk, enables cluster-manager mode with RF/SF, becomes license manager, enables MC and deployment server |
| `splunk_indexer` | `splunk_indexer` | installs Splunk, joins the cluster as a peer, enables receiving on 9997, becomes a license peer |
| `splunk_search_head` | `splunk_search_head` | installs Splunk, joins as a search head to the cluster manager, becomes a license peer |
| `splunk-es` | `splunk_search_head` | installs Enterprise Security — **port from `PowerPlant/ss-pp-ab/roles/splunk-es`**, which already installs from a pre-staged `.spl` |
| `splunk_cim` | `splunk_search_head` + peers via CM | installs the CIM add-on and the TAs, enables the chosen accelerated data models |
| `splunk` (existing) | — | **retire or narrow**; it currently does single-instance indexer + search head |
| `splunk-forwarder` (existing) | unchanged hosts | only its `outputs.conf` target changes |

`pass4SymmKey` and the cluster secret belong in `group_vars/all/vault.yml` alongside the existing Splunk credentials.

---

## 7. Deployment order

Each step depends on the previous being green, the same shape as the Security Onion phases:

1. **Cluster manager** — must exist before any peer can join
2. **Indexers** — join the cluster, enable receiving
3. **Search head** — connects to the cluster manager as a search head
4. **ES** on the search head
5. **Indexes app pushed to peers via the CM** — ES's own indexes (`notable`, `risk`, `threat_activity`, …) must exist on the indexers, and the supported path is the CM's `manager-apps`, not editing peers directly
6. **CIM add-on and TAs**, then enable data model acceleration
7. **Forwarders repointed** to the indexers
8. **Verification** (§11)

Steps 5 and 7 are the ones most likely to be got wrong: pushing indexes to peers by hand rather than through the cluster manager, and repointing forwarders before the indexers are actually receiving.

---

## 8. GATING DEPENDENCY — ES artifacts with no egress

**Settle this before writing any role.**

Enterprise Security is ~1 GB, plus the CIM add-on and every TA. These ranges target platforms with **no external access, not even a proxy**, so it cannot be fetched at deploy time. The current bundle (`ab_mb.tgz`) is 5.8 MB — adding a gigabyte-plus changes the delivery story entirely.

PowerPlant's `splunk-es` role already assumes the answer: it installs from `{{ ansible_installers }}/splunk/splunk-es.spl`, i.e. a file **pre-staged on the controller** at `/var/share/installers/`. That is the established pattern in this project, and the same one `group_vars/linux.yml` uses for the Splunk packages themselves (`splunk_server_installer` points at the in-platform Nexus with a pre-staged fallback).

Three options:

| option | viability |
|---|---|
| **In-platform Nexus** | the intended answer; blocked on access, which Eric could not self-serve as of 2026-08-07 |
| **Pre-staged on the controller image** | works today if the image can carry it; matches what `splunk-es` already expects |
| **In the tarball** | technically possible, but a >1 GB tarball pulled per deploy is a poor fit for the blueprint download step |

This is the same class as the ETOPEN ruleset, two orders of magnitude larger. **Recommendation: do not begin implementation until the artifact source is decided**, because it determines whether `splunk-es` is a copy task or a download task, and whether the CIM/TA install is one artifact or a dozen.

---

## 9. The larger half — CIM normalization

Standing up the infrastructure is perhaps 30% of this work.

**ES correlation searches fire off accelerated data models, not raw events.** If the range's data is not CIM-compliant, ES installs perfectly and generates no notables — which looks like a broken deployment and is actually a content gap. This is the part that is routinely underestimated.

Required:

- **`Splunk_TA_nix`** — Linux syslog from every Ubuntu host
- **`Splunk_TA_windows`** — Windows Security/System/Application, and the WEC subscriptions from `bs-wec` / `fops-wec01`
- **Sysmon TA** — Sysmon is already deployed range-wide by `site.yml`
- **pfSense and VyOS** — no first-party TA; needs sourcetype definitions and field extractions written against `netfw`, which `soc-syslog` already populates

Data models worth accelerating, chosen by what the training scenarios actually exercise:

| model | fed by |
|---|---|
| Authentication | Windows Security, WEC, Linux auth |
| Network_Traffic | pfSense + VyOS via `netfw` |
| Endpoint | Sysmon |
| Malware | EDR, once the CrowdStrike/SentinelOne choice is made |
| Web | `bs-proxy` (squid) access logs |

**Accelerate only these.** Every accelerated model is continuous indexer load, and the default set is considerably larger than what this range feeds.

Worth noting an interaction with the existing dual-SIEM design: Security Onion already consumes the same endpoint telemetry via Elastic Agent, and the same syslog via the filestream input on `soc-syslog`. Splunk keeping its own copy is deliberate (see `project_siem_choice`), but the *questions each tool answers* should be decided explicitly, or twelve analysts will check two places for the same answer.

---

## 10. Analyst access and RBAC

**Analysts should stop RDP-ing into the search head.** `host_vars/soc-splunk.yml` currently records that they RDP in and drive Splunk Web in a local browser — which is why it runs the Desktop image. That does not scale past a couple of people and puts twelve interactive sessions on the box running ES.

They should browse to `https://172.31.7.19:8000` from their own workstations. **This already works** as of 2026-08-13: the WPAD PAC previously sent IP-literal requests to `172.31.x` through squid, which is fixed. If RDP access is dropped, `soc-splunk` can move to the Server image and recover the RAM.

**Individual accounts, not shared `admin`.** Twelve analysts on one login means overwritten saved searches and dashboards, one shared job history, no attribution for after-action review, and every analyst holding rights to delete an index. For a *training* range the attribution matters more than the hygiene — assessment is impossible when every action was taken by `admin`.

```
role: analyst
  inherits:        user
  srchIndexesAllowed: windows, sysmon, linux, netfw, ot, wec, notable, risk
  capabilities:    search, schedule_search, rtsearch (no admin_all_objects,
                   no delete_by_keyword, no edit_indexes)
  srchJobsQuota:   6      (default user is 3; ES analysts run more)
```

One account per workstation, `soc-analyst01`..`12`, mapping to the named domain users the range already creates. The same argument applies to Security Onion, which also has a single shared `admin@blackstone.mil` — worth doing both together.

---

## 11. Acceptance tests

Each asserts an **outcome**, not that a service is running. This project has had six separate defects where a check measured the transport and reported it as the outcome; see `UPSTREAM_FIXES.md`.

| # | Test | Pass |
|---|---|---|
| 1 | `splunk show cluster-status` on the CM | all peers Up, **search factor met**, replication factor met |
| 2 | `| rest /services/search/distributed/peers` on the SH | both indexers, status `Up` |
| 3 | Ingest reaches **both** peers | `| tstats count where index=* by splunk_server` returns both, non-zero |
| 4 | A UF on an arbitrary host | `splunk list forward-server` shows both indexers active |
| 5 | ES indexes exist **on the peers** | `notable`, `risk`, `threat_activity` present on both, pushed via CM |
| 6 | Data model acceleration is progressing | each enabled model >0% complete and advancing between two samples |
| 7 | **ES generates notables** | `index=notable` non-zero within 24h of ingest — the check that actually proves ES is wired to the data |
| 8 | Concurrency headroom | `| rest /services/server/status/resource-usage/splunk-processes` under load shows ad-hoc slots remaining |
| 9 | Analyst RBAC | an `analyst` account can search the SOC indexes and **cannot** delete an index |

Test 7 is the one that matters. Tests 1–6 can all pass while ES produces nothing, because they verify plumbing and ES fires off *models*, not events.

---

## 12. Open decisions

1. **Analyst count.** This sheet assumes **12** (6 existing + 6 new) with headroom to ~13 at 32 cores. At 16 analysts the search head needs revisiting, not rounding up.
2. **Retention window.** Drives indexer disk; 1 TB each is a placeholder. Needs a stated window plus `maxTotalDataSizeMB` per index rather than defaults.
3. **ES artifact source** — §8. **Gating.**
4. **Which data models to accelerate** — §9 proposes five.
5. **EDR product** (CrowdStrike vs SentinelOne) — still open from `CLAUDE.md` §12, and it determines whether the Malware data model has a feed at all.
6. **RDP to the search head** — recommend dropping, which also allows the Server image.
7. **Splunk/SO division of labour** — which questions each tool answers, now that both hold overlapping data.

---

## 13. Build-time version checklist

Pin and record at build time:

- Splunk Enterprise — must match across CM, peers and SH; a version skew between search head and peers is unsupported
- Splunk Enterprise Security — check the ES↔Enterprise compatibility matrix before pinning
- Splunk Common Information Model add-on
- `Splunk_TA_nix`, `Splunk_TA_windows`, Sysmon TA
- Universal Forwarder — currently `9.4.1` per `group_vars/linux.yml`; a UF newer than the indexers is unsupported

Record the resolved versions here once chosen, the same way the fuel and power sheets do.
