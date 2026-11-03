# 05 — Build and Deploy

## The deploy chain, end to end

```
build_tarball.sh  ──▶  ab_mb.tgz (or ab_mbs.tgz, stacked)  ──▶  commit + push (pinned commit)
                                               │
blueprint download-range-vars-script: wget the tarball from the pinned raw URL
                                               │  (through mgmt proxy 10.255.240.1:3128)
                                   tar -xzf into /etc/ansible
                                               │
blueprint deploy-script:  ./deploy.sh | tee /var/log/playbook_run.log
                                               │
                                    site.yml, up to 3 attempts
```

The controller **deploys from the tarball, not a git pull**. The repo is just
where the tarball is built and versioned; the running range only ever sees the
tarball (`ab_mb.tgz` on the monitoring-only variant, `ab_mbs.tgz` on the
stacked variant). Fetch files on the controller with `wget` through the mgmt
proxy — `curl` does not work through it.

## Building the tarball

```bash
./build_tarball.sh          # writes the tarball; refuses if a gate fails
```

`build_tarball.sh` auto-discovers roles from **every** play under `playbooks/`
plus `site.yml` and the top-level `fuel_farm_playbook.yml` / `atc_playbook.yml`,
understanding `roles:`, `import_role` and `include_role` (so a role referenced
only via `import_role` + `tasks_from`, like `vyos_mirror`, is still bundled).
Each role is taken from SimSpace's core Ansible library first, then overridden
by the local overlay roles. Bundled artifacts: roles, playbooks, `site.yml`,
`hosts`, `group_vars`, `host_vars`, the verifier scripts, `deploy.sh`,
`requirements.yml`, the vendored `collections/`, the ETOPEN `rules/`, **and**
the encrypted vault plus its password file (see "Secrets / vault" below).
**Not** bundled: `docs/`.

### Build-time gates (the archive is refused if any fails)

| Gate | Catches |
|---|---|
| `verify_shell_args.py` | An apostrophe in a PowerShell/shell comment inside a free-form module arg — Ansible's `split_args()` counts quotes and the play fails to **load** (valid YAML, so a separate check). |
| `verify_so_inventory.py` | An SO host in `[so_all]` but not `[linux]` (roles to run, no way to log in — it once cost three deploy attempts). |
| `verify_dup_keys.py` | Duplicate YAML mapping keys (silently drop data). |
| `verify_handlers.py` | `notify:` targets that resolve to no handler. |
| `verify_task_keywords.py` | Task keywords misindented as module arguments. |
| `verify_defend_filters.py` | Malformed Elastic Defend filters. |
| `verify_vars.py` | Jinja references with no definition. Advisory, not a hard gate. Run it against the **stage**, not the repo root — the stage includes base roles copied from SimSpace's core Ansible library, so some vars appear only there. |

## Publishing

Rebuild the tarball **and** commit it with the code change, every time, because
the controller deploys the tarball. After committing, **verify the committed
artifact, not the working copy**:

```bash
git show HEAD:ab_mb.tgz | tar tz | head                       # it is really in the commit
git show HEAD:ab_mb.tgz | tar xzO ./hosts | grep -c <thing>   # spot-check content
```

Hand over a **commit-pinned** raw URL (not a branch URL — branch URLs are
CDN-cached), then point the blueprint's `download-range-vars-script` at that
SHA (see the operations & troubleshooting chapter).

## `deploy.sh` — the 3-attempt retry model

`deploy.sh` runs `site.yml` up to three times, and **attempt 2 is not a success
condition**:

1. full sweep
2. retry-file scope — a **repair pass** over the hosts that failed in attempt 1
3. full sweep — **this is what actually confirms the range**

The retry file lists hosts that *failed*. Repairing them does not re-run the
plays whose targets were dropped when they failed, so a clean repair pass is not
a deployed range. Attempt 3 is the confirmation. A first-attempt failure is
often ordering (a host depends on state another reaches asynchronously), not a
fault.

- `FORKS` is **derived**, not chosen: the largest single play target plus a
  small margin, so the widest play runs in one batch. Recount when hosts are
  added.
- `BOOT_DELAY` (default 180s; `BOOT_DELAY=0` to skip) lets a freshly
  provisioned range finish booting — it covers hosts that do not exist yet,
  which `wait_for_connection` cannot. Use `BOOT_DELAY=0 ./deploy.sh` on an
  already-up range.

```bash
sudo ./deploy.sh                 # full build, 3 attempts
BOOT_DELAY=0 ./deploy.sh         # already-up range
ansible-playbook playbooks/40-manager.yml            # one phase, during dev
ansible-playbook site.yml --limit bs-dc01,soc-so-manager  # targeted re-run
```

