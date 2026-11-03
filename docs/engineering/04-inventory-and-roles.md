# 04 — Inventory and Roles

## How membership drives behaviour

The `hosts` INI inventory is the control surface for the whole build. Group
names are **load-bearing**: roles and plays address them by name
(`groups['so_manager']`, `hosts: windows`, `windows:linux:!so_all:!no_endpoint_agent`),
so renaming a group in `hosts` without renaming it in the roles breaks the build
silently. A host's capabilities and the telemetry it emits are decided entirely
by which groups it is in.

Two naming choices are deliberate and worth internalising:

- There is **no `[unmanaged]` group.** The OT hosts the enterprise would call
  "unmanaged" are fully Ansible-managed here — the group is named
  `[no_endpoint_agent]` because the only thing those hosts are excluded from is
  *endpoint telemetry*, not management.
- There is **no generic `[siem]` umbrella group.** Used as an exclusion it would
  silently change meaning every time a host joined it; plays name `[so_all]`
  directly instead.

## Inventory groups (current expanded sizes)

Platform / OS groups:

| Group | Size | Meaning |
|---|---|---|
| `windows` | 48 | all Windows VMs (`:children` of `win10` 15 / `win11` 23 / `winserver2022` 10) |
| `linux` | 26 (30 stacked) | `:children` of `ubuntu22` / `ubuntu24` / `ubuntu25`; the stacked variant adds the four Splunk VMs |
| `vyos` | 5 | `bs-edge-rtr`, `bs-core-rtr`, `bs-ops-rtr`, `bs-sec-rtr`, `bs-modbus-gateway` |
| `pfsense` | 2 | `bs-edge-fw`, `bs-ops-fw` |

Active-Directory groups:

| Group | Size | Meaning |
|---|---|---|
| `pdc` / `additional_dc` / `domain_controllers` | 2 / 2 / 4 | DC promotion roles across both domains |
| `members_blackstone` / `members_fops` | 29 / 15 | domain-joined non-DC hosts per domain |
| `wec` | 2 | Windows Event Collectors (`bs-wec`, `fops-wec01`) |

Role / posture groups:

| Group | Size | Meaning |
|---|---|---|
| `aue` | 38 | emulated-user workstations |
| `ae` | 10 | attack-emulation targets: both domains' DCs, file servers and WECs |
| `fileservers` | 4 | `bs-file01/02`, `fops-file01/02` |
| `no_endpoint_agent` | 3 | OT hosts that run the process, so get **no** endpoint agent: `ff-plc-1`, `fuel-farm-sim`, `control-room-hmi` |
| `ot_fuel` / `ot_mtu` | 4 / 1 | fuel-farm OT hosts; the MTU HMI segment |
| `atc` | 3 | ATC subsystem Linux hosts (`atc-radar`, `atc-weather`, `atc-station`) |
| `soc_analysts` | 6 | analyst workstations (`soc-analyst01..06`) |
| `dmz` | 3 | `bs-www`, `bs-ftp`, `bs-ns1` |

Security Onion groups:

| Group | Size | Meaning |
|---|---|---|
| `so_manager` / `so_search` / `so_sensor` | 1 / 1 / 4 | grid node roles |
| `so_all` (`:children`) | 6 | every grid node |
| `so_mirror_routers` | 4 | the VyOS routers carrying a GRE mirror (`bs-core-rtr`, `bs-ops-rtr`, `bs-modbus-gateway`, `bs-edge-rtr`) |
| `ansible_controller` | 1 | the mirror/controller host — `ansible_connection=local`, deliberately **not** in `[linux]` |

Splunk + Malcolm groups (stacked variant only):

| Group | Size | Meaning |
|---|---|---|
| `splunk_search_head` / `splunk_indexer` / `splunk_cluster_manager` | 1 / 2 / 1 | the distributed Splunk cluster |
| `splunk-forwarder` (`:children` = windows + linux) | 78 | universal-forwarder recipients |
| `splunk_forwarder_exempt` | — | the Splunk tier itself + `so_manager` + `so_search` |
| `malcolm` | 1 | the Malcolm OT appliance (`soc-malcolm`) |

The key derived numbers today: **85 managed hosts** on the monitoring-only
variant (**89** on the stacked variant); **48 Windows**; **48 domain-joined**
(44 members + 4 DCs); **66 Elastic-Agent endpoints** (`[windows]`+`[linux]`
minus `[so_all]` minus `[no_endpoint_agent]`). Sizes are computed from the
current inventory and move when hosts are added/removed; the local `ansible`
control node is counted separately from the managed total.

### Why the control node must be `ansible_connection=local`

`[ansible_controller]` holds exactly one host, and it carries
`ansible_connection=local`. Without it the controller inherits the default SSH
connection and Ansible tries to SSH to itself; that one host returns
`unreachable`, the group has no other member, and the run ends with "NO MORE
HOSTS LEFT" at the first phase — every later phase silently never executes,
with `failed=0` on every host because nothing that could fail was reached. The
controller's `ansible_host` is still its management IP: connection-local and
reachable-at-that-IP are different facts about the same host, and the SO nodes
fetch the in-range mirror from that IP.

## `group_vars` hierarchy (lowest → highest precedence)

1. `group_vars/all/` — `main.yml` (proxy, syslog server IP, domain-admin
   account, shared tunables), `security_onion.yml` (the entire SO stack: pins,
   registry, subnets, airgap + Nexus source URLs), `vault.yml` (encrypted
   secrets).
2. `group_vars/<group>.yml` — `windows.yml`, `linux.yml`, `vyos.yml`,
   `pfsense.yml`, `blackstone.yml`, `fops.yml`, `fuel.yml`, `atc.yml`, and (on
   the stacked variant) `ubuntu25.yml`, `malcolm.yml`, `splunk_indexer.yml`,
   `splunk_search_head.yml`.
