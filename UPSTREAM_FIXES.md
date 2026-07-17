# Upstream Fixes & Enhancements — airfield-range

Running log of issues, gaps, and suggested improvements discovered while deploying `airfield-range`. Candidates for PRs or discussion with the `range-development-ansible` maintainers, or for re-copies from PowerPlant when an existing PowerPlant fix needs to be brought across.

Per the [role-sourcing memory](../../.claude/projects/-Users-eric-starace-vCity/memory/project_airfield_role_sourcing.md): when an upstream fix is needed in airfield-range too, re-copy it explicitly **and log it here the same turn** (per the [UPSTREAM_FIXES feedback memory](../../.claude/projects/-Users-eric-starace-vCity/memory/feedback_upstream_fixes_log.md)).

Severity key:
- **bug** — role malfunctions or produces incorrect results
- **gap** — missing functionality that ranges have to work around
- **enhancement** — works but could be more robust or ergonomic
- **platform** — SimSpace platform-side issue, not Ansible

Format: `## YYYY-MM-DD · <severity> · <target path / heading>` followed by Symptom → Detection (if non-obvious) → Fix (upstream) → Workaround (overlay).

**Historical domain names:** entries dated before 2026-07-02 reference `vcab.lan` / `flightops.lan` and OU groups `pdc_vcab` / `pdc_flightops` / `members_vcab` / `members_flightops` — these were **renamed to `blackstone.mil` / `fops.blackstone.mil` / `pdc_blackstone` / `pdc_fops` / `members_blackstone` / `members_fops` in the Blackstone rebrand on 2026-07-02** (see `[[project_blackstone_rebrand]]` memory). The technical content of every pre-rebrand entry still applies; only the domain/group labels changed. Don't edit those entries retroactively — the labels are preserved as historical fact.

---

## 2026-07-16 · gap · roles/fuel_plc/tasks/main.yml — OpenPLC container comes up with no program loaded (Modbus :502 dead)

**Symptom.** After `fuel_plc` deploys, verify shows `ff-plc-1 OpenPLC container running` green and `ff-plc-1 OpenPLC web :8080` green, but `Modbus :502` refuses TCP indefinitely. The container is happy, the web UI works, and `/opt/openplc/programs/fuel_farm.st` is present in the bind mount — but nothing binds :502 because the OpenPLC runtime hasn't been told what program to run.

**Why the manual step was there.** `fdamador/openplc` (v3 fork) needs a web-UI upload + `/start_plc` to activate a program — merely dropping the .st file into a bind mount doesn't register it in `openplc.db`. The role's original stopgap was a `debug` task printing "upload via web UI on first deploy", which broke the range's promise of full idempotent redeploys.

**Fix (overlay).** New Python helper `roles/fuel_plc/files/openplc_bootstrap.py` runs on the target after container start:

1. GET /dashboard → if runtime is Running with our program, exit 0 (idempotent).
2. Otherwise: `/stop_plc` → multipart POST `/upload-program` (.st file) → scrape `prog_file` hidden input from response → POST `/upload-program-action` (metadata) → GET `/compile-program?file=…` (drain the streaming matiec log) → GET `/start_plc` → poll `/dashboard` until Running (60s timeout).

Also adds `python3-requests` to the apt install for the target, and creates `/opt/openplc/bin/` for the script. The main.yml task's `changed_when` fires only when the bootstrap actually mutated state (script prints `no change` on idempotent runs).

**Verify coverage.** Old `ff-plc-1 Modbus :502 accepting TCP (needs program uploaded via web UI)` label reworded (no longer needs a hint), and a new stricter probe added: fuel-farm-sim's pymodbus reads HR 0 (`LR1_PRESET_GAL`) from ff-plc-1 → if that succeeds, the program is loaded, the addresses are mapped, and the SCADA-bus is truly usable.

**Follow-up (not blocking).** Container's `openplc.db` isn't persisted — the current bind mounts (`/workdir/etc`, `/workdir/programs`) don't cover the DB path (`/workdir/webserver/openplc.db`). Every container restart re-runs the bootstrap (idempotent, cheap), which is fine for CI/reset semantics but means the OpenPLC admin creds env is re-applied every restart. If persistence is added later, this role needs a real `/change_password` rotation call.

**Amendment (2026-07-17, same day).** First run of the bootstrap script from an actual deploy revealed that `fdamador/openplc` does NOT honor `OPENPLC_ADMIN_USER` / `OPENPLC_ADMIN_PASSWORD` env vars — the container came up with the hardcoded `openplc/openplc` default and rejected the vault password. Bootstrap now tries the vault-configured creds first, falls back to `openplc/openplc` on failure, and stderr-WARNs when the default succeeded so the role can flag "rotation still owed". Real `/change_password` rotation is deferred because the endpoint URL varies across forks and would re-invalidate the session mid-flow.

---

## 2026-07-16 · bug · roles/fuel_sim/templates/fuelsim.service.j2 — service can't bind Modbus :502 as unprivileged user

**Symptom.** After fuelsim.service is enabled and running, `verify_fuel_farm.sh` shows fuel-farm-sim :502 `MODBUS_REFUSED` and `ss -tlnp | grep :502` returns nothing. `journalctl -u fuelsim` reveals:

```
pymodbus.logging Failed to start server [Errno 13] error while attempting to bind on address ('172.16.46.17', 502): permission denied
```

The pymodbus server-run loop catches the OSError, logs the warning, and *continues* — so the process stays "active (running)" without ever listening. Modbus is silently down.

