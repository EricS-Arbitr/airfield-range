# 08 — Operations and Troubleshooting

## Credentials

The Security Onion web/join secrets and the OT (OpenPLC / FUXA / InfluxDB /
fuel-DB) and Malcolm secrets live in `group_vars/all/vault.yml` (ansible-vault).
The OS and device **connection** accounts and the domain-admin account live in
the per-group `group_vars`. The values below are the current range **defaults** —
rotate them before any deployment that is not a lab.

The two variants are built on **different platform image generations**, which is
the one credential difference that bites: the monitoring-only variant runs the
1.1.x images (Windows local admin `simspace`), the Splunk-stacked variant the
1.2.x images (Windows local admin `xadmin`). Using the wrong account for the
image generation does not fail fast — it retries to timeout and looks like an
unbooted host.

| System | Access | Username | Password |
|---|---|---|---|
| Security Onion SOC | `https://172.31.7.15` | `admin@blackstone.mil` | (vault) |
| SO node join secret | — | — | (vault) |
| Domain admin (both domains) | `blackstone.mil` / `fops.blackstone.mil` | `simspace` *(mon-only)* · `xadmin` *(stacked)* | `Simspace1!Simspace1!` *(mon-only)* · `ConfigingInTheNameOf1!` *(stacked)* |
| Domain users | both domains | seeded identities | (the domain-admin password) |
| Windows hosts | RDP / WinRM | `simspace` *(1.1.x)* · `xadmin` *(1.2.x)* | `Simspace1!Simspace1!` · `ConfigingInTheNameOf1!` |
| Linux hosts | SSH | `simspace` | `simspace1` |
| Ubuntu Desktop 25 *(stacked)* | SSH / console | `xadmin` | `PocketFullofShellCod3!` |
| pfSense firewalls | SSH / web | `admin` | `simspace1` |
| VyOS routers | SSH | `vyos` | `Simspace1!` |
| OpenPLC / FUXA / InfluxDB / fuel audit DB | OT | role accounts | (vault; default `Simspace1!…`) |
| Splunk *(stacked)* | web / mgmt | `admin` | `simspace1` |
| Splunk cluster secret *(stacked)* | — | — | `Simspace1!ClusterSecret` |
| Malcolm UI *(stacked)* | `https://172.31.7.30` | `xadmin` | (vault) |

### Rotation

```bash
ansible-vault edit group_vars/all/vault.yml
```

Rotate the vaulted secrets there. The connection and domain-admin accounts are
plaintext in the per-group `group_vars`; change them there, and remember the
Windows `ansible_become_user` must track `ansible_user` (runas has no separate
become password, so a mismatch fails escalation on every Windows task). Tasks
that embed a credential in a shell command carry `no_log` — Ansible echoes `cmd`
on failure and `deploy.sh` tees that to `/var/log/playbook_run.log`.

## Known behaviours (look like faults, are not)

- **SOC health-check failures fetching detection content.** SOC tries GitHub
  and the ET rules server every few minutes for rule updates, AI summaries and
  playbooks. The range has no egress and runs in airgap mode, so these fail
  continuously. Detection on ingested data is unaffected.
- **Gateway ARP repair messages during deploy.** The `init` role catches and
  corrects the platform-level gateway-ARP fault (an impostor MAC answers ARP and
  ICMP for the gateway on some segments, so the gateway pings while nothing
  routes off-subnet) before it breaks routing. Seeing the repair is good.
- **Emulated-user noise.** Continuous browsing, file and process activity across
  `[aue]` is intentional. Detections that only work on a quiet network are not
  detections.
- **A first-attempt host failure.** Usually ordering, not fault — the host
  depends on state another reaches asynchronously. Attempt 3 is the
  confirmation (see the build & deploy chapter).
- **The L3.5 boundary permits everything today.** `bs-ops-fw` carries lab
  "allow any" rules; blocking ACLs are deferred until every subsystem works. A
  cross-boundary reachability test that *succeeds* is expected for now.

## Common pitfalls

1. **VyOS image bakes self-loop default routes.** FRR refuses a default whose
   next-hop resolves to a local interface, so `show ip route` has no
   `S>* 0.0.0.0/0` even though `host_vars` declares one. Fix with
   `extra_static_routes_remove` per host.
