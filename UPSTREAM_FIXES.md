# Upstream Fixes & Enhancements — airfield-range

Running log of issues, gaps, and suggested improvements discovered while deploying `airfield-range`. Candidates for PRs or discussion with the `range-development-ansible` maintainers, or for re-copies from PowerPlant when an existing PowerPlant fix needs to be brought across.

Per the [role-sourcing memory](../../.claude/projects/-Users-eric-starace-vCity/memory/project_airfield_role_sourcing.md): when an upstream fix is needed in airfield-range too, re-copy it explicitly **and log it here the same turn** (per the [UPSTREAM_FIXES feedback memory](../../.claude/projects/-Users-eric-starace-vCity/memory/feedback_upstream_fixes_log.md)).

Severity key:
- **bug** — role malfunctions or produces incorrect results
- **gap** — missing functionality that ranges have to work around
- **enhancement** — works but could be more robust or ergonomic
- **platform** — SimSpace platform-side issue, not Ansible

Format: `## YYYY-MM-DD · <severity> · <target path / heading>` followed by Symptom → Detection (if non-obvious) → Fix (upstream) → Workaround (overlay).

---

## 2026-07-08 · gap · roles/dcpromo/tasks/main.yml — use parent Administrator for Install-ADDSDomain instead of granting EA to simspace

**Symptom.** On a fresh range's first deploy, dcpromo(child) partially completed Install-ADDSDomain on fops-dc01 — set the machine's Primary DNS Suffix to `fops.blackstone.mil` (systeminfo showed `Domain: fops.blackstone.mil`, `OS Configuration: Member Server`) — then failed. Fops-dc01 was left in a "half-joined" state: registry indicates domain membership, but only local SAM accounts exist (`net users` shows only Administrator/simspace/etc.) and `net group /DOMAIN` returns "domain not contacted". Consequence: unqualified NTLM auth to fops-dc01 fails for `simspace` (server misinterprets it as `fops.blackstone.mil\simspace` which doesn't exist). Only `.\simspace` (explicit local SAM) authenticates. All subsequent playbook plays that target fops-dc01 fail unreachable → 30-minute init hangs on retries.

**Root cause.** The prior (2026-07-07) EA-grant task on this line depended on `simspace` existing as an AD user in the parent forest before dcpromo(child) ran. But in `site.yml`, `Create Users` (line 470) runs AFTER dcpromo(child) (line 461) — so at the moment EA-grant fires, `simspace` may not yet exist in blackstone.mil. Even if `microsoft.ad.domain` migrates the local `simspace` user during forest creation, it's a Domain User (not EA and not DA), and `Install-ADDSDomain -DomainType ChildDomain` requires BOTH memberships — creating a new domain modifies the forest's Partitions container AND alters the schema replication topology. So EA alone was insufficient; the promotion still failed authorization.

**Fix (overlay, landed 2026-07-08).** Deleted the EA-grant task entirely. Changed the Install-ADDSDomain credential from `{{ parent_domain_name }}\{{ domain_admin }}` (= blackstone.mil\simspace) to `{{ parent_domain_name }}\Administrator`. The parent forest's built-in Administrator is auto-EA + auto-DA + Schema Admin as soon as microsoft.ad.domain finishes on bs-dc01; no group-membership manipulation needed. Password is preserved from the local Administrator, which the "local admin guest customization fix" tasks earlier in the dcpromo role already reset to `{{ domain_admin_password }}` (= Simspace1!Simspace1!). Idempotent, no delegate_to, no dependency on create_users having run first.

**Fix (upstream).** In `range-development-ansible/roles/dcpromo/tasks/main.yml`, when adding child-domain support (currently the customer role only handles forest-root creation), use the parent forest's Administrator credential rather than the operator's Ansible user. Alternatively, if using a domain user like `simspace` is preferred, split `create_users` into per-domain runs and enforce ordering: forest-root users must exist BEFORE any child-domain promotion attempts.

---

## 2026-07-07 · gap · roles/dcpromo/tasks/main.yml — child-domain path needs Enterprise Admin on the parent forest

**Symptom.** After the DNS bootstrap fix (below) landed, `Install-ADDSDomain` still failed on fops-dc01. With the surface-errors fix in place (commit 0857a62), the actual message came through:
```
PROMOTION_EXCEPTION: Verification of user credential permissions failed.
You have not supplied user credentials that belong to the Enterprise Admins
group. The installation may fail with an access denied error.
```
`C:\Windows\debug\dcpromoui.log` on fops-dc01 confirmed: `User is not EA`.

**Root cause.** Creating a child domain modifies the Partitions container in the forest's Configuration NC, which only Enterprise Admins can write to. The role uses `simspace` as the promotion credential — `simspace` is a Domain Admin in blackstone.mil (per the `create_users` role) but NOT an Enterprise Admin. Only the built-in `Administrator` gets auto-EA on forest-root install; any subsequently-created domain user needs explicit membership. My role's earlier comment ("`simspace` is Domain Admin in the parent... auto-promoted into Enterprise Admins on forest-root install") was wrong on that second half — the auto-promotion only applies to the account that ran the promotion (Administrator), not to subsequently-created domain users.

**Fix (overlay, landed 2026-07-07).** Added a task in the child-domain path that grants EA to `{{ domain_admin }}` on the parent forest before Install-ADDSDomain runs. `delegate_to: "{{ groups['pdc_blackstone'] | first }}"` so the `Add-ADGroupMember -Identity "Enterprise Admins"` call lands on the forest-root DC where the group lives. Idempotent — catches the "already a member" exception and reports `ALREADY_EA` instead of failing on re-runs.

**Fix (upstream).** In `range-development-ansible/roles/create_users/tasks/main.yml`, if the target user is `simspace` (or any Domain Admin whose role includes forest-management), add them to Enterprise Admins + Schema Admins on the forest root. Alternatively, the customer's dcpromo role should handle this automatically when `parent_domain_name` is set. Right now the customer role only supports single-domain forest creation, so this is one of several gaps around child-domain support.

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

## 2026-06-29 · bug · roles/pfsense_firewall/files/airfield_iface_watchdog.sh — skipped vmx1 on bs-ops-fw

**Symptom.** Even after the watchdog daemon (layer 4 above) was installed and verified running, every full deploy still produced "domain not contacted" failures on the 10 Eng/SOC hosts behind `bs-ops-fw`. vmx1 (172.31.1.14, SWITCH_3 transit toward bs-ops-rtr) stayed dropped indefinitely — the watchdog never logged a rebind for it.

**Root cause.** The watchdog script started with `if ($key === "lan" || $key === "wan") continue;` — intended to skip the management interface and the (non-existent on a transit firewall) WAN interface. But pfSense's `config.xml` assigns the key `wan` to whichever interface holds the **default gateway**. On `bs-ops-fw`, vmx1 is the default-gateway-facing interface (`GW_OPS_RTR` toward bs-ops-rtr), so pfSense keys it `wan`. The watchdog therefore deliberately skipped the very interface that keeps dropping.

**Fix (overlay).** Changed the skip condition from `$key === "lan" || $key === "wan"` to `$phys === "vmx0"`. Per CLAUDE.md §3 row 10, vmx0 is the management NIC on every pfSense firewall in this build, so excluding by physical name (rather than by config-key) reliably skips only the mgmt plane while supervising all data-plane interfaces — including the default-gateway-facing one. The lan-vs-wan keying inside pfSense is irrelevant to whether an interface is data-plane.

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

<!-- New entries go above this line, newest first. -->