**Root cause.** The unit runs as `User=fuelsim` (an unprivileged system account, by design — the daemon shouldn't need root). Port 502 is <1024 → privileged on Linux → requires `CAP_NET_BIND_SERVICE` at bind time. The systemd unit granted no capabilities to the exec, so the bind failed.

**Fix (overlay).** Add to the `[Service]` block:

```
AmbientCapabilities=CAP_NET_BIND_SERVICE
CapabilityBoundingSet=CAP_NET_BIND_SERVICE
```

Preferred over lowering `net.ipv4.ip_unprivileged_port_start` (system-wide, affects all users) or running the daemon as root. `AmbientCapabilities` requires systemd ≥ 229 (jammy ships 249, noble 255) — always available on our baseline. Also preferred over `setcap CAP_NET_BIND_SERVICE=+ep` on the venv python binary, which would leak the capability to *every* python invocation on the host (venv builds, ad-hoc `python3 -m` runs, etc.).

**Secondary observation for the upstream `pymodbus` project.** A bind failure inside `StartAsyncTcpServer` shouldn't downgrade to `WARNING` and let the coroutine return "Server listening." — this masks a fatal misconfiguration behind a healthy-looking service. Worth an upstream issue if we hit it again.

---

## 2026-07-16 · bug · roles/fuel_db/tasks/main.yml — fuel_rw got table INSERT but no sequence USAGE, breaking every INSERT with a SERIAL PK

**Symptom.** fuelsim's `state_machine.db_flusher` logs one traceback per event batch:

```
psycopg2.errors.InsufficientPrivilege: permission denied for sequence truck_queue_queue_id_seq
psycopg2.errors.InsufficientPrivilege: permission denied for sequence events_event_id_seq
```

Every INSERT into a table with a SERIAL/bigserial PK fails because Postgres invokes `nextval('<table>_<col>_seq')` under the caller's role, and treats sequence privileges independently from table privileges. Table INSERT alone is not enough.

**Root cause.** The role had one `postgresql_privs` task granting `SELECT,INSERT,UPDATE,DELETE` to `fuel_rw` on `type: table`, but never granted anything on `type: sequence`. All the affected tables in `schema.sql.j2` (`events`, `truck_queue`, `load_txn`, `delivery_txn`, `tank_level_snap`) use SERIAL / bigserial PKs → each has an auto-created sequence → each INSERT hits the missing privilege.

**Fix (overlay).** Add a second `postgresql_privs` task with `type: sequence`, `objs: ALL_IN_SCHEMA`:
- `fuel_rw` → `USAGE,SELECT,UPDATE` (nextval + currval + `setval` — the last is defensively included so tools like `pg_dump --data-only` restore reads correctly).
- `fuel_ro` → `SELECT` (currval only, doesn't advance the sequence — safe for read-only dashboards).

**Why the acceptance schema tests missed it.** The verify script checks table *existence* (`\dt` in psql), not INSERT permission under the app role — so the tables all showed up green even though the app couldn't write to them. Post-fix, the verify script should be extended with a fuel_rw round-trip probe (INSERT ... RETURNING pk into a temp row, then DELETE) to catch this class of regression.

---

## 2026-07-14 · enhancement · site.yml — bootstrap fops.blackstone.mil\simspace from bs-dc01 before create_users

**Symptom.** On a fresh range deploy where fops-dc01 has just been promoted, `create_users` on the `pdc` group tries to open a WinRM connection to fops-dc01 as unqualified `simspace` — and gets `ntlm: the specified credentials were rejected by the server`. `simspace` doesn't exist yet as a `fops.blackstone.mil` domain user (create_users hasn't run there yet), and post-promotion the machine no longer falls back to local-SAM auth for that name. Workaround previously required a temporary `blackstone.mil\Administrator` override in `host_vars/fops-dc01.yml` for the immediate re-run, then removal afterward.

**Why bs-dc01 doesn't hit this.** Empirically, bs-dc01 keeps accepting unqualified `simspace` post-Install-ADDSForest — its local-SAM `simspace` account survives promotion and NTLM falls back to it. Install-ADDSDomain on the child (fops-dc01) leaves the machine in a state where the same fallback doesn't happen; the exact mechanism (SAM cleanup? primary-domain resolution order? Windows Server 2022 hardening for child DCs?) hasn't been isolated, but the behavioral difference is reproducible on every fresh deploy.

**Fix (overlay).** New play in `site.yml` inserted between `Post-dcpromo diagnostic snapshot` and `Create Users`:

- Runs on `pdc_blackstone` (bs-dc01), which accepts unqualified `simspace` fine.
- Uses PowerShell `Get-ADUser -Server fops.blackstone.mil -Credential blackstone.mil\Administrator` and `New-ADUser`/`Add-ADGroupMember` with the same explicit credential to reach fops-dc01 via AD RPC/LDAP (bypasses fops-dc01's broken WinRM).
- Creates `simspace` in fops.blackstone.mil with `PasswordNeverExpires` + adds to Domain Admins.
- Idempotent: if the user exists (re-runs, or partial prior deploy), it's a no-op for the create + a harmless re-add for the group membership.

After this play, `create_users` on fops-dc01 authenticates via unqualified `simspace` normally — because `fops.blackstone.mil\simspace` now exists as a DA. No more host_vars override dance on future fresh deploys.

---

## 2026-07-14 · bug · roles/pfsense_firewall/tasks/main.yml — remote-syslog daemon never reloaded when config already matched

**Symptom.** `verify_deployment.sh` failed on `soc-syslog receiving from bs-edge-fw` and `soc-syslog receiving from bs-ops-fw` (`STALE_OR_MISSING`). Both pfSense boxes had the correct block in `/cf/conf/config.xml`:
```
<syslog>
  <remoteserver>172.31.7.13</remoteserver>
  <enable></enable>
  <logall></logall>
  <ipproto>ipv4</ipproto>
</syslog>
```
but tcpdump on soc-syslog showed zero packets from either pfSense's interface IPs. On inspection: bs-edge-fw's `/etc/syslog.conf` had **no remote-forward stanza** (config.xml never got regenerated into the runtime file); bs-ops-fw's `syslogd` process **wasn't running at all**.

**Root cause.** The task `Configure remote syslog forwarding` only called `system_syslogd_start()` when it had staged a config diff (`$changed = true`). On a re-deploy where config.xml already had the right values but runtime state had drifted (either the file regen missed or the daemon crashed and never got restarted), no reload was triggered.

**Fix (overlay).** Split into two tasks: the write-config task stays conditional (idempotent), and a new **always-runs** follow-up unconditionally calls `system_syslogd_start(true)` — the `true` flag forces config regeneration + daemon restart even when PHP thinks nothing changed. Belt-and-suspenders fallback: if `pgrep syslogd` still returns nothing after that, run `service syslogd onerestart`. This guarantees runtime = config.xml on every deploy.

---

## 2026-07-14 · bug · roles/create_users/tasks/main.yml — Get-ADDefaultDomainPasswordPolicy targets caller's domain, not local DC

**Symptom.** On fops-dc01 (child DC), `create_users` failed at:
```
Get-ADDefaultDomainPasswordPolicy : Unable to contact the server. This may be because this server does not exist, it is currently down, or it does not have the Active Directory Web Services running.
CategoryInfo: (BLACKSTONE:ADDefaultDomainPasswordPolicy) [ADServerDownException]
```
Note the target realm in the error: `BLACKSTONE` — the parent forest — not `fops.blackstone.mil` where the task was running.

**Root cause.** `Get-ADDefaultDomainPasswordPolicy` (and `Set-`) default to the **current user's** domain, not the local machine's. When Ansible authenticates to fops-dc01 with a parent-forest EA credential (e.g. via the temporary `blackstone.mil\Administrator` host_vars override needed post-promotion), both cmdlets try to reach the parent forest DC across the network via ADWS — which intermittently fails even when bs-dc01's ADWS is healthy. Knock-on effect: `simspace` was never created as a domain user in fops.blackstone.mil, which cascaded into `additional_dc` on fops-dc02 failing with `"You have not supplied user credentials that belong to Domain Admins/Enterprise Admins"` (the credential it was passing, `simspace@fops.blackstone.mil`, didn't exist).

**Fix (overlay).** Pin both cmdlets to `-Server $env:COMPUTERNAME`:
```powershell
(Get-ADDefaultDomainPasswordPolicy -Server $env:COMPUTERNAME).ComplexityEnabled
```
```powershell
$dom = Get-ADDomain -Server $env:COMPUTERNAME
Set-ADDefaultDomainPasswordPolicy -Identity $dom.DistinguishedName -Server $env:COMPUTERNAME -ComplexityEnabled $false
```
Now the query always talks to the local DC regardless of caller identity.

---

## 2026-07-14 · bug · roles/dcpromo_child_heal — WinRM/NTLM credential format + reboot mechanics (three sub-fixes)

**Symptom.** The heal role from 2026-07-13 was structurally sound but its first `win_ping` never succeeded, so `end_host` fired and no healing occurred. Symptoms across three iterations:

1. First iteration used `.\simspace` for local auth — got `ntlm: credentials rejected` on every attempt.
2. Second iteration switched to `MACHINE\simspace` — got a Jinja `recursive loop detected in template string` from `heal_local_password: "{{ ansible_password | default(...) }}"` (since the task also sets `ansible_password: "{{ heal_local_password }}"`).
3. Third iteration used `Start-Job { Restart-Computer }` to fire the healing reboot async — but the job died with the WinRM PowerShell process, so the reboot never happened; registry values were cleared but not applied.

**Root causes.**

1. `.\user` is a Windows console shorthand, **not an NTLM auth format**. pywinrm doesn't accept it. Real NTLM requires `MACHINENAME\user` or `user@REALM`.
2. Ansible template resolution: setting a role default to `{{ ansible_password | default(...) }}` and then having a task set `ansible_password: {{ default_var }}` creates a resolution cycle → recursion depth error.
3. `Start-Job` runs the scriptblock as a child process of the current PowerShell session. When WinRM closes the session, PowerShell dies, and any Start-Job children die with it before `Start-Sleep` finishes.

**Fixes.**

1. Use `{{ inventory_hostname | upper }}\simspace` for MACHINE\user format; fall back to `MACHINE\Administrator` (dcpromo's "local admin fix" task sets its password to `domain_admin_password`).
2. Hard-code the literal password in role defaults; never reference `ansible_password` from a default consumed by a task that then rewrites `ansible_password`.
3. Replace `Start-Job` reboot with `shutdown.exe /r /t 5 /f` — launches a detached system process that survives WinRM session termination.

Also added: try DEFAULT (unqualified `simspace`) creds **first**. On a CLEAN baseline snapshot the machine is in WORKGROUP mode → default creds work → `meta: end_host` with no healing needed. Only fall through to MACHINE\ variants if the default is rejected (true half-joined state).

---

## 2026-07-13 · enhancement · roles/dcpromo_child_heal — auto-heal half-join residue before init

**Symptom.** Third consecutive fresh-range deploy hit the same failure pattern: `Install-ADDSDomain` on fops-dc01 succeeds far enough to (a) register the `FOPS` cross-reference in bs-dc01's Configuration NC and (b) set fops-dc01's Primary DNS Suffix + Domain hint to `fops.blackstone.mil` — then dies before AD services come up. Every subsequent deploy times out at `init` for 30 min × 3 attempts (NTLM rejects unqualified `simspace` because the machine's now-broken domain hint prefixes it as `fops.blackstone.mil\simspace`). Manual recovery loop (metadata cleanup via Administrator/EA + SimSpace snapshot revert) has taken 4 rounds so far.

**Root cause.** Install-ADDSDomain is not transactional on this platform — partial failures leave residue on both the parent forest (CrossRef under CN=Partitions, orphan Server + NTDS Settings under CN=Sites) and the local machine (Domain / NV Domain registry hints under Tcpip\\Parameters). Retrying Install-ADDSDomain against this state fails at pre-flight because the CrossRef already exists. And init can't even reach the host to trigger the retry.

**Fix (overlay).** New role `roles/dcpromo_child_heal` runs at the very top of site.yml (before `init`), targets `pdc_fops`, uses local `.\simspace` credentials (unaffected by the broken domain hint). Idempotent flow:

1. Ping via `.\simspace`. If unreachable, `meta: end_host` (SimSpace-platform issue, not ours to solve).
2. Detect state: `HALF_JOINED` (PartOfDomain=True + child domain + ADWS/NTDS Stopped), `ALREADY_DC` (running), or `CLEAN`. Only `HALF_JOINED` triggers healing.
3. Delegate CrossRef + orphan-DC-object removal to `pdc_blackstone` using Administrator (auto-EA post-forest-root promotion) — exactly the manual `Remove-ADObject` sequence we've been running by hand.
4. Clear `Tcpip\\Parameters!Domain`, `Tcpip\\Parameters!NV Domain`, and `NTDS\\Parameters!DcPromoInProgress` on the half-joined host.
5. Reboot (`win_reboot`, 900 s timeout).
6. Verify default (unqualified `simspace`) credentials work post-reboot; if yes, healed → init proceeds normally.

Combined with the 2026-07-10 DNS-bootstrap fix, this makes the child-domain path resilient to Install-ADDSDomain partial failures: any prior failure is auto-cleaned at the start of the next deploy rather than requiring manual metadata-cleanup + snapshot-revert cycles.

**Why the pre-init position matters.** init's `wait_for_connection` uses default (unqualified) credentials with a 30-min timeout and `any_errors_fatal: true`. If the healing play ran later, it could never fire because init would abort the whole deploy first. Placing it upstream, with `any_errors_fatal: false`, lets the heal proceed and end_host cleanly whether or not residue is present.

---

## 2026-07-10 · bug · roles/dcpromo/tasks/main.yml — child-domain DNS bootstrap picks wrong interface (or none)

**Symptom.** On child-domain promotion, Install-ADDSDomain fails with:
```
PROMOTION_EXCEPTION: Verification of user credential permissions failed.
An Active Directory domain controller for the domain "blackstone.mil"
could not be contacted. Ensure that you supplied the correct DNS domain name.
```
Preceding task ("Point primary DNS at parent PDC before Install-ADDSDomain") logs `NO_IFACE_FOUND` on tag-limited retries (`--tags dcpromo_child` skips `common`, so no NIC yet has DNS populated) or, on full deploys, silently picks the mgmt NIC (Ethernet0 gets DNS `172.31.2.7` written to it — but mgmt has no default route, so DNS lookups for `_ldap._tcp.blackstone.mil` still fail).

**Root cause.** The task selected `Get-DnsClientServerAddress ... | Where { ServerAddresses.Count -gt 0 } | Select -First 1`. Two failure modes:
1. Tag-limited retry / snapshot-reverted baseline → no interface has DNS → filter returns nothing → `NO_IFACE_FOUND` → downstream Install-ADDSDomain gets a misleading "could not be contacted" error.
2. Full deploy where `common` set DNS on both NICs → the mgmt NIC (Ethernet0, lower InterfaceIndex) wins → DNS gets written to an interface with no default route → SRV resolution still fails.

**Fix (overlay).** Rewrote the interface-picker with a three-tier strategy: (1) prefer the interface carrying the IPv4 default route (== prod NIC per airfield contract — mgmt NIC has empty gateway), (2) fall back to "first with DNS set" (post-`common` steady state), (3) last-resort to any non-loopback/non-tunnel IPv4 interface. Also added `failed_when: "'NO_IFACE_FOUND' in ..."` so a genuine no-interface state fails loudly at the DNS-bootstrap task instead of masking behind Install-ADDSDomain's downstream error. Rationale for using default route as primary signal: it's the invariant enforced by the airfield host_vars contract (§8 of CLAUDE.md) — mgmt NICs always have `gateway: ""`, so the default route is always on the prod NIC.

---

## 2026-07-08 · bug · roles/global_dns/templates/simspace_includes.conf.j2 — corpora `redirect` zone collision aborts unbound

**Symptom.** On a fresh range, corp hosts can't resolve any external name — bs-dc01's forwarders point at 8.8.8.8/8.8.4.4/1.1.1.1 (is-inet lo aliases) but every query times out. Digging into is-inet: unbound isn't running at all. Attempting to start it manually reveals the reason:
```
error: local-data in redirect zone must reside at top of zone,
       not at www.github.com. A 70.39.65.196
fatal error: Could not set up local zones
```

**Root cause.** is-inet's `/etc/unbound/corpus.d/*.conf` files ship hundreds of `local-zone: "<domain>." redirect` entries (Ukraine/Russia/US censorship-simulation corpora). Our `global_dns_records` in `group_vars/all.yml` add sub-domain `local-data` entries for some of the same domains (github.com, google.com, microsoft.com, etc. — plus airfield's aviation zones and blackstone.mil). Unbound refuses to accept sub-domain records in a `redirect` zone — the whole config load fails and the daemon exits.

**Fix (overlay).** Extended `roles/global_dns/templates/simspace_includes.conf.j2` to emit `local-zone: "<zone>." transparent` at the top of the file for every unique zone that appears in `global_dns_records`. `transparent` overrides the corpora's `redirect` type so our sub-domain records get honored. Trade-off: the corpora's redirect no longer applies to our overridden zones — fine for scenario purposes since our records are what we want anyway.

**Fix (upstream).** Same patch in `range-development-ansible/roles/global_dns/templates/simspace_includes.conf.j2`. Any range that adds records under a corpora-covered domain hits this bug.

---

## 2026-07-08 · bug · is-inet image — unbound doesn't auto-start; log file + PID file missing

**Symptom.** After range provisioning, corp DNS queries to 8.8.8.8 (is-inet lo alias) time out. Inside the is-inet container, `ss -lnu | grep :53` returns nothing — **unbound isn't running**. Postfix / Dovecot / httpd (for webmail) are all up. Manually running `docker exec -d is-inet /usr/sbin/unbound` fails with `Could not open logfile /var/log/unbound.log: Permission denied`.

**Root cause.** The RC-IS-INET container image's entrypoint launches the mail stack but not unbound. `/etc/unbound/unbound.conf` specifies `logfile: "/var/log/unbound.log"`, but that file doesn't exist inside the container and the `unbound` user can't create it. There's also a stale `/var/run/unbound.pid` on some baselines.

**Fix (overlay).** New `roles/is_inet_fix/` on airfield-range. Deploys three things on the is-inet host:
1. A shell script at `/usr/local/sbin/airfield-unbound-supervise.sh` that checks whether unbound is listening in the container; if not, creates the logfile with `unbound:unbound` ownership, clears any stale PID, and runs `docker exec -d is-inet /usr/sbin/unbound`.
2. A systemd oneshot service that runs the script at boot.
3. A systemd timer that re-runs the script every minute so any container restart re-launches unbound.

Idempotent + self-healing across container/host restarts.

**Fix (upstream / platform).** Either bake `/var/log/unbound.log` (owned by `unbound`) into the container image, or extend the image entrypoint to launch unbound alongside the mail stack. Also worth: the `range-development-ansible/roles/handlers/handlers/main.yml`'s `reload unbound` handler uses `ignore_errors: yes`, which masks this failure silently — either drop the ignore or explicitly probe for a running daemon first.

---

## 2026-07-08 · platform · RC-IS-INET image — eth1 provisioned as /32 with no default gateway

**Symptom.** is-inet's `eth1` (data-plane interface, at 200.200.200.2) comes up with a `/32` mask and no default gateway. `ip route` shows only the host's own /32; is-inet has no connected route to its own 200.200.200.0/24 LAN. Result: replies to any external client get `ENETUNREACH` and are silently dropped.

**Root cause.** RC-IS-INET image bug — same one PowerPlant documented on 2026-05-22. PowerPlant's UPSTREAM_FIXES noted a "planned" `is_inet_fix` role that was never built. Airfield hit the same wall today.

**Fix (overlay).** `roles/is_inet_fix/` drops a netplan supplement at `/etc/netplan/99-airfield-eth1.yaml` with the correct `/24` addressing + default gateway (`200.200.200.1` = bs-edge-rtr eth0). `netplan apply` on change. Persistent across reboots. Values driven from `host_vars/is-inet.yml` variables (`isinet_dataplane_ip`, `_prefix`, `_gateway`) so airfield's exact addressing isn't baked into the role.

**Fix (upstream / SimSpace).** Image cloud-init/netplan should honor the YAML-declared prefix and configure a default gateway pointing at the subnet's GATEWAY-roled neighbor. Same PowerPlant entry from 2026-05-22 applies here — the airfield `is_inet_fix` role should be back-ported to `range-development-ansible/roles/is_inet_fix/` (or PowerPlant/ss-pp-ab).

---

## 2026-07-08 · gap · corp -> is-inet: bs-edge-rtr missing return route to corp

**Symptom.** After is-inet is functional, corp DNS queries reach unbound and unbound replies — but corp hosts still don't get answers. tcpdump on is-inet's eth1 shows queries from `172.31.x.x` arriving and replies going back the way they came. But the replies never reach corp.

**Root cause.** `bs-edge-rtr`'s routing table has only its default route (via `200.200.200.2` = is-inet) plus its two connected /30 (`199.252.163.0/30` toward bs-edge-fw) and /24 (`200.200.200.0/24` toward is-inet). **No route to `172.31.0.0/16`**. eBGP with bs-edge-fw is up but shows `(Policy) (Policy)` for prefix counts — corp routes aren't being advertised due to some route-map/filter on bs-edge-fw's `pfsense_bgp` side. is-inet's reply → bs-edge-rtr → follows default back to is-inet → routing loop → TTL exhaust → drop.

**Fix (overlay).** Added an `extra_static_routes` entry to `host_vars/bs-edge-rtr.yml`:
```yaml
extra_static_routes:
  - route: "172.31.0.0/16"
    next_hop: "199.252.163.1"        # bs-edge-fw WAN
```
Consumed by the customer `vyos` role's "Additional VyOS static routes" overlay play. Combines with bs-edge-fw's outbound NAT (see next entry) so return traffic finds its way home even without eBGP advertising corp routes.

**Fix (upstream).** Either (a) fix the BGP route-map filtering on bs-edge-fw so corp routes actually get sent to bs-edge-rtr, or (b) leave this as a static-route belt-and-suspenders permanently. Static is arguably cleaner at this WAN edge — it works even if BGP goes sideways, and matches CLAUDE.md §3 (item 8) intent.

---

## 2026-07-08 · gap · host_vars/bs-edge-fw.yml — outbound NAT must be `automatic`, not `disabled`

**Symptom.** Even after is-inet is fixed and bs-edge-rtr has a return route, DNS from a random corp host is fragile — depends on bs-edge-rtr's route being present.

**Root cause.** The `pfsense_firewall` role defaults to `pfsense_disable_outbound_nat: true` (correct for INTERNAL transit firewalls like bs-ops-fw). But `bs-edge-fw` is the RANGE-EDGE firewall: corp <-> simulated internet. `172.31.0.0/16` is RFC1918 and not publicly routable, so NAT at the edge is realistic and expected. Without it, is-inet sees corp source IPs and depends entirely on bs-edge-rtr knowing where to route them back.

**Fix (overlay).** Set `pfsense_disable_outbound_nat: false` in `host_vars/bs-edge-fw.yml`. The role's "disable" code becomes a no-op; a fresh pfSense install then keeps its stock `automatic` outbound-NAT mode.

**Fix (upstream).** Consider making the `pfsense_firewall` role's disable-NAT default OFF, or driven by an explicit `pfsense_nat_outbound_mode` variable (accepting `automatic` / `hybrid` / `disabled`). The current all-or-nothing behavior forces every range to think about it per-firewall.

---

## 2026-07-08 · platform · fresh child DC — GPMC New-GPLink fails with HRESULT 0x8007054B despite AD services healthy

**Symptom.** On a freshly-promoted child DC (fops-dc01 in fops.blackstone.mil), `New-GPLink -Target "DC=fops,DC=blackstone,DC=mil"` returns:
```
The specified domain either does not exist or could not be contacted.
(Exception from HRESULT: 0x8007054B)
```
Reproducible even as `fops.blackstone.mil\Administrator`. Also `Get-ADPrincipalGroupMembership` returns "The server is not operational" while `Get-ADDomain` and `Get-GPO` succeed — mixed AD subsystem readiness.

**Detection.** All of these work:
- `Get-Service NTDS, ADWS, DNS` all `Running`
- `Resolve-DnsName fops.blackstone.mil` returns child DC's A records
- `Resolve-DnsName _ldap._tcp.pdc._msdcs.fops.blackstone.mil` returns fops-dc01
- `Get-ADDomain` returns `DC=fops,DC=blackstone,DC=mil`
- `Get-GPO -Name "Mapped Network Drives"` returns the GPO created earlier in the same role

Yet the specific RPC/GPMI subsystem used by `New-GPLink` (and by ActiveDirectory's `Get-ADPrincipalGroupMembership`) cannot bind to the domain. Points to RPC endpoint mapper or DRSUAPI binding not fully established on the freshly-promoted DC — the same subsystem that hosts DsReplicaGetInfo used by GPMC.

**Fix (overlay).** Wrapped the `Mapped Drive — fops.blackstone.mil` play tasks in a `block`/`rescue` structure so a first-deploy failure doesn't halt the rest of the playbook. (Initial attempt used `include_role` with `ignore_errors: true`, but that flag only affects the include-operation itself, NOT the tasks pulled in by the included role — the failing DSC task inside the role still marked the host as failed and aborted the play. `block`/`rescue` is the only reliable way to make in-role tasks non-fatal.) Mapped drives are convenience UX, not core range functionality. Re-run `--tags mapped_drive_fops` after the deploy completes; the RPC/GPMI subsystem usually settles within 10-30 minutes of promotion.

**Fix (upstream / platform).** No clean upstream fix. Possible mitigations:
- Add a `Wait-ForRpcSubsystem`-style task after dcpromo(child) that pings the RPC endpoint until it responds, before running any GPMC operation.
- Move `mapped_drive` for child domains to run AFTER a `Reboot Windows` cycle post-dcpromo (rebooting the DC forces full re-init of the RPC subsystem).
- Retry `New-GPLink` internally in the role with a delay loop rather than failing on first attempt.

---

## 2026-07-08 · gap · roles/mapped_drive/tasks/main.yml — DN builder broke for child domains

**Symptom.** Deploy failed on fops-dc01 with:
```
TASK [mapped_drive : Link GPO to OU]
FAILED! => 'domain_tld_name' is undefined
```

**Root cause.** The GPLink task built the domain DN as `DC={{ short_domain_name }},DC={{ domain_tld_name }}`, assuming a two-label FQDN (label + TLD). Works for `blackstone.mil` (=> `DC=blackstone,DC=mil`) but not for `fops.blackstone.mil` (three labels; there's no clean single-label `domain_tld_name` value to use). `group_vars/fops.yml` correctly defines only `domain_name` and `short_domain_name` and omits `domain_tld_name`.

**Fix (overlay).** Rebuilt the DN dynamically from `domain_name` by splitting on dots and joining with `,DC=`:
`Path: "DC={{ domain_name.split('.') | join(',DC=') }}"`. Works for arbitrary FQDN depth. No group_vars changes needed.

**Fix (upstream).** In `range-development-ansible/roles/mapped_drive/tasks/main.yml`, replace the two-part construction with the dynamic split form so the role is child-domain safe out of the box. Same pattern applies to any other DN-building tasks in customer roles (grep for `short_domain_name` + `domain_tld_name` co-usage).

---

## 2026-07-08 · gap · roles/dcpromo/tasks/main.yml — use parent Administrator for Install-ADDSDomain instead of granting EA to simspace

**Symptom.** On a fresh range's first deploy, dcpromo(child) partially completed Install-ADDSDomain on fops-dc01 — set the machine's Primary DNS Suffix to `fops.blackstone.mil` (systeminfo showed `Domain: fops.blackstone.mil`, `OS Configuration: Member Server`) — then failed. Fops-dc01 was left in a "half-joined" state: registry indicates domain membership, but only local SAM accounts exist (`net users` shows only Administrator/simspace/etc.) and `net group /DOMAIN` returns "domain not contacted". Consequence: unqualified NTLM auth to fops-dc01 fails for `simspace` (server misinterprets it as `fops.blackstone.mil\simspace` which doesn't exist). Only `.\simspace` (explicit local SAM) authenticates. All subsequent playbook plays that target fops-dc01 fail unreachable → 30-minute init hangs on retries.

**Root cause.** The prior (2026-07-07) EA-grant task on this line depended on `simspace` existing as an AD user in the parent forest before dcpromo(child) ran. But in `site.yml`, `Create Users` (line 470) runs AFTER dcpromo(child) (line 461) — so at the moment EA-grant fires, `simspace` may not yet exist in blackstone.mil. Even if `microsoft.ad.domain` migrates the local `simspace` user during forest creation, it's a Domain User (not EA and not DA), and `Install-ADDSDomain -DomainType ChildDomain` requires BOTH memberships — creating a new domain modifies the forest's Partitions container AND alters the schema replication topology. So EA alone was insufficient; the promotion still failed authorization.

**Fix (overlay, landed 2026-07-08).** Deleted the EA-grant task entirely. Changed the Install-ADDSDomain credential from `{{ parent_domain_name }}\{{ domain_admin }}` (= blackstone.mil\simspace) to `{{ parent_domain_name }}\Administrator`. The parent forest's built-in Administrator is auto-EA + auto-DA + Schema Admin as soon as microsoft.ad.domain finishes on bs-dc01; no group-membership manipulation needed. Password is preserved from the local Administrator, which the "local admin guest customization fix" tasks earlier in the dcpromo role already reset to `{{ domain_admin_password }}` (= Simspace1!Simspace1!). Idempotent, no delegate_to, no dependency on create_users having run first.

**Fix (upstream).** In `range-development-ansible/roles/dcpromo/tasks/main.yml`, when adding child-domain support (currently the customer role only handles forest-root creation), use the parent forest's Administrator credential rather than the operator's Ansible user. Alternatively, if using a domain user like `simspace` is preferred, split `create_users` into per-domain runs and enforce ordering: forest-root users must exist BEFORE any child-domain promotion attempts.

---

## 2026-07-07 · gap · roles/dcpromo/tasks/main.yml — child-domain path needs Enterprise Admin on the parent forest (SUPERSEDED)

> **SUPERSEDED 2026-07-08:** The EA-grant overlay described below was **deleted from the role**. Replaced by the newer 2026-07-08 fix that uses the parent-forest built-in `Administrator` credential (auto-EA + auto-DA + Schema Admin) for `Install-ADDSDomain`, so no group-membership manipulation is needed at all. See the 2026-07-08 `dcpromo` entry above. Entry retained here for historical context and for the root-cause explanation (why simspace-as-DA is not enough for child-domain creation), which the newer entry references.

**Symptom.** After the DNS bootstrap fix (below) landed, `Install-ADDSDomain` still failed on fops-dc01. With the surface-errors fix in place (commit 0857a62), the actual message came through:
```
PROMOTION_EXCEPTION: Verification of user credential permissions failed.
You have not supplied user credentials that belong to the Enterprise Admins
group. The installation may fail with an access denied error.
```
`C:\Windows\debug\dcpromoui.log` on fops-dc01 confirmed: `User is not EA`.

**Root cause.** Creating a child domain modifies the Partitions container in the forest's Configuration NC, which only Enterprise Admins can write to. The role uses `simspace` as the promotion credential — `simspace` is a Domain Admin in blackstone.mil (per the `create_users` role) but NOT an Enterprise Admin. Only the built-in `Administrator` gets auto-EA on forest-root install; any subsequently-created domain user needs explicit membership.

**Fix (overlay — REVERTED 2026-07-08).** Originally: added a task in the child-domain path that granted EA to `{{ domain_admin }}` on the parent forest before Install-ADDSDomain, delegated to bs-dc01. That approach was found to be insufficient (EA alone; `Install-ADDSDomain -DomainType ChildDomain` also requires Domain Admin membership) AND fragile (depends on simspace existing as an AD user before create_users runs, which the site.yml ordering did not guarantee). Deleted on 2026-07-08 in favor of using `blackstone.mil\Administrator` directly.

**Fix (upstream).** See the 2026-07-08 entry.

---

## 2026-07-07 · gap · roles/dcpromo/tasks/main.yml — child-domain path needs DNS pointed at parent PDC pre-promotion

**Symptom.** After the AD-Domain-Services install fix landed on 2026-07-06, fresh-range deploys got past the ADDSDeployment error but `Install-ADDSDomain` still didn't complete — fops-dc01 remained a WORKGROUP standalone. Later, all 15 fops member hosts failed to join with:
```
Computer 'fops-flight01' failed to join domain 'fops.blackstone.mil' from its
current workgroup 'WORKGROUP' with following error message: The specified
domain either does not exist or could not be contacted.
```
Diagnostic on fops-dc01: DomainRole=2 (standalone), ADDS installed, ADDSDeployment module importable, primary DNS = `172.31.3.12,172.31.3.11` (itself + sibling), `Resolve-DnsName blackstone.mil` empty. TCP 389 to bs-dc01 succeeded — network path fine, DNS bootstrap broken.

**Root cause.** `Install-ADDSDomain -DomainType ChildDomain -ParentDomainName blackstone.mil` needs to resolve `_ldap._tcp.blackstone.mil` SRV records to find a parent-forest DC to authenticate against. The default SimSpace image sets fops-dc01's primary DNS to the two designated fops.blackstone.mil DCs (172.31.3.11 = itself, 172.31.3.12 = fops-dc02). Neither can answer for blackstone.mil until child promotion completes — chicken-and-egg. Install-ADDSDomain fails silently (or bails so quickly the overall task appears to succeed), fops-dc01 stays standalone, and every downstream fops member join fails.

**Fix (overlay, landed 2026-07-07).** Added a pre-promotion task in the child-domain path of `roles/dcpromo/tasks/main.yml` that sets fops-dc01's primary DNS to `{{ parent_domain_pdc_ip }}` (172.31.2.7 = bs-dc01) plus 8.8.8.8 fallback, then calls `Clear-DnsClientCache`. Runs after the AD-Domain-Services install + reboot, before the Install-ADDSDomain block, gated by the same `when: parent_domain_name is defined` + `NEEDS_PROMOTION` guards. Introduces new `parent_domain_pdc_ip` variable in `group_vars/fops.yml`. After Install-ADDSDomain finishes and the reboot handler fires, fops-dc01 is itself a DC and its own DNS starts answering; downstream member joins can point at fops-dc01 (172.31.3.11) as designed.

**Fix (upstream).** In `range-development-ansible/roles/dcpromo/tasks/main.yml`, if a `parent_domain_name` var is present, the role should automatically set primary DNS to a parent DC before running Install-ADDSDomain — nobody who runs a child-domain promotion should have to figure this out themselves. The customer's dcpromo role currently only supports single-domain forest creation; a proper child-domain mode with DNS bootstrap would eliminate this whole class of failure.

---

## 2026-07-06 · gap · site.yml + roles/domain_member_retry — `pause` incompatible with `strategy: free`

**Symptom.** Both Join Domain plays (blackstone + fops) had `strategy: free` for wall-clock parallelism. Deploy fails immediately after the first member's Check-if-already-joined task:
```
TASK [domain_member_retry : Check if already domain joined]
changed: [bs-supply03]
ERROR! The 'pause' module bypasses the host loop, which is currently not
supported in the free strategy and would instead execute for every host
in the inventory list.
```
All 3 deploy.sh attempts fail identically before any host actually joins.

**Root cause.** `roles/domain_member_retry/tasks/main.yml:22` uses `ansible.builtin.pause` to wait for the post-join NIC flap to settle. Ansible's `free` strategy explicitly rejects `pause` because pause is a per-play blocker, not per-host — under free, it would either block all hosts (defeating the point) or fire N times per host (nonsense). Ansible chose to hard-fail the play rather than pick either behavior. Identical failure hit PowerPlant on 2026-07-03; airfield inherited the same optimization + the same bug when the strategy: free pattern was copied across.

**Fix (overlay).** Reverted `strategy: free` on the two Join Domain plays in `site.yml`. The other 6 `strategy: free` plays keep the speedup — strip_apipa, root_certs, network_discovery, AUE bundle, AE bundle, splunk-forwarder, sysmon — none of them use `pause`.

**Fix (upstream).** In `range-development-ansible/roles/domain_member_retry/tasks/main.yml`, replace `pause: seconds: N` with a delegated `wait_for` on the local Ansible controller:
```yaml
- name: Wait for network reconfiguration to complete
  ansible.builtin.wait_for:
    timeout: 30
  delegate_to: localhost
  become: false
```
`wait_for` works under `strategy: free`. This would let Join Domain — the single slowest play in the deploy — parallelize like the other 6 do.

---

## 2026-07-06 · gap · roles/dcpromo/tasks/main.yml — child-domain path missing AD-Domain-Services feature install

**Symptom.** On a fresh-range deploy the `dcpromo` role's child-domain task fails on `fops-dc01`:
```
TASK [dcpromo : Create child domain (this host becomes first DC of fops.blackstone.mil)]
fatal: [fops-dc01]: FAILED! => ...
  "message": "The specified module 'ADDSDeployment' was not loaded because no valid module
              file was found in any module directory."
  "target_name": "ADDSDeployment"
Import-Module ADDSDeployment -ErrorAction Stop
```
All 3 deploy.sh attempts fail identically at this task. Forest root (`bs-dc01`) succeeds because that path uses `microsoft.ad.domain`, which internally installs the feature; the child path uses `ansible.windows.win_powershell` directly.

**Root cause.** `Install-ADDSDomain` lives in the `ADDSDeployment` PowerShell module, which ships only once the **`AD-Domain-Services`** Windows Feature is installed on the host. The role installs `rsat-ADDS` (the RSAT client tools bundle — usable for querying an existing DC) but NOT the actual `AD-Domain-Services` role. `microsoft.ad.domain` auto-installs `AD-Domain-Services` as part of its own execution; the child-domain `win_powershell` block does not, so it hits `Import-Module ADDSDeployment` on a host that has only the RSAT client tools.

**Fix (overlay, landed 2026-07-06).** Added an `ansible.windows.win_feature` task for `AD-Domain-Services` with `include_management_tools: true` inside the child-domain gate (`when: parent_domain_name is defined`), followed by a conditional `win_reboot` in case the feature install requires it. Placed just after the "Compute child-domain label" set_fact and before "Check if host is already a DC". Idempotent: on subsequent runs, `win_feature` is a no-op if `AD-Domain-Services` is already present.

**Fix (upstream).** In `range-development-ansible/roles/dcpromo/tasks/main.yml`, make the RSAT install block install BOTH `AD-Domain-Services` (the role/feature) AND `rsat-ADDS` (the tools) unconditionally, before either mode runs. Both paths need the feature, and `microsoft.ad.domain`'s auto-install of it is an undocumented side effect that shouldn't be relied on.

---

## 2026-06-25 · platform · RC-VyOS-Router image — self-loop default routes per /24 interface IP

**Symptom.** A VyOS router with multiple /24 LAN interfaces (e.g. `bs-core-rtr` with Services/HQ/IT/Supply) loses its default route entirely after deploy. `show ip route` has no `S>* 0.0.0.0/0` line even though `static_route` declares one in host_vars; downstream subnets report "destination net unreachable."

**Detection.**
```
vyos@bs-core-rtr$ show configuration commands | grep 'static route 0.0.0.0/0'
set protocols static route 0.0.0.0/0 next-hop 172.31.1.5
set protocols static route 0.0.0.0/0 next-hop 172.31.2.1    # own eth1 IP
set protocols static route 0.0.0.0/0 next-hop 172.31.14.1   # own eth2 IP
set protocols static route 0.0.0.0/0 next-hop 172.31.15.1   # own eth3 IP
set protocols static route 0.0.0.0/0 next-hop 172.31.16.1   # own eth4 IP
vyos@bs-core-rtr$ show ip route 0.0.0.0/0
% Network not in table
```

**Root cause.** The `RC-VyOS-Router:1.1.0` image template ships a `0.0.0.0/0` static for every /24 interface IP on the box, with the next-hop set to the router's own connected IP. FRR refuses to install a default whose next-hop resolves to a local interface and ECMPs the entire route out of the FIB — net result, no default route at all. Same root cause and same template behaviour PowerPlant documented on 2026-05-27 for the SimSpace VyOS image.

**Fix (upstream).** Strip the per-/24 `0.0.0.0/0 next-hop <self>` defaults from the `RC-VyOS-Router:1.1.0` image template before publish. The image should ship with NO baked-in default route — host_vars / playbook decides.

**Workaround (overlay).** Each affected VyOS host declares `extra_static_routes_remove: [{network, next_hop}]` listing every self-loop next-hop in host_vars. The "Remove stale VyOS static routes (image-baked self-loop defaults)" play in `site.yml` issues a matching `delete protocols static route ...` so only the real default (`static_route` set by the customer `vyos` role) survives. Currently applied to: `bs-edge-rtr`, `bs-core-rtr`, `bs-ops-rtr`, `bs-sec-rtr`, `bs-modbus-gateway`.

---

## 2026-06-25 · bug · roles/pfsense_firewall/handlers/main.yml (ported from PowerPlant)

**Symptom.** On any pfSense host that defines BOTH `pfsense_bgp` and `pfsense_ospf` in `host_vars` (the eBGP-edge case — `bs-edge-fw` here, `pp-external-firewall` in PowerPlant), the `restart frr` handler fails with `bgpd: -A option specified more than once! Invalid options.` after committing FRR config changes. `ospfd` never starts; the firewall loses its OSPF adjacencies and BGP session.

**Root cause.** The handler's shell heredoc has the two protocol-launch lines inlined:

```
{% if pfsense_bgp is defined %}/usr/local/sbin/bgpd -d -A 127.0.0.1 -f /var/etc/frr/frr.conf{% endif %}
{% if pfsense_ospf is defined %}/usr/local/sbin/ospfd -d -A 127.0.0.1 -f /var/etc/frr/frr.conf{% endif %}
```

Ansible's default Jinja config sets `trim_blocks=True` + `lstrip_blocks=True`. With both, the newline after the first `{% endif %}` is trimmed AND the leading whitespace of the next `{% if %}` is stripped — leaving `bgpd ... frr.conf/usr/local/sbin/ospfd ...` as one shell command. The shell parses `-A 127.0.0.1` twice (once from each command) and `bgpd` rejects it.

**Detection.** `restart frr` handler errors with the `-A option specified more than once!` message + bgpd usage dump. The smushed command is visible in the failure output as a single `cmd:` line.

**Fix (upstream).** Put each `bgpd`/`ospfd` launch on its own line with the `{% if %}` and `{% endif %}` on their own lines too, so Jinja's whitespace stripping leaves the launch lines intact:

```
{% if pfsense_bgp is defined %}
/usr/local/sbin/bgpd -d -A 127.0.0.1 -f /var/etc/frr/frr.conf
{% endif %}
{% if pfsense_ospf is defined %}
/usr/local/sbin/ospfd -d -A 127.0.0.1 -f /var/etc/frr/frr.conf
{% endif %}
```

**Workaround (overlay).** Already applied in `airfield-range/roles/pfsense_firewall/handlers/main.yml`. Back-port the same fix into `PowerPlant/ss-pp-ab/roles/pfsense_firewall/handlers/main.yml` — `pp-external-firewall` defines both `pfsense_bgp` (eBGP to ISP) and `pfsense_ospf` (area 0 on DMZ + EDGE_TRANSIT), so the same bug should affect PowerPlant FRR convergence (consistent with the "FRR convergence pending verification" note in ss-pp-ab/CLAUDE.md).

---

## 2026-06-25 · bug · range-development-ansible/roles/common/tasks/windows.yml

**Symptom.** Every Windows host DDNS-registers BOTH its mgmt adapter (Ethernet0, `10.255.240.0/20`) AND its data-plane adapter into AD DNS. `ping bs-dc01` round-robins onto the mgmt IP roughly half the time — which corp workstations can reach over the platform orchestration VLAN but which is supposed to be out of play in-scenario.

**Root cause.** The customer `common/tasks/windows.yml` "Disable control net DNS registration" task is a double-bug:
1. The loop value is misspelled `Ehternet0` — never matches the actual mgmt adapter.
2. The cmdlet parameter is misspelled `RegisterThisConnectionAddress` instead of the real `RegisterThisConnectionsAddress`.

Net result: the task fires but achieves nothing.

**Fix (upstream).** Correct both typos in `range-development-ansible/roles/common/tasks/windows.yml`:
- `Ehternet0` → `Ethernet0`
- `RegisterThisConnectionAddress` → `RegisterThisConnectionsAddress`

Same root cause and same fix PowerPlant documented on 2026-05-27.

**Workaround (overlay).** Two plays in `site.yml`:
1. `Strip mgmt interface from AD DNS registration` (hosts: windows) — explicitly disables DDNS on `Ethernet0` and re-registers so only the data-plane adapter's record stays.
2. `Purge mgmt-subnet A records from AD DNS` (hosts: pdc) — scrubs any `10.255.240.0/20` A records that already snuck into each forest's zone before play #1 lands.

---

## 2026-06-25 · gap · range-development-ansible/roles/dns

**Symptom.** `nslookup vcab.lan` works from a domain-joined workstation, but `nslookup hbo.com` (or any external name) times out — no traffic exits the forest.

**Root cause.** The customer `dns` role creates AD zones + the records listed in `internal_dns_records` but doesn't configure DNS server forwarders. The Windows DNS server returns SERVFAIL for any zone it isn't authoritative for.

**Fix (upstream).** Have the role optionally set `Set-DnsServerForwarder` based on a `dns_forwarders` group_vars list, or document the requirement in the role README.

**Workaround (overlay).** `Configure DNS forwarders to is-inet` play in `site.yml` runs against `[domain_controllers]` and sets the forwarder list to `8.8.8.8 / 8.8.4.4 / 1.1.1.1` (is-inet's unbound aliases). Same pattern PowerPlant logged on 2026-05-22.

---

## 2026-06-25 · bug · range-development-ansible/roles/dns/tasks/main.yml

**Symptom.** First `dns` play run after a fresh `dcpromo` fails on both PDCs with `Failed to set properties on the zone <domain>: Failed to reset the directory partition for zone <domain> on server <DC>.`

**Root cause.** `dcpromo` auto-creates the forward zone for the new domain (e.g., `vcab.lan`) and stores it in the **domain** directory partition (`CN=MicrosoftDNS,DC=DomainDnsZones,...`). The customer `dns` role then runs `community.windows.win_dns_zone` with `state: present` + `replication: forest` (hardcoded). Because the zone already exists, the module attempts to **migrate** its replication scope from the domain partition to the forest partition. On a brand-new single-domain forest the migration call (`Set-DnsServerPrimaryZone -ReplicationScope Forest`) errors with the "reset the directory partition" message; for our two-forest build it fails identically on `vcab.lan` (bs-dc01) and `flightops.lan` (fops-dc01).

**Fix (upstream).** Replace the hardcoded `replication: forest` in both zone tasks with a variable that defaults to a value compatible with the most common deployment shape (single-domain forest):

```yaml
replication: "{{ dns_zone_replication | default('domain') }}"
```

For ranges with multi-domain forests where forest-wide replication actually matters, set `dns_zone_replication: forest` in `group_vars/all.yml`. The literal comment `# or 'domain' or 'none' based on your needs` already in the role file suggests the original author intended this to be tunable; it just never was.

**Workaround (overlay).** Already applied in `airfield-range/roles/dns/tasks/main.yml` (both Forward and Reverse zone tasks). Functionally identical for vcab.lan and flightops.lan since each is a single-domain forest. Back-port the same change into `range-development-ansible/roles/dns/tasks/main.yml`.

---

## 2026-06-26 · bug · range-development-ansible/roles/create_users/tasks/main.yml

**Symptom.** `Install-ADDSDomainController` on `bs-dc02` / `fops-dc02` fails with:

```
Verification of user credential permissions failed. You have not supplied
user credentials that belong to the Domain Admins group or the Enterprise
Admins group.
```

…even after `create_users` ran cleanly with no errors. Verifying directly on bs-dc01:

```
Get-ADGroupMember "Domain Admins"   # only Administrator; no simspace
Get-ADUser simspace -Properties MemberOf
# MemberOf: { CN=Users,CN=Builtin,DC=vcab,DC=lan,
#             CN=Administrators,CN=Builtin,DC=vcab,DC=lan }
```

…so `simspace` exists with the right password, but it's in **`Builtin\Administrators`** (local domain group on each DC) instead of the global **`Domain Admins`** group required by Install-ADDSDomainController.

**Root cause.** The `Group Assignment` task in `create_users` uses:

```yaml
microsoft.ad.user:
  name: "{{ item.name }}"
  groups:
    set: "{{ item.groups }}"   # e.g., ["Domain Admins"]
```

When `microsoft.ad.user` resolves the group name `"Domain Admins"`, it walks AD in a search order that hits the **`CN=Builtin`** container first. There happens to be an `Administrators` group there (`Builtin\Administrators`), and the partial/loose match logic appears to match `"Domain Admins"` against it instead of the global `CN=Domain Admins,CN=Users` group. Net result: the user lands in `Builtin\Administrators`, which gives them local-admin rights on the DC machine but does NOT grant the domain-wide Domain Admins privilege.

**Fix (upstream).** Replace the loose `microsoft.ad.user` `groups: set:` block with an explicit `microsoft.ad.group` `members: add:` pass that resolves the group by sAMAccountName:

```yaml
- name: Force-add Domain Users to their declared groups (explicit sAMAccountName)
  microsoft.ad.group:
    identity: "{{ item.1 }}"        # group sAMAccountName from DomainUsers[*].groups
    members:
      add: ["{{ item.0.name }}"]
  loop: "{{ DomainUsers | subelements('groups') }}"
  loop_control:
    label: "{{ item.0.name }} → {{ item.1 }}"
```

`microsoft.ad.group identity:` resolves unambiguously — `"Domain Admins"` lands in the correct global group every time.

**Workaround (overlay).** Already applied in `airfield-range/roles/create_users/tasks/main.yml`: the original `Group Assignment` task is kept (it's idempotent and harmless), with the new `microsoft.ad.group` task appended as a belt-and-suspenders pass. Removed the stale `group_assignment` role reference from `site.yml`'s Create Users play — the customer role at `range-development-ansible/roles/group_assignment/main.yml` is malformed (file at role root instead of `tasks/main.yml`, wrapped as a full playbook) and silently contributes zero tasks; it's redundant with `create_users` doing the job inline anyway.

---

## 2026-06-26 · bug (stale) · range-development-ansible/roles/group_assignment

**Symptom.** The customer's `group_assignment` "role" looks like:

```yaml
- name: Create Users
  hosts: pdc
  roles:
    - create_users
    - group_assignment
```

The intent is to add each `DomainUsers` entry to the AD groups declared in its `groups:` list (most importantly `Domain Admins`). In practice the role does nothing, so the `simspace` user gets created but is never added to Domain Admins. The first downstream symptom is `additional_dc` failing on bs-dc02 / fops-dc02 with:

```
Verification of user credential permissions failed. You have not supplied
user credentials that belong to the Domain Admins group or the Enterprise
Admins group.
```

**Root cause.** The `group_assignment` "role" is malformed:

```
roles/group_assignment/
├── README.md
└── main.yml      ← wrong path (should be tasks/main.yml)
```

…and `main.yml` is wrapped as a full playbook (`hosts: pdc`, `gather_facts: false`, `tasks:`) instead of being a bare task list. When referenced under a play's `roles:` block, Ansible's role loader looks for `tasks/main.yml`, doesn't find it, and silently contributes zero tasks. The role appears to run cleanly in the playbook output, but no group assignments actually happen.

**Fix (upstream).** Reorganize the customer's role to the conventional layout:

```
roles/group_assignment/
├── README.md
└── tasks/
    └── main.yml   ← bare task body, no playbook wrapper
```

Bare task body:

```yaml
- name: Group Assignment
  community.windows.win_domain_user:
    name: "{{ item.name }}"
    groups: "{{ item.groups }}"
    state: present
  loop: "{{ DomainUsers }}"
  when:
    - DomainUsers is defined
    - item.groups is defined
```

**Workaround (overlay).** Already applied in `airfield-range/roles/group_assignment/` — the tasks/main.yml exists with the bare task body and the old `main.yml` is deleted. Same fix should be back-ported to `range-development-ansible/roles/group_assignment/` (PowerPlant likely hits the same silent no-op, which would explain "FRR convergence pending verification" but not the broader AD trust posture — worth double-checking pp-dc02's Domain Admin membership).

---

## 2026-06-26 · bug · range-development-ansible/roles/mapped_drive

**Symptom.** Running `mapped_drive` against any non-DC Windows host fails with:

```
"msg": "Resource 'GroupPolicy' not found."
"msg": "Failed to invoke DSC Test method: The term 'Get-GPO' is not recognized..."
```

…on every member workstation / file server in both forests.

**Root cause.** The role uses `ansible.windows.win_dsc` with the **`GroupPolicy`** DSC resource (and `GPRegistryValue`, `GPLink` for the linked GPO). Both the DSC resource and the underlying `Get-GPO`/`Set-GPRegistryValue` cmdlets ship with `RSAT-GPMC`, which is **only installed by default on Domain Controllers**. Member workstations and member servers don't have it.

**Architecturally**, the role's intent IS to run once on a DC: create a single GPO, populate its registry values, link it to the domain root. GP replication then propagates the GPO to every DC and every member machine applies it at next login. Targeting member hosts directly was always the wrong shape — every member runs into the missing-module error and there's no benefit to running it per-host.

**Fix (upstream).** Either move the `mapped_drive` README to clearly call out "this role MUST run on a Domain Controller" or add a guard at the top of `tasks/main.yml`:

```yaml
- name: Fail early if RSAT-GPMC is missing
  ansible.windows.win_powershell:
    script: |
      if (-not (Get-Command Get-GPO -ErrorAction SilentlyContinue)) {
        throw "mapped_drive must run on a Domain Controller (RSAT-GPMC required)."
      }
```

**Workaround (overlay).** Already applied in airfield-range `site.yml`: both `Mapped Drive — vcab.lan` and `Mapped Drive — flightops.lan` plays now target `pdc_vcab` / `pdc_flightops` instead of `members_*`. The GPO replicates to every member automatically.

---

## 2026-06-26 · bug (recurring) · pfSense interface IP drops during FRR restart

**Symptom.** Hosts behind `bs-ops-fw` (Engineering `172.31.8.0/24`, SOC `172.31.7.0/24`) intermittently can't reach `bs-dc01` to join `vcab.lan`. Traceroute from a failed host (e.g., `bs-eng01 172.31.8.11`):

```
tracert -d 172.31.2.7
  1   172.31.8.1   <- bs-ops-fw responds
  2   172.31.8.1  reports: Destination host unreachable
```

OSPF adjacency is `Full` on bs-ops-fw (FRR shows it), and `bs-ops-rtr` knows the route to `172.31.2.0/24`. But `netstat -rn` on bs-ops-fw is missing the connected entry for `172.31.1.12/30` (SWITCH_3/vmx1) — vmx1 has no kernel IP, so the kernel rejects FRR's attempt to install the OSPF-learned route via that interface.

**Root cause.** The `pfsense_firewall` role's `restart frr` handler kills + restarts `zebra` and `ospfd`. On the SimSpace `RC_pfSense:1.0.0` image, that SIGTERM occasionally races with one of the data-plane interfaces and the kernel IP drops between the SIGTERM and the new daemon's `interface_attach`. After the handler completes, vmx1 (or whichever NIC lost the race) is up at L2 (FRR still gets Hellos) but has no IPv4 address at the kernel level.

The existing "Post-flight — re-verify data-plane interface IPs are bound" task in the role runs *before* the handler — so it can't catch a drop that happens *because of* the handler firing later in the same play.

**Fix (upstream).** Add a complementary post-handler rebind task that runs *after* `meta: flush_handlers`. Same PHP body as the pre-handler task (`interface_configure()` + raw `ifconfig`), just scheduled after the restart-frr handler fires.

**Workaround (overlay).** Applied in `airfield-range/roles/pfsense_firewall/tasks/main.yml` — new task "Post-handler — re-verify data-plane interface IPs survived FRR restart" runs immediately after the `flush_handlers` meta task. Does not re-notify `restart frr` (the daemons are already running; re-binding the kernel IP is enough — FRR's interface listener picks it up).

---

## 2026-06-29 · platform (recurring) · pfSense data-plane interface drops AFTER role finishes

**Symptom.** Every full `./deploy.sh` run, Eng + SOC member hosts (10 total, behind `bs-ops-fw`) fail `domain_member_retry` with "The specified domain either does not exist or could not be contacted." On `bs-ops-fw`, vmx1 (SWITCH_3) has lost its 172.31.1.14/30 kernel IP again — no connected route, OSPF route to vcab `172.31.2.0/24` can't install, hosts behind the firewall can't reach `bs-dc01`.

**Detection sequence:** `ifconfig vmx1` shows no `inet` line; `netstat -rn -f inet | head` is missing the `172.31.1.12/30 link#... vmx1` connected entry; `vtysh -c "show ip ospf neighbor"` shows the adjacency `Full` (LSAs flow at L2); `vtysh -c "show ip route 172.31.2.0/24"` shows the route in FRR but `netstat` doesn't have it in the kernel.

**Root cause (best understanding so far).** pfSense's `write_config()` triggers a background interface refresh on the SimSpace `RC_pfSense:1.0.0` image. The pre-handler `Post-flight — re-verify data-plane interface IPs are bound` task in `roles/pfsense_firewall/tasks/main.yml` and the post-handler companion `Post-handler — re-verify ...` both catch drift that happens DURING the play, but they can't catch a refresh that fires seconds-to-minutes AFTER the play completes — by then Ansible has moved on to the AD foundation plays and the vmx1 binding silently disappears in the gap.

**Fix (upstream).** Would require either a SimSpace image change to stop the delayed interface refresh, or a pfSense FRR package change to bind the data-plane IPs at a lower level (e.g., via `rc.conf.local` ifconfig lines) so they survive write_config refreshes.

**Workaround (overlay).** Four layers of defense in `airfield-range`:

1. `pfsense_firewall` role's `Post-flight — re-verify data-plane interface IPs are bound` task (pre-handler).
2. `pfsense_firewall` role's `Post-handler — re-verify data-plane interface IPs survived FRR restart` task (right after `meta: flush_handlers`).
3. Standalone play in `site.yml` named `pfSense — pre-AD interface re-verify (catches delayed vmx drop)` that fires between `pfSense firewalls` and `dcpromo`. 20-second settle pause + PHP rebind. Tagged across every AD-foundation tag (`strip_apipa`, `domain_member_retry`, `dcpromo`, etc.) so scoped runs can't accidentally skip it.
4. **Final layer (2026-06-29, after layers 1-3 still failed):** background watchdog daemon installed by the `pfsense_firewall` role at `/usr/local/etc/rc.d/airfield_iface_watchdog`. Loops every 30 seconds running the same rebind PHP, logging each rebind to syslog with tag `airfield-iface-watchdog`. Layers 1-3 are time-bounded (they only run during a deploy window); layer 4 is the only one that survives between deploys, which is when we observed the actual vmx1 drops happen (2-5 minute gaps from a known-good rebind to the next break, well after Ansible moved on).

When any rebind fires, watch `/var/log/messages` on the pfSense for an `airfield-iface-watchdog` syslog entry. Status: `service airfield_iface_watchdog status`. The maximum window where vmx1 stays broken is now ~30 seconds (one watchdog cycle).

---

## 2026-06-29 · bug · roles/pfsense_firewall/files/airfield_iface_watchdog.sh — skipped vmx1 on bs-ops-fw

**Symptom.** Even after the watchdog daemon (layer 4 above) was installed and verified running, every full deploy still produced "domain not contacted" failures on the 10 Eng/SOC hosts behind `bs-ops-fw`. vmx1 (172.31.1.14, SWITCH_3 transit toward bs-ops-rtr) stayed dropped indefinitely — the watchdog never logged a rebind for it.

**Root cause.** The watchdog script started with `if ($key === "lan" || $key === "wan") continue;` — intended to skip the management interface and the (non-existent on a transit firewall) WAN interface. But pfSense's `config.xml` assigns the key `wan` to whichever interface holds the **default gateway**. On `bs-ops-fw`, vmx1 is the default-gateway-facing interface (`GW_OPS_RTR` toward bs-ops-rtr), so pfSense keys it `wan`. The watchdog therefore deliberately skipped the very interface that keeps dropping.

**Fix (overlay).** Changed the skip condition from `$key === "lan" || $key === "wan"` to `$phys === "vmx0"`. Per CLAUDE.md §3 row 10, vmx0 is the management NIC on every pfSense firewall in this build, so excluding by physical name (rather than by config-key) reliably skips only the mgmt plane while supervising all data-plane interfaces — including the default-gateway-facing one. The lan-vs-wan keying inside pfSense is irrelevant to whether an interface is data-plane.

---

## 2026-06-30 · bug (upstream pfSense/FreeBSD) · pfSense syslog forwarding omits HOSTNAME field

**Symptom.** After enabling remote syslog forwarding (`syslog/enable` + `syslog/remoteserver` + `syslog/logall` in pfSense `config.xml`, then `system_syslogd_start()`), packets arrive at the SOC collector but `$hostname` is unparseable, so rsyslog's per-host template writes them to `/var/log/remote/<source-ip>/syslog.log` (e.g. `/var/log/remote/172.31.1.21/` for bs-ops-fw) instead of `/var/log/remote/bs-ops-fw/`.

**Detection.**
```
tcpdump -i any -n -A "udp port 514 and src <pfsense-ip>" -c 5
# Packets look like:
<30>Jun 30 16:24:03 dhclient[21361]: No DHCPOFFERS received.
#       ^^^^^^^^^^^^ timestamp     ^^^^^^^^^^ program — HOSTNAME field is missing
```

VyOS routers on the same collector format correctly (`Jun 30 16:24:03 bs-core-rtr systemd[1]: Started ...`).

**Root cause.** pfSense's FreeBSD syslogd does not insert the local hostname when forwarding messages received via the chrooted log socket (`/var/dhcpd/var/run/log`), and on pfSense 2.8.1 this behavior extends to most non-dhclient sources too. The remote messages are technically malformed RFC3164. Confirmed on pfSense 2.8.1 (SimSpace image `RC_pfSense:1.0.0`); was reportedly working on earlier PowerPlant images where the rsyslog template comment notes "pfSense sends its hostname unqualified (pp-ot-firewall)".

**Fix (upstream).** Would require a pfSense / FreeBSD syslogd patch to consistently insert the local hostname on remote forwards, regardless of which log socket the message came in on.

**Workaround (overlay).** Map source-IP → hostname on the rsyslog side. `roles/syslog_server/templates/30-remote.conf.j2` now iterates `syslog_source_ip_map` (a list of `{ip, name}` dicts from host_vars/soc-syslog.yml) and emits one `if $fromhost-ip == '<ip>' then set $!hostfile = '<name>';` line per entry. Non-pfSense sources still flow through the default `$hostname`-from-message path. The map is small (2 entries today, one per pfSense firewall) and inventory-driven, so adding a third firewall is one host_vars line.

After the fix, restart rsyslog on soc-syslog (the role's handler does this on template change) and any new packets land in `/var/log/remote/<hostname>/`. Stale IP-named directories from before the fix can be deleted manually.

---

## 2026-06-30 · platform · pfSense data-plane dhclient poisons zebra route installation

**Symptom.** On a fresh range deploy, ALL Eng + SOC member hosts (10 total, behind bs-ops-fw) fail `domain_member_retry` with "The specified domain either does not exist or could not be contacted." Every other Windows host joins fine — only the subnets that have to traverse bs-ops-fw fail. The watchdog reports vmx1 bound, OSPF neighbors Full, FRR's `show ip route` shows `O>* 172.31.2.0/24 via 172.31.1.13` (selected + installed in FIB). But `netstat -rn -f inet` is missing `172.31.2.0/24`, missing the default route, and missing everything else FRR claims to have installed via vmx1. `route -n get 172.31.2.7` returns "route has not been found." Routes via vmx3 (sec-rtr direction) install correctly; routes via vmx1 (ops-rtr direction) do not.

**Detection.**
```
# On bs-ops-fw:
vtysh -c "show ip route" | head -40
# Output includes mysterious bogus connected entries:
C>* 10.41.240.0/20  is directly connected, vmx1, 02:19:24
C>* 192.168.1.0/24  is directly connected, vmx1, 02:19:22
# Plus the legit:
C>* 172.31.1.12/30  is directly connected, vmx1
# These bogus C>* entries are NOT visible in `ifconfig vmx1` (which shows
# only the configured 172.31.1.14) -- they are stale state baked into
# zebra's connected-route view at zebra startup time.

ps auxww | grep "dhclient.*vmx"
# Reveals: dhclient running on vmx1 (data-plane) alongside vmx0 (mgmt).
_dhcp    6037   0.0  0.2  14408  3228  -  SCs  20:09  dhclient: vmx1
```

**Root cause.** SimSpace's `RC_pfSense:1.0.0` image spawns `dhclient` on EVERY `vmxN` interface at boot, regardless of whether config.xml has the interface set to `ipv4_type=static`. dhclient transiently acquires leases from the SimSpace backend platform networks (10.41.240.0/20, 192.168.1.0/24 observed). pfSense's `interface_configure()` then sets the interface to the configured static IP and removes the alias, but zebra has ALREADY read the connected-route table during its startup and recorded the transient subnets as `C>*`. Zebra then silently refuses to install any OSPF route via that interface because it can't unambiguously select among the (apparent) multiple connected paths.

**Fix (upstream).** SimSpace's pfSense 1.0.0 image should set the data-plane interfaces to `ipv4_type=staticv4` at the rc.conf level so dhclient never spawns on them, OR pfSense's `interface_configure()` should explicitly `pkill -f "dhclient.*<phys>"` when switching an interface from DHCP→static.

**Workaround (overlay).** Two defenses in `roles/pfsense_firewall`:

1. **Deploy-time kill** — `tasks/main.yml` "Kill dhclient on data-plane interfaces" task runs AFTER `interface_configure()` and BEFORE the `meta: flush_handlers` that triggers `restart frr`. Net effect: when zebra starts (or restarts), dhclient is dead on every vmx1+, so zebra's connected-route view is clean and OSPF route installation works.

2. **Runtime kill** — the `airfield_iface_watchdog.sh` daemon now runs `pkill -f "dhclient.*vmx[1-9]"` at the start of each 30-second loop iteration. If pfSense (or some image-side script) respawns dhclient between deploys, the watchdog kills it within 30s. Logged to syslog with tag `airfield-iface-watchdog` when a kill occurs.

Manual recovery if a deploy precedes the fix landing on the live firewall: `pkill -f "dhclient.*vmx[1-9]"; pkill -9 zebra; pkill -9 ospfd; sleep 3; /usr/local/sbin/zebra -d -A 127.0.0.1 -s 90000000 -f /var/etc/frr/frr.conf; sleep 4; /usr/local/sbin/ospfd -d -A 127.0.0.1 -f /var/etc/frr/frr.conf`. After the zebra+ospfd restart, the OSPF routes install correctly and Eng/SOC hosts can reach the vcab DCs.

---

## 2026-07-01 · bug · roles/common/tasks/windows.yml — DDNS disable targets typo'd adapter names

**Symptom.** `Disable control net DNS registration` task in the base `common` role never actually disabled DDNS on the management interface — the mgmt IP (10.255.240.0/20) leaked into AD DNS on every Windows host. `Resolve-DnsName bs-dc01 -DnsOnly` round-robin resolved to the mgmt IP half the time; `Test-NetConnection bs-dc01` would occasionally hit the OOB mgmt interface. Symptom shows up as workstation→server flows that shouldn't work (mgmt is out-of-band) succeeding intermittently.

**Detection.**
```yaml
# roles/common/tasks/windows.yml (pre-fix)
- name: Disable control net DNS registration
  ansible.windows.win_powershell:
    script: |
      Get-NetAdapter {{ item }} | set-DnsClient -RegisterThisConnectionsAddress $false
  loop:
    - Ehternet0   # ← typo, never matches real "Ethernet0"
    - Ethernet2   # ← doesn't exist on airfield hosts (we use Ethernet0=mgmt, Ethernet1=prod)
```
`Get-NetAdapter` silently returns an empty pipeline on the misspelled/missing name; the task reports `ok=1` and never modifies anything.

**Root cause.** Upstream customer repo (`range-development-ansible/roles/common/tasks/windows.yml`) has the same typo — it was carried forward when we copied `common` into `airfield-range/roles/` per the role-sourcing policy. PowerPlant/ss-pp-ab flagged this in their `PROJECT_LOG.md` but couldn't fix the base role directly (they don't own the customer repo), so they layered a compensating play in `arbitr_pp_playbook.yaml`. Airfield-range owns its `roles/common/` copy, so we can fix the source.

**Fix (overlay).** Correct `Ehternet0 → Ethernet0` and drop the non-existent `Ethernet2` entry so the loop only touches the real mgmt adapter. Added an inline comment referencing this entry and the belt-and-suspenders `Strip mgmt interface from AD DNS registration` overlay play in `site.yml:479` which handles two additional scenarios (purging already-registered mgmt A records on the PDC + forcing a re-register so the data-plane record stays).

**Fix (upstream).** File an issue against the customer repo — the base `common` role should target the mgmt adapter by ROLE, not by hard-coded interface name, so it's portable across ranges.

---

## 2026-07-01 · bug · roles/pfsense_firewall/tasks/main.yml — dhclient-kill shell task rc=-15

**Symptom.** After the fresh-range dhclient poisoning fix landed (2026-06-30 entry above), the first end-to-end deploy on the next range failed on the pfSense play with:
```
fatal: [bs-ops-fw]: FAILED! => {"cmd": "set +e\npkill -f 'dhclient.*vmx[1-9]'...", "rc": -15, "delta": "0:00:01.006324", "stdout": "", "stderr": ""}
```
`rc=-15` = SIGTERM to the Python subprocess wrapping the SSH command. Delta of exactly 1.006 seconds pins the kill to just after `sleep 1`, before the follow-up pgrep/echo could run. Empty stdout+stderr means the shell died mid-script.

**Root cause (best understanding).** The original task was `ansible.builtin.shell` running a multi-line script (`set +e; pkill; sleep 1; pgrep; if...`). On pfSense 2.8.1 (FreeBSD 14 base), the pkill occasionally severs the running task's own SSH session lineage even though the `dhclient.*vmx[1-9]` regex doesn't match Ansible's connection process. Cause suspected: pfSense's `/usr/local/sbin/watchfrr` or `sysrc` respawn logic tracks process trees and can SIGTERM adjacent shell descendants when it kills+restarts dhclient. The 1-second sleep window is enough for that cascade to reach our task's shell.

**Fix (overlay).** Switch from `shell: |` (multi-line script with sleep) to `command:` (single atomic pkill invocation). No sleep, no follow-up pgrep, no nested shell. `pkill -f 'dhclient.*vmx[1-9]'` returns 0 if it killed something, 1 if no matches, >1 on error. `failed_when: false` + `changed_when: rc == 0` absorbs both non-error rc values cleanly. The watchdog daemon (already running from the previous deploy) handles any respawn within 30s.

---

<!-- Entries are organized in phases: the most recent chronological run is at the top of
     the file (newest-first within that run), then older phases follow oldest-first. When
     adding a new entry, place it at the top under a "newest first" convention until the
     next phase break; then the whole run becomes historical and stays in place. -->