2. **`next_hop` typo: own /30 IP instead of the peer's.** The inline comment
   naming the device on the other end of each `next_hop` exists because of
   exactly this.
3. **Windows DDNS registers both adapters** → `ping <host>` resolves to a
   management IP half the time. The baseline strip play fixes it;
   `Resolve-DnsName -DnsOnly` is the reliable check.
4. **pfSense `/cf/conf/config.xml` permission denied as non-admin.**
   `ansible_user: admin` is mandatory on pfSense — the other pre-provisioned
   user can't copy onto the root-owned config file.
5. **pfSense FRR runtime dir missing on boot** (`/var/run/frr/`); the
   `pfsense_firewall` role pre-creates it before `service frr onestart`.
6. **`gretap` vs `gre`.** Zeek silent, Suricata alerting = layer-3 tunnel. Both
   ends `gretap`.
7. **pfSense NIC ordering.** Management is on the **first** NIC (`vmx0`); a
   `host_vars` referencing `vmx0` as a data-plane interface is stale.
8. **The `dns` role sets no forwarders** — non-Blackstone names don't resolve
   until the baseline forwarders play runs; a child-DC `DomainDnsZones` partition
   may lag on a cold build, which the `dns` play gates on.
9. **FUXA ships without the Modbus driver.** `frangoteam/fuxa` has no
   `modbus-serial` package, so the device never connects until the `fuel_hmi`
   role installs it into the container — from the Nexus npm group, through the
   mgmt proxy, with `--strict-ssl=false` for the internal-CA cert.

The authoritative, dated list of overlay workarounds lives in the range's
upstream-fixes log — read it before changing network, AD or SO plumbing; half
the time the problem is already diagnosed there.

## Re-deploying and resetting

```bash
# Re-run one phase during development
ansible-playbook playbooks/40-manager.yml

# Re-run a subsystem
ansible-playbook fuel_farm_playbook.yml
ansible-playbook atc_playbook.yml

# Targeted recovery (e.g. a single DC + the Fleet gate on the manager)
BOOT_DELAY=0 ansible-playbook site.yml --limit bs-dc01,soc-so-manager

# Full rebuild on an already-up range
BOOT_DELAY=0 ./deploy.sh
```

A `--tags reset` scope (data-only vs also-config) is a design knob — confirm
intent before wiring destructive resets.

## Re-pinning the blueprint to a new tarball

The deploy chain is driven by the blueprint, not git. After building and pushing
a new tarball:

1. Build, commit and push the tarball (see the build & deploy chapter); note the
   **full** commit SHA.
2. Edit the platform blueprint → the `download-range-vars-script` `content:` →
   replace the SHA in the `…/<full-sha>/<tarball>` raw URL (`airfield_seconion.tgz` on the
   monitoring-only variant, `airfield_full_stack.tgz` on the stacked variant).
3. Confirm the blueprint references the SHA you pushed.
4. A fresh range fetches that exact tarball via `wget … -e https_proxy=
   10.255.240.1:3128` (curl does **not** work through this proxy on the
   controller; wget does).

Blueprints are not bundled in the tarball, so this change needs no rebuild — but
it only takes effect on a **fresh** range built from the edited blueprint, not
on a running one (re-pull the tarball by hand to update a live controller).

## Operating gotchas worth internalizing

- **Nexus is reachable only via the mgmt proxy, never directly**, and presents
  an internal-CA cert. Any new fetcher you add must route through
  `10.255.240.1:3128` and trust/skip-verify `nexus.dev.ng.simspace.lan`.
- **The vault ships inside the tarball** with its password file — the whole
  tarball is the trust boundary, so a fresh controller needs no out-of-band
  vault step, but anyone with the tarball has the vault.
- **Verify the committed tarball, not the working copy** (`git show HEAD:<tarball>`)
  — a stale or partially-staged tarball has silently shipped before.
- **`docs/` is not deployed** — editing it never changes a running range.
- **An overlay role replaces the base role wholesale**, so a fix to a base role
  must be a full copy in the overlay, and a fix made in one variant does not
  propagate to the other — change both when the behaviour should match.