## Phase order (`site.yml`)

`site.yml` holds the range **baseline** as inline plays (init + common across
all hosts, both AD domains + the child trust, DNS, DHCP, the firewalls and
routers, the enterprise and flight-ops tiers) and then imports the monitoring
and subsystem phases in order:

```
10-mirror           GRE/tc mirror + SO source staging — imported FIRST (fails
                    fast; kicks the 7 GB registry build off in the background)
  …                 the inline baseline plays (AD, DNS, firewalls, routers,
                    enterprise, flight-ops; on the stacked variant also the
                    whole Splunk cluster)
05-time             Windows clock, DC-first
20-vyos             GRE tunnels + tc mirror rules on the four mirror routers
30-prereqs          so_base on all SO nodes
40-manager          SO manager install — MUST complete before 50
50-nodes            search + sensor grid-join
60-verify           grid verification
70-analyst          analyst-workstation enablement
75-endpoint         Sysmon, then Elastic Agent enrolment
80-fleet-integrations  move log-source hosts onto dedicated Fleet policies
90-malcolm          Malcolm OT appliance (stacked variant only)
fuel_farm_playbook  the fuel-farm OT chain
atc_playbook        the ATC subsystem
```

`10-mirror` is hoisted ahead of the baseline because the mirror/registry is a
hard prerequisite for 30/40/50 — discovering it broken after a multi-hour
baseline wastes the run — and because it overlaps the 7 GB image build with the
baseline. Safe to hoist because the controller is in `[ansible_controller]`
only, never `[linux]`.

`75-endpoint` runs after `60-verify` because enrolment needs a manager that is
actually green — Fleet has to be answering before an agent can register — and
`80-fleet-integrations` **must** follow it: enrolment puts every endpoint on
`endpoints-initial`, then that phase reassigns the log-source hosts to dedicated
policies. On the stacked variant the Splunk universal forwarder is laid down in
the inline baseline and the Elastic Agent in `75-endpoint`; the two read the
same sources independently and neither can starve the other.

## Secrets / vault

Both variants carry an **ansible-vault**–encrypted `group_vars/all/vault.yml`.
`deploy.sh` enforces it with a **fail-closed guard**: it refuses to run unless
`group_vars/all/vault.yml` exists and is `$ANSIBLE_VAULT`-encrypted, and unless
the password file is present. On this range the vault password ships **inside
the tarball** alongside the encrypted vault (the whole tarball is the trust
boundary), so a fresh controller needs no out-of-band step. The vault holds the
Security Onion web/join secrets and the OT (OpenPLC/FUXA/InfluxDB/fuel-DB) and —
on the stacked variant — Malcolm secrets. The OS/device connection accounts and
the domain-admin account live in the per-group `group_vars` as described in the
operations chapter. Tasks that embed a credential in a shell command carry
`no_log`, because Ansible echoes `cmd` on failure and `deploy.sh` tees that to
`/var/log/playbook_run.log`.

## Artifact sourcing (Nexus)

The range has no general egress. Everything is fetched from the in-platform
Nexus (`nexus.dev.ng.simspace.lan`) through the management proxy
(`10.255.240.1:3128`); Nexus presents an internal-CA cert, so each fetcher is
told to trust or skip-verify that host:

| Artifact | Nexus repo | Fetcher / note |
|---|---|---|
| SO source snapshot + airgap content | `ng_raw` (`installers/so/…`) | `get_url`, `validate_certs: false` |
| SO + Malcolm container images | `nexus-docker` | `skopeo --src-tls-verify=false`; dockerd `insecure-registries` |
| Malcolm 26.08.0 source | `ng_raw` (`installers/malcolm/…`) | `get_url` (stacked) |
| Ansible Galaxy collections | `ng_raw/galaxy` | `ansible-galaxy … --ignore-certs` |
| Fuel-farm Python packages | `ng_pypi` | `pip --index-url … --trusted-host …` |
| HMI npm closure (`modbus-serial`) | `npm` group | `npm --registry … --strict-ssl=false`, via the mgmt proxy |
| Splunk packages | `ng_raw` | staged `.deb` (stacked) |
| **apt packages + `docker-ce`** | **Ubuntu archive / `download.docker.com`** | **NOT Nexus.** `ng-apt` serves several Ubuntu releases from one index; no pin can safely prefer the right one, so apt stays on upstream through the proxy. |

Before cutting a blueprint over to a Nexus-sourced tarball, confirm Nexus is
populated and reachable through the proxy for that range — otherwise the deploy
fails early on the SO source or a Galaxy fetch.