3. `host_vars/<host>.yml` — per-host IPs, interfaces, role-specific tuning.
4. Inline play vars in `site.yml`.

No hardcoded IPs live in tasks or templates — `group_vars` / `host_vars` are the
single source of truth.

## Roles

Roles are pulled from SimSpace's core Ansible library first, then overridden by
the local overlay roles (an overlay role of the same name **replaces** the base
role wholesale). The tarball bundles the base roles it uses plus the custom
overlays; the exact set is auto-discovered from every play (see the build &
deploy chapter). The monitoring-only variant carries 52 roles; the stacked
variant adds 11 (Splunk ×8, Malcolm ×2, `docker_engine`).

### Range / enterprise roles

| Role | Purpose |
|---|---|
| `init` | Waits for WinRM, then repairs and pins the default-gateway ARP entry (an impostor MAC answers ARP/ICMP for the gateway on some segments). |
| `common` | Overlay of the base baseline role. |
| `dcpromo` / `dcpromo_child_heal` | Promote the forest-root and child-domain DCs; the heal pass clears half-join residue before a child promotion retries. |
| `additional_dc` | Joins the second DC in each domain. |
| `domain_member_retry` | Robust domain join with repair passes — the join path, not the member, is usually what fails on a cold build. |
| `dns` | AD-integrated zones + records; child-DC `DomainDnsZones` partition gate; record writes retry. |
| `global_dns` | Scenario public-name resolution on `is-inet` for the simulated Internet. |
| `create_users` / `ue` / `aue_agent` | Seed domain users; drive emulated-user activity. |
| `drainhole` | Windows endpoint activity harness (`network_mgr.exe`), staged from Nexus and set to run at startup across the Windows fleet alongside the AUE agent. |
| `fileserver` / `mapped_drive` | File servers and the mapped drives that reference them. |
| `email` | Postfix + Dovecot + webmail on `is-inet`, multi-domain for both forests. |
| `blackstone_www` | The public web presence on `bs-www`. |
| `squid` / `platform_proxy` | The in-scenario proxy (`bs-proxy`) and platform-proxy wiring. |
| `strip_apipa` | Removes `169.254.x.x` APIPA addresses Windows assigns during DHCP→static handoff. |
| `network_discovery` / `disable_firewall` / `disable_uac` / `autologin` / `chrome` / `secpol` / `root_certs` / `ae_gpo` | Windows posture and workstation enablement. |
| `pfsense_firewall` | Drives pfSense 2.8.1 via `pfsensible.core` + `php -r` escape hatches (interfaces, gateways, default-gw pin, OSPF/BGP, static routes, rules, syslog). |
| `vyos` / `vyos_mirror` | VyOS router config; GRE `gretap` tunnels + `tc mirred` mirror rules feeding the sensors (and Malcolm). |
| `syslog_server` | `soc-syslog` as the central rsyslog collector (per-device files + agent fan-out). |

### OT / fuel-farm roles

| Role | Purpose |
|---|---|
| `fuel_plc` | OpenPLC v4 controller on `ff-plc-1` (Modbus TCP `:502`). |
| `fuel_sim` | pymodbus field simulation on `fuel-farm-sim` — it *is* the plant. |
| `fuel_hmi` | FUXA operator console on `control-room-hmi` (`:1881`); the Modbus-serial driver installs into the container from the Nexus npm group. |
| `fuel_historian` | Telegraf + InfluxDB 2.7 + Grafana on `fuel-hist`. |
| `fuel_db` | PostgreSQL 16 + TimescaleDB audit DB on `fuel-db`. |

### ATC roles

| Role | Purpose |
|---|---|
| `atc_core` | The radar/ADS-B replay and controller working-position backend. |
| `atc_cwp` | Controller working position / display on `atc-station` and the flight display clients. |
| `atc_inject` | The recorded flight-data injector. |
| `atc_weather` | The recorded-weather feed on `atc-weather`. |

### Monitoring roles

| Role | Purpose |
|---|---|
| `so_apt_mirror` | nginx on the controller serving the SO source snapshot, airgap content, ETOPEN rules and the skopeo-built container registry. |
| `so_base` | Prereqs on every grid node before `so-setup`. |
| `so_manager` | Renders the answer file, seeds the registry from the mirror, runs `so-setup`, verifies the outcome (not the exit code). |
| `so_search` | Search-node install + grid join. |
| `so_sensor` | Sensor install + grid join, GRE decap, Zeek/Suricata. |
| `so_fleet_integrations` | Host-scoped Fleet policies + integrations (pfSense, VyOS, host logs) driven directly against the Fleet API. |
| `elastic_agent` | Installs/enrols the Elastic Agent on every endpoint from controller-staged installers. |
| `sysmon` | Installs Sysmon across the `[windows]` fleet from a Nexus-staged installer. |
| `docker_engine` *(stacked)* | Installs Docker honouring the Nexus insecure-registry/proxy config (consumed by Malcolm). |
| `malcolm` / `malcolm_host_prep` *(stacked)* | Deploys the Malcolm OT appliance (26.08.0, images from Nexus retagged to `ghcr.io`) and prepares its `gretap` decap interface. |
| `splunk` / `splunk_cluster_manager` / `splunk_indexer` / `splunk_search_head` / `splunk_license_peer` / `splunk-forwarder` / `splunk-es` / `splunk-verify-user` *(stacked)* | The distributed Splunk cluster, the universal forwarders, Enterprise Security content, and a post-deploy login/search proof. |

### Adding a role

Don't edit the role list in `build_tarball.sh` — it auto-discovers from plays.
Add a role by referencing it in a play (`roles:`, `import_role` or
`include_role`), and it is bundled on the next build.
