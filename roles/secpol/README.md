# Secpol Role

## Description
Configures Windows security policy user rights assignments to support automated attack scenarios in the cyber range. The role grants specific security privileges to Domain Admins, enabling them to perform security-sensitive operations required for range exercises.

## Variable Definition Location
Variables for this role should be defined in **group_vars/[domain].yml** where [domain] matches your AD domain inventory group name (e.g., site.yml, inet.yml)

## Required Variables

### In group_vars/[domain].yml

| Variable | Required | Description |
|----------|----------|-------------|
| domain_name | Yes | The Active Directory domain name (e.g., "site.com") |

## Optional Variables

None - this role uses only the domain_name variable to construct the security principal.

## Complete Example Configuration

### group_vars/site.yml
```yaml
domain_name: "site.com"
```
group_vars/inet.yml
```yaml
domain_name: "inet.com"
