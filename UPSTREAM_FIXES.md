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

<!-- New entries go above this line, newest first. -->
