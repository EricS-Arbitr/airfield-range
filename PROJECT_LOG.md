# airfield-range — Project Activity Log

Period: 2026-06-18 → ongoing

## Goal

A working Ansible overlay (`airfield-range`) that provisions the **JCTE vCity Military Airfield** cyber range on top of customer's `range-development-ansible` base, with all role customizations copied into `airfield-range/roles/` per the role-sourcing policy. Five bespoke OT/business systems: weather radar, ATC, access control (Leosac), fuel farm, power grid. Two-tier AD (`vcab.lan` + `flightops.lan`) with cross-forest trust.

## Phase log

### Phase 0 — Scoping & decisions (2026-06-18 → 2026-06-24)

- CLAUDE.md owner-decision table populated (control plane `10.255.240.0/20`; pfSense firewalls; DNP3 power; segmented OT; in-enclave historian; Ubuntu 24.04 baseline; `.2` reserved; OSPF + eBGP-edge + static-at-L3.5; pfSense automation = `pfsensible.core` + `php -r`; pfSense NIC position FIRST; AD trust = vcab.lan TRUSTS flightops.lan).
- Network blueprint `WORK_DIR/ARBITR_MB_011.yml` built and iterated — 80 `VmInstance`s including the `ansible` control node, `bs-modbus-gateway` with full 4-NIC Purdue chain, every host with a `managementInterface` block, name/hostname cleanup.
- Inventory (`hosts`) drafted, 79 production VmInstances accounted for across 52 host-list groups + 14 `:children` roll-ups (the `ansible` host is platform-managed and lives in `[infrastructure]`).
- `group_vars/` and `host_vars/` scaffolded (13 group files, 79 per-host files generated from the blueprint).
- Scaffolding ported from `ss-pp-ab/` (`build_tarball.sh`, `deploy.sh`, `verify_vars.py`, `requirements.yml`, `UPSTREAM_FIXES.md`, this file).
- First roles copied in: `init`, `common`, `vyos`, `handlers` (meta dep of common). All sourced from `range-development-ansible/roles/` per the role-sourcing policy.

### Phase 1 — Network (planned)

00-network: VyOS routers (5) + pfSense firewalls (2). OSPF area 0 IGP across corp links + LAN interfaces; eBGP at `bs-edge-rtr` ↔ `bs-edge-fw`; STATIC-only at `bs-ops-fw` ↔ `bs-modbus-gateway`.

### Phase 2 — Foundation (planned)

10-foundation: NTP (`bs-ntp` once added to blueprint), both AD domains + cross-forest trust, CA, DNS, DHCP.

### Phase 3+ — Enterprise / Flight ops / SOC / PACS / OT / Injection (planned)

Per CLAUDE.md §10 deployment-order tiers.

---

## 2026-08-11 — Security Onion ported in (branch `security-onion`)

Distributed SO 2.4 grid: manager + search + four sensors, one sensor per
mirrored router. Branch is deliberately unmerged; `main` stays Splunk-only.

**Roles copied** from `PowerPlant/ss-pp-ab@security-onion` per the
role-sourcing policy, unmodified except where the range differs:
`so_base`, `so_apt_mirror`, `so_manager`, `so_search`, `so_sensor`,
`vyos_mirror`, `elastic_agent`. The only edit was the topology named in
`elastic_agent`'s preflight failure message (pp-ot-firewall -> bs-ops-fw).
`so_subnet_security` is aliased in group_vars rather than renamed in the
roles, so the next re-copy stays a plain `cp` — see UPSTREAM_FIXES.

**Playbooks** `playbooks/05-time … 75-endpoint`, appended to `site.yml` as
nine `import_playbook` entries rather than interleaved, so a diff against
`main` shows the SO work and nothing else.

Three differ from PowerPlant's versions on purpose:

- **75-endpoint** drops the Sysmon install play. site.yml already ends with
  Sysmon scoped to `hosts: windows`; PowerPlant needed the play because its
  baseline installed Sysmon only on the `[aue]` workstations. The
  verification play stays.
- **75-endpoint** replaces the first-three-octets subnet comparison with real
  CIDR containment via `ipaddr`. PowerPlant's shortcut is exact only when
  every declared subnet is a /24 and says so in its own comment; this range
  has `172.16.45.0/29` and `172.16.45.8/29` sharing a third octet. Exercised
  three ways before shipping: 69/69 covered on real inventory; both OT hosts
  named when the /29s are removed; and ff-plc-1 alone named when only the
  first /29 is declared — proving the boundary is respected, not rounded.
- **70-analyst** targets `[soc_analysts]`, not `[hunt]`. Here `[hunt]` is
  soc-flare/soc-sift/soc-openvas, two of which are Linux, and every task in
  that playbook is `win_powershell`.

**Also:** `vault_so_web_password` + `vault_so_remote_password` added to the
vault; `ansible.utils` added to requirements.yml (it was already a hard
dependency of `roles/common`, working only because the controller image
happens to ship it).

### Outstanding before this can deploy

1. `vyos_gre_source_ip` / `so_gre_remote_underlay` per router — drafted and
   marked VERIFY in both files. They must match EXACTLY.
2. Router interface numbering — `show interfaces` on all four.
3. The blueprint's download `ScriptDefinition` still pins a `main` commit.

