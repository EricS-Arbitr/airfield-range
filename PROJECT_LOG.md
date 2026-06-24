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
