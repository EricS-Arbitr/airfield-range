# 03 — Active Directory

## Forest

A single forest with **two domains** and **four** domain controllers:

| Domain | Role | DC | Address | Inventory group |
|---|---|---|---|---|
| `blackstone.mil` | Forest root / base enterprise | `bs-dc01` | `172.31.2.7` | `pdc` |
| `blackstone.mil` | | `bs-dc02` | `172.31.2.8` | `additional_dc` |
| `fops.blackstone.mil` | Child domain / flight operations | `fops-dc01` | `172.31.3.11` | `pdc` |
| `fops.blackstone.mil` | | `fops-dc02` | `172.31.3.12` | `additional_dc` |

`fops.blackstone.mil` is a **child domain** in the same forest as the root
`blackstone.mil`. The parent/child trust is automatic, bidirectional and
transitive — no manual cross-forest trust is set up, and a principal in either
domain can authenticate to resources in the other by default. Routine
cross-domain identity traffic is therefore *expected*; the point of interest is
where it appears that it should not.

Promotion order matters. The two forest-root and child-root DCs (`bs-dc01`,
`fops-dc01`) are promoted first via `dcpromo`; the additional DCs
(`bs-dc02`, `fops-dc02`) join via `additional_dc`. The child domain is the
fragile step: a half-completed child promotion leaves residue that blocks a
clean retry, so a `dcpromo(child)` self-heal pass runs ahead of promotion to
clear it.

## Windows Event Collectors

Each domain has a WEC that receives forwarded Windows events and is the audited
log path into monitoring:

| WEC | Address | Domain |
|---|---|---|
| `bs-wec` | `172.31.2.17` | blackstone.mil |
| `fops-wec01` | `172.31.3.20` | fops.blackstone.mil |

## Membership

- **44 domain members** (`[members_blackstone]` 29 + `[members_fops]` 15) +
  4 DCs = **48 domain-joined hosts**. Every Windows VM in the range is
  domain-joined.
- Enterprise members include the HQ/IT/Supply workstations, the file servers,
  the Engineering **Windows** workstations (`bs-eng01..04`) and the SOC analyst
  workstations (`soc-analyst01..06`) — Engineering and SOC Windows hosts join
  `blackstone.mil` by decision.
- Flight-ops members include the flight-ops user workstations, the flight
  display workstations, the file servers and `fops-wec01`.
- The Engineering **Linux** hosts (`bs-eng05`, `bs-eng06`), the ATC subsystem
  hosts, the fuel-farm OT hosts, the Security Onion grid and the forensic
  tooling are **not** domain-joined.

Counts are derived from the inventory; re-derive after inventory changes (see
the inventory & roles chapter).

## Emulated users

Domain user identities with realistic names are seeded across both domains and
placed in the appropriate groups. Emulated users run on the Windows
workstations in `[aue]` — HQ, IT, Supply, Engineering (Windows), flight-ops
users, the flight display consoles, and the SOC analyst workstations — and log
in, browse, open documents and move files continuously. This is the background
noise detection logic must survive. `is-inet` hosts mailboxes for both domains
(Postfix + Dovecot, multi-domain) so user mail traffic is real.

## DNS and the management plane (the recurring hazard)

Every host is dual-homed, with a management NIC on `10.255.240.0/20`. Windows
registers **both** adapters into AD DNS by default, so `ping <host>` can resolve
to a management IP half the time. Two mitigations are wired in:

1. A baseline play strips the management interface from AD DNS registration and
   scrubs `10.255.240.x` A records. (The base `common` role's own attempt at
   this has known bugs — a typo'd adapter name and the wrong cmdlet parameter —
   so the overlay play is the real fix.)
2. DCs force-register and prove their locator SRV records before any member
   join runs, because a fresh build sometimes starts joins before Netlogon has
   registered them.

`Resolve-DnsName <host> -DnsOnly` is the gold-standard check — `ping` and
`nslookup` both have LLMNR/NetBIOS footguns on the shared management L2.

## DNS forwarders

The `dns` role does not set forwarders, so anything outside the two Blackstone
domains is unresolvable on domain workstations until a baseline play points the
DCs at `is-inet`'s simulated public resolvers. This keeps the range
self-contained while still giving trainees name resolution for scenario domains.

## Child-DC / DNS-partition convergence note

On a cold build a freshly promoted child DC can answer ADWS minutes before its
`DomainDnsZones` application partition is enlisted, and DNS record writes against
it fail until it is. The `dns` play gates on the partition being present
(probing it, creating the built-in partitions if absent, restarting the DNS
service, re-probing) and record writes retry rather than failing the host.
Keep the gate and the record retry if you refactor the `dns` play — the
two-domain build exercises this path on every cold run.
