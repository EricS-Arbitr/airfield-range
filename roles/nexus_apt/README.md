# nexus_apt

Points a managed Ubuntu host's apt at the in-platform **per-release** Nexus
repos (`ng-apt-jammy-prod` / `ng-apt-noble-prod` on
`range-registry.dev.ng.simspace.lan`), so apt installs come from Nexus in an
air-gapped range instead of `archive.ubuntu.com`.

Invoked by `playbooks/12-nexus-apt.yml` on `linux:malcolm`, early (before
`00-baseline`), so every later apt install resolves from Nexus. It is the single
owner of the ng-apt apt source that `docker_engine` depends on
(`docker_engine_use_nexus_apt: true`).

What it does:

- Removes any leftover source/pin/drop-in from the old mixed-release `ng-apt`.
- Installs the **bundled** signing key (`files/simspace-apt-central.asc`) to
  `/etc/apt/keyrings/` and **asserts its fingerprint**
  (`94B0 39FE 2E15 E293 6ED1 AB46 6459 1A0D 0B01 32DD`) before trusting it — no
  deploy-time key fetch.
- Writes `/etc/apt/sources.list.d/ng-apt-prod.sources` (deb822) for the host's
  own codename, `main`, `Signed-By` the bundled key.
- Pins the Nexus repo to priority 600 so it is **preferred** over
  `archive.ubuntu.com` (priority 500), which stays as a **fallback**.
- Skips TLS verification for `range-registry` only (internal CA; integrity is
  still enforced by the GPG `Signed-By`), matching the `validate_certs: no`
  pattern every `nexus.*` fetch in this repo already uses.
- `apt update`.

Transport: apt reaches `range-registry` through the range's existing mgmt-plane
proxy (the same path the working `nexus.*` fetches use); this role sets no proxy
itself.

Key variables: see `defaults/main.yml` (`nexus_apt_registry_host`,
`nexus_apt_repo`, `nexus_apt_pin_priority`, `nexus_apt_skip_tls_verify`).
