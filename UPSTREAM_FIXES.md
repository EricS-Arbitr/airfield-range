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

<!-- New entries go above this line, newest first. -->
