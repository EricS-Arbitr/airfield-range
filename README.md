# airfield-range

Range-specific Ansible overlay for the **Blackstone Auxiliary Field**
cyber-range scenario on the SimSpace NG platform, with **Security Onion 2.4**
as the range's SIEM.

The scenario is a military airfield: a single Active Directory forest with a
`blackstone.mil` root (enterprise / SOC) and a `fops.blackstone.mil` child
(flight operations), an OT fuel farm, and a simulated air-traffic-control
subsystem. This is the **Security-Onion-only** range; its sibling
[`airfield-stacked`](../airfield-stacked) adds a distributed Splunk cluster and
a Malcolm OT appliance on the same scenario. (`ss-pp-so` is the same SO-only
relationship on the PowerPlant side and the working reference for the grid.)

This is an overlay, not a standalone playbook. It layers on the customer's
shared platform repo at `../range-development-ansible/`: that repo ships base
roles, this one ships the range's inventory, variables, custom roles and
playbooks. `build_tarball.sh` combines the two into `airfield_seconion.tgz`,
which is extracted to `/etc/ansible` on the range's Ansible controller.

## Build and deploy

```bash
./build_tarball.sh          # -> airfield_seconion.tgz  (validates before it writes)
# copy airfield_seconion.tgz to the controller, extract to /etc/ansible, then:
./deploy.sh                 # site.yml, up to 3 attempts
BOOT_DELAY=0 ./deploy.sh    # skip the boot wait on an already-up range
```

`build_tarball.sh` refuses to write an archive that would not deploy. It checks
Jinja variable references (`verify_vars.py`), free-form shell arguments that
Ansible's `split_args()` would reject (`verify_shell_args.py`), handler
`notify`/`listen` wiring (`verify_handlers.py`), duplicate YAML keys
(`verify_dup_keys.py`), task-keyword misuse (`verify_task_keywords.py`), and the
Elastic Defend path filters (`verify_defend_filters.py`).

`deploy.sh` runs `site.yml` up to three times: full sweep, then a
retry-file-scoped pass over only the hosts that failed, then a full sweep again
as a safety net. A cold build from a fresh blueprint legitimately needs all
three — some AD and grid steps self-clear on a later attempt.

## The range

85 hosts across 13 host-bearing segments. 44 are domain members — 29 on
`blackstone.mil`, 15 on `fops.blackstone.mil` — and the managed Windows fleet is
48 hosts. The two domains sit in one forest; parent/child trust is automatic and
bidirectional.

**Security Onion** is a distributed grid, not a single box:

| Host | Role |
|---|---|
| `soc-so-manager` | grid manager |
| `soc-so-search` | search node |
| `soc-sensor-corp` | enterprise traffic |
| `soc-sensor-fops` | flight-ops traffic |
| `soc-sensor-ot` | fuel-farm OT traffic |
| `soc-sensor-edge` | attack path and simulated Internet |

There is no aggregation tap — each sensor watches its own vantage point, fed by
GRE + `tc` mirror rules from the routers in `[so_mirror_routers]`
(`bs-core-rtr`, `bs-ops-rtr`, `bs-modbus-gateway`, `bs-edge-rtr`). Endpoints
report through an Elastic Agent enrolled into the grid's Fleet.

Two scenario subsystems ride on top of the baseline (see *Subsystems* below):
an OT **fuel farm** (POL) and a virtual **air-traffic-control** stack.

## What gets deployed

`site.yml` is the entry point. `10-mirror` is imported first so its
container-image build overlaps the range baseline, and because a broken mirror
should fail in minutes rather than after a multi-hour baseline. The baseline
plays run inline in `site.yml` (network, the two-forest AD build, hosts,
services); the Security Onion phases and the two subsystems are imported at the
tail.

| Phase | Does |
|---|---|
| `10-mirror` | nginx on the controller serving the SO source, detection content and container registry artifacts |
| *(baseline, inline)* | network, two-forest AD (`blackstone.mil` + `fops.blackstone.mil`), VyOS/pfSense, DNS, domain joins, hosts, services |
| `05-time` | Windows clock correction, DC-first |
| `20-vyos` | GRE tunnels + `tc` mirror rules to the sensors |
| `30-prereqs` | `so_base` on every grid node |
| `40-manager` | waits for the registry artifacts, then installs the manager |
| `50-nodes` | search + sensors join the grid |
| `60-verify` | grid health |
| `70-analyst` | analyst workstation enablement (SIFT / FLARE / OpenVAS bench) |
| `75-endpoint` | Sysmon, then Elastic Agent enrolment into Fleet |
| `80-fleet-integrations` | host-scoped log sources into SO via dedicated Fleet policies |
| `fuel_farm_playbook.yml` | the OT fuel-farm subsystem (runs after the SIEM tier) |
| `atc_playbook.yml` | the air-traffic-control subsystem |

## Security Onion

A distributed grid: manager, search node and four sensors. Endpoints report
through Elastic Agent; the sensors see network traffic through GRE mirrors from
the VyOS/OT routers.

Container images and the SO source snapshot are served from the controller's
mirror (`so_apt_mirror` runs on the controller, not a grid node) rather than
pulled from ghcr.io, so no in-play system needs internet access. The source and
images are staged from the in-platform Nexus (`ng_raw` for the source/airgap
content, `nexus-docker` for the images); the controller is the only host that
reaches out, and only to build those artifacts.

## Subsystems

These are what distinguish the airfield scenario from the PowerPlant ranges.
Both deploy after the SIEM tier so the grid can see their traffic as it comes
up, and both can be iterated on their own.

- **Fuel farm (OT / POL)** — a segmented Modbus-TCP industrial control system:
  OpenPLC controller, a field-instrument simulator, a FUXA HMI, an
  InfluxDB/Grafana historian and a PostgreSQL audit DB. `soc-sensor-ot` mirrors
  all three OT segments into the grid. Iterate with
  `ansible-playbook fuel_farm_playbook.yml`; verify with `./verify_fuel_farm.sh`.
- **Air traffic control** — an ADS-B feed driving `readsb` → a `tar1090` scope
  and a Flight Data Processor on `atc-radar`, a METAR/TAF/ATIS weather feed, and
  a controller-working-position kiosk. Iterate with
  `ansible-playbook atc_playbook.yml`; verify with `./verify_atc.sh`.

## Documentation

| File | For |
|---|---|
| `BUILD_SPEC.md` | working on this repo — layout, conventions, pitfalls, recipes |
| `docs/RANGE_GUIDE.md` | operators and instructors |
| `docs/RANGE_OVERVIEW.md` | the range at a glance |
| `docs/engineering/` | the full engineering pack (also bundled as the PDFs in `docs/`) |
| `UPSTREAM_FIXES.md` | customer-repo bugs and the overlay workarounds, with the reasoning |
| `PROJECT_LOG.md` | chronological build history |

Read `UPSTREAM_FIXES.md` before changing network, AD or Security Onion
plumbing. Most surprises are already diagnosed there.

## Secrets

Credentials live in `group_vars/all/vault.yml`, encrypted with ansible-vault.
`ansible.cfg` points `vault_password_file` at `/home/simspace/.vault_pass` on
the controller; that file does not persist across range spin-ups, and
`deploy.sh` fails closed with the recreate command when it is missing.
