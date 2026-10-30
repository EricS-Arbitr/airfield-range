# Domain_Member_Retry Role

## Description
Joins Windows systems to an Active Directory domain. The computer name is set from the Ansible inventory hostname.

Each join runs as a **pass**, and the role makes up to `domain_join_passes` of them (default 3). A pass is:

1. **Locator preflight.** Resolve `_ldap._tcp.dc._msdcs.<domain>` and open TCP 389, 88 and 445 to an advertised DC, retrying `domain_join_locator_retries` times at 15s intervals. This is what the join itself depends on, so passing here means the precondition holds rather than merely resembling it. The probe is pure PowerShell — `nltest` is not guaranteed present on every client image.
2. **Reboot**, from pass 2 onward only.
3. **Join**, non-fatal — `microsoft.ad.membership` routinely loses its connection mid-join because the host reconfigures its network identity, so the module's return code decides nothing.
4. **Settle and reconnect**, then re-read `Win32_ComputerSystem.PartOfDomain`. That reading is the outcome.

Once a pass succeeds, every task in the remaining passes is skipped. A host that never joins fails with the last probe result quoted, which separates "no DC answered" from "a DC answered and refused the join".

Hosts already in the domain when the role starts skip the passes entirely and do **not** notify the reboot handler.

Why the preflight exists: the previous shape attempted the join blind and, on failure, rebooted and tried once more. Blind, it could not tell a busy DC from a wrong one, and spent its only retry on a reboot that fixed nothing. See `UPSTREAM_FIXES.md` 2026-09-18.

## Tunables

| Variable | Default | Description |
|----------|---------|-------------|
| domain_join_passes | 3 | Join passes before the host is declared failed |
| domain_join_locator_retries | 20 | Locator probe retries per pass, 15s apart (20 × 15s = 5 min) |

## Variable Definition Location
Variables for this role should be defined in **group_vars/[domain].yml** where [domain] matches your AD domain inventory group name (e.g., site.yml, inet.yml)

## Required Variables

### In group_vars/[domain].yml

| Variable | Required | Description |
|----------|----------|-------------|
| domain_name | Yes | Active Directory domain to join |
| domain_admin | Yes | Domain administrator username |
| domain_admin_password | Yes | Domain administrator password |

### Automatically Available Variables

| Variable | Description |
|----------|-------------|
| inventory_hostname | Computer name from Ansible inventory |

## Complete Example Configuration

### group_vars/site.yml
```yaml
domain_name: "site.com"
domain_admin: "simspace"
domain_admin_password: "Simspace1!Simspace1!"
```
group_vars/inet.yml
```yaml
domain_name: "inet.com"
domain_admin: "admin"
domain_admin_password: "InetPass123!"
```
hosts
```yaml
[site]
site-file
site-mail
site-www

[inet]
inet-web
inet-db

[members]
site-file
site-mail
site-www
inet-web
inet-db
