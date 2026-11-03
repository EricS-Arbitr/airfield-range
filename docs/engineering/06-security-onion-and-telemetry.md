# 06 — Security Monitoring and Telemetry

Security Onion is the range's network-security monitoring grid; every log source
reports to it. On the **Splunk-stacked variant** a distributed Splunk cluster
runs alongside it, fed in parallel by a universal forwarder on every managed
host, and a Malcolm appliance adds ICS-protocol deep-packet analysis for the
fuel-farm enclave (see the later sections); the monitoring stacks are
independent.

## The grid

Distributed Security Onion 2.4 on the SOC segment (`172.31.7.0/24`):

| Node | Address | Role |
|---|---|---|
| `soc-so-manager` | `172.31.7.15` | Manager, SOC web UI (`https://172.31.7.15`), Fleet |
| `soc-so-search` | `172.31.7.20` | Search node (`SEARCHNODE`) |
| `soc-sensor-corp` | `172.31.7.21` | Enterprise traffic |
| `soc-sensor-fops` | `172.31.7.22` | Flight-ops traffic |
| `soc-sensor-ot` | `172.31.7.23` | Fuel-farm OT traffic |
| `soc-sensor-edge` | `172.31.7.24` | Attack path + simulated Internet |

Pins live in `group_vars/all/security_onion.yml`:

| Var | Value | Meaning |
|---|---|---|
| `so_git_ref` | `55af7eb…` | SO 2.4/main HEAD snapshotted 2026-07-22 (a SHA, not a tag — the answer-file mechanism ships only on 2.4/main) |
| `so_registry_image_version` | `2.4.211` | SO image set |
| `so_registry_registry_version` | `3.0.0` | registry container |
| `so_registry_elasticsearch_version` | `9.0.8` | Elasticsearch |
| `so_airgap_mode` | `true` | no internet during setup |
| `so_registry_min_repos` | `22` | setup is gated on this many repos present in the local registry |

The SOC web UI is configured for **IP access** (`BASIC` advertisement), so there
is no hostname to resolve — reach it from an analyst workstation at
`https://172.31.7.15`, admin `admin@blackstone.mil`. The grid runs on Ubuntu
Desktop 22.04.5: SO 2.4 at the pinned SHA refuses `noble` outright and only
tolerates `jammy` because `so_base` fakes it as `focal`, which is why these
nodes sit in `[ubuntu22]` while the base Linux baseline is 24.04.

## Airgap install: the registry seed

`so-setup` runs in airgap mode and pulls container images from a **local
registry**, never ghcr/dockerhub. The flow:

1. `so_apt_mirror` (on the controller) serves the SO source snapshot, the
   airgap content, the ETOPEN ruleset, and the container-registry artifacts.
2. The registry images are staged as `registry.tar` / `registry_image.tar`
   **before** `so-setup` runs — `so-setup` deletes the docker proxy drop-in
   mid-run, so anything not already local is unreachable.
3. `so_manager` seeds the registry, renders the answer file, runs `so-setup`,
   and verifies the **outcome** (repos present, services up) rather than the
   exit code. Setup is gated on `so_registry_min_repos` (22) being present.

With `so_registry_source` pointing at the Nexus docker registry the controller
pulls the registry contents from Nexus; with `so_registry_artifact_url` set to a
raw base it downloads a prebuilt tarball; otherwise it builds the registry
locally with skopeo. No in-play system needs internet access.

## Positional sensor coverage

Each sensor is fed by exactly one router's `gretap` mirror (see the network
chapter for the `tc`/`gretap` mechanics). There is no aggregation tap, so:

- An event on `soc-sensor-ot` and absent from `soc-sensor-fops` is traffic that
  did **not** cross the OT boundary.
- Cross-segment activity must be correlated across sensors, not read from one
  stream.
- The management plane is excluded from every mirror (`action pass` for
  `10.255.240.0/20` ahead of the `mirred` catch-all), so trainees never see the
  orchestration plane.

