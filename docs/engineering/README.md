# Blackstone Auxiliary Field — Engineering Documentation Pack

Engineering reference for the **Blackstone Auxiliary Field** cyber range: a
two-domain military-base enterprise across `blackstone.mil` and its flight-ops
child `fops.blackstone.mil`, a layer-3–isolated fuel-farm OT enclave, a bespoke
air-traffic-control subsystem, and the blue-team instrumentation that watches
all of it. This pack is for engineers who **build, extend, operate and debug**
the range — not for trainees or event coordinators.

**Audience.** Engineers who build, extend, operate and debug the range. Assumes
working knowledge of Security Onion, Active Directory, Ansible, and the SimSpace
platform.

**Scope.** The range as deployed by its Ansible overlay. Every variant runs a
distributed Security Onion grid as its network-security monitoring; one variant
additionally runs a distributed Splunk cluster, with a universal forwarder on
every managed host feeding it alongside the Elastic Agent, plus a Malcolm
appliance for OT deep-packet analysis. Where behaviour differs between the
monitoring-only and the Splunk-stacked variants, the text calls it out.

For the operator view see the **Range Guide**; for the one-page capability
summary see the **Range Overview**. This pack goes deeper into design rationale,
the build/verify pipeline, the inventory and role model, and the failure modes
you will actually hit.

## Contents

| Chapter | Covers |
|---|---|
| 01 · Architecture | Purpose, design decisions, system architecture, data/telemetry flows, segmentation model |
| 02 · Network | Addressing plan, transit links, the OSPF/eBGP/static routing model, firewalls, DNS, the GRE mirror fabric |
| 03 · Active Directory | Two-domain forest, four DCs, parent/child trust, members, emulated users, DNS/mgmt hazards |
| 04 · Inventory & roles | Inventory groups and their meaning, host→group mapping, custom + base roles, group_vars hierarchy |
| 05 · Build & deploy | Tarball build, build-time gates, blueprint fetch + deploy, the 3-attempt retry model, phase order, vault, Nexus artifact sourcing |
| 06 · Security monitoring & telemetry | The Security Onion grid, sensor placement, registry seeding, Fleet integrations, endpoint telemetry, the Splunk cluster + universal forwarders, Malcolm OT DPI |
| 07 · Verification | Build-time verifiers, runtime health checks, what "healthy" looks like |
| 08 · Operations & troubleshooting | Known behaviours, common pitfalls, secrets/rotation, re-deploy/reset, blueprint re-pinning |

## Authority order

When sources disagree, trust them in this order:

1. **The platform blueprint** — the definitive list of what VMs exist, their
   images, IPs and sizing.
2. **The inventory** — group membership, which drives what Ansible does to each
   host.
3. **`group_vars` / `host_vars`** — the single source of truth for addressing,
   credentials, pins and tunables.
4. **This pack** — narrative and rationale over the above.

A number in prose is only as good as the blueprint and inventory it was derived
from. Where this pack states a count (host totals, Windows totals, sensor
coverage, …) it was computed from the range's current blueprint and inventory;
re-derive after any change rather than trusting the text.
