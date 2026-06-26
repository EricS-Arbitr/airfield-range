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

<!-- New entries go above this line, newest first. -->