| Sensor | Mirror source | Segments |
|---|---|---|
| `soc-sensor-corp` | `bs-core-rtr` | Services, HQ, IT, Supply |
| `soc-sensor-fops` | `bs-ops-rtr` | Flight-Ops-Services, Flight-Control-Center, Flight-Ops-Users |
| `soc-sensor-ot` | `bs-modbus-gateway` | MTU, Fuel-Farm PLC, Fuel-Farm Sim |
| `soc-sensor-edge` | `bs-edge-rtr` | simulated Internet + edge transit |

The single most common sensor failure is a `gre` (layer-3) tunnel where a
`gretap` (layer-2) tunnel is required: Suricata keeps alerting while Zeek
produces **zero** `conn.log` records. Both tunnel ends must be `gretap`.

## Endpoint telemetry

`elastic_agent` enrols every `[windows]` and `[linux]` host into SO's Fleet from
controller-staged installers — **66 endpoints**: every Windows and Linux host
except the SO grid (`[so_all]`, which runs its own agent) and the three OT hosts
in `[no_endpoint_agent]`. **Sysmon** runs across the entire `[windows]` fleet
(48 hosts) from a Nexus-staged installer.

The OT exclusion is the instructive one. Endpoint-agent placement in the OT
chain follows the **Purdue level, not the subnet**: a host gets an agent only if
its job is to *monitor* the process, not to *run* it.

- `ff-plc-1` (L1, the OpenPLC controller), `fuel-farm-sim` (L0/1, the field
  simulation) and `control-room-hmi` (L2, the FUXA operator console) get **no**
  agent — they are the control loop, and `control-room-hmi` is additionally a
  Purdue bridge host where an agent's failure mode is worst.
- `fuel-hist` (L3 historian) and `fuel-db` (L3 audit DB) **are** agent targets —
  Level 3 records and reports, it does not drive the process, and endpoint
  agents are routine there.

Excluding the HMI is a deliberate, conservative reading for a *training* range:
it leaves a real endpoint-visibility gap in the OT enclave that trainees have to
close with network monitoring instead. `soc-sensor-ot` mirrors all three OT
segments, so the traffic is still fully visible on the wire — that is the
compensating control, and teaching it is the point.

Because the two agent-bearing OT hosts cross the L3.5 boundary, their permitted
Fleet path is expressed as two `/32`s in `so_agent_endpoint_subnets` (not a
`/24` that would also admit `fuel-farm-sim`), and `75-endpoint` asserts every
agent target's address falls inside the permitted set — add a host on a new
segment and the assert fails loudly instead of that host silently failing to
enrol.

## Application and device log sources

Non-endpoint sources reach SO as **parsed datasets**, each on a dedicated Fleet
agent policy created by `so_fleet_integrations`. The firewalls and routers
cannot run an agent, so `soc-syslog` is the central rsyslog collector for them:

- **pfSense ×2.** rsyslog on `soc-syslog` relays each firewall's raw datagram to
  a per-firewall loopback port — `bs-edge-fw` → 9001, `bs-ops-fw` → 9002 — where
  the Elastic pfSense integration parses it. The port carries the device
  identity, because pfSense omits the hostname field on remote forwards and the
  loopback relay would otherwise stamp every message with the same source
  address. Nothing re-renders the message, so grok parsing is untouched.
- **VyOS ×5.** The agent tails `soc-syslog`'s `/var/log/remote/<router>/syslog.log`
  directly. The VyOS paths are **enumerated per router, never globbed** —
  `/var/log/remote/` also holds the SO grid nodes' own daemon logs (they are in
  `[linux]`), and SO already collects those natively, so reading them back would
  be duplicate data about the thing doing the reading.

### Fleet-integration rules worth knowing

- Derive the input id, never pattern-match it: the key is
  `<policy_template>-<input_type>`, and a package's policy template is not
  always named after the package.
- A dedicated policy contains only what you put in it, so moving a host off
  `endpoints-initial` strips its endpoint telemetry unless the base integrations
  are mirrored too (`mirror_base: true`).
- Renaming an integration does not replace the old one (`…_check` matches by
  name), so add the old name to the retired-integrations list in the same change.
- `so_fleet_integrations` drives `elastic_fleet_integration_create/_update`
  directly rather than `so-elastic-fleet-integration-policy-load`, which walks
  only hardcoded directories and would never read one we add.

## Splunk cluster (stacked variant)

On the Splunk-stacked variant, a distributed Splunk Enterprise cluster runs on
the SOC segment alongside the Security Onion grid, giving analysts a second,
independent SIEM over the same estate:

| Node | Address | Role |
|---|---|---|
| `soc-splunk` | `172.31.7.25` | Search head (analyst-facing web UI) |
| `soc-splunk-idx01` | `172.31.7.26` | Indexer (cluster peer) |
| `soc-splunk-idx02` | `172.31.7.27` | Indexer (cluster peer) |
| `soc-splunk-cm` | `172.31.7.28` | Cluster manager + license manager |

The two indexers form a single indexer cluster (label `mb-idxc`, replication and
search factor 2); the cluster manager distributes `indexes.conf` and is the one
license manager — `splunk_license_peer` points the manager, both peers and the
search head at it, because four instances each as their own license manager is
four violations, not one deployment. Collection is by **universal forwarder on
every managed host** (`[splunk-forwarder]` = `[windows]` + `[linux]`, 78 hosts),
with the Splunk tier itself plus `so_manager`/`so_search` carved out by
`[splunk_forwarder_exempt]`:

- Windows hosts forward event logs and Sysmon; Linux hosts forward syslog and
  service logs.
- The firewalls' and routers' syslog is aggregated on `soc-syslog` and a
  forwarder there ships it to the indexers.
- The Splunk cluster hosts are themselves Elastic-Agent targets — an indexer is
  exactly the kind of host worth watching — which is why a path-scoped Defend
  exclusion for `/opt/splunk/var` exists, so their own bucket churn does not
  swamp the dataset.

Because the forwarder path and the Elastic Agent path are independent, the same
source feeds both stacks — an analyst can be trained or assessed on either, and
a gap in one is not a gap in the other. `splunk-verify-user` proves after deploy
that a user can actually log in and search, because a search head that cannot
search is not a working SIEM.

## Malcolm — OT deep-packet analysis (stacked variant)

`soc-malcolm` (`172.31.7.30`, SOC segment) is a CISA Malcolm appliance dedicated
to the fuel-farm enclave. Security Onion sees the same OT traffic on
`soc-sensor-ot`; Malcolm adds the ICS parsers (Modbus and the rest) that make
process traffic legible.

| Property | Value |
|---|---|
| Version | Malcolm **26.08.0** (OpenSearch, Arkime, Zeek, Suricata, Logstash) under Docker |
| Image source | pulled from the Nexus docker registry and **retagged locally to their `ghcr.io` names** — the compose stays `ghcr.io` because Malcolm's keystore step greps the compose for those names; rewriting the refs breaks keystore creation and the stack never starts |
| Source | `ng_raw` tarball, unpacked into the install dir (gates the git clone) |
| Capture | live on `otmir0` — an IP-less `gretap` decap interface; Zeek/Suricata/netsniff-ng read the decapsulated mirror |
| Feed | a second `gretap` tunnel (`tun1`) fanned out from `bs-modbus-gateway`, carrying the fuel-farm process segments, aimed at `172.31.7.30` |
| Web UI | `https://172.31.7.30` |

The appliance is **not** in `[linux]`, so the base baseline never runs against
it; the Malcolm phase owns its whole configuration. The tunnel is receive-only —
Malcolm observes the mirrored flows, it never replies.
