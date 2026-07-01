# Autologin Role

## Description
Configures Windows systems for automatic login at startup using specified domain credentials. The role sets registry keys to enable auto-login functionality, eliminating the need for manual authentication during system boot.

## Variable Definition Location
Variables for this role are defined in:
- **host_vars/[hostname].yml** for host-specific login credentials
- **group_vars/[domain].yml** for domain name

## Required Variables

### In host_vars/[hostname].yml

| Variable | Required | Description |
|----------|----------|-------------|
| logon_user | Yes* | Username for automatic login (*role only runs when defined) |
| logon_user_password | Yes* | Password for the auto-login user (*required with logon_user) |

### In group_vars/[domain].yml

| Variable | Required | Description |
|----------|----------|-------------|
| domain_name | Yes | Domain name for the auto-login account |

## Optional Variables

None - this role uses only the required variables listed above.

## Complete Example Configuration

### host_vars/acc-win10-1.yml
```yaml
ansible_host: 10.10.6.111
logon_user: "charity.bowen"
logon_user_password: "Simspace1!Simspace1!"
```
host_vars/acc-win10-2.yml
```yaml
ansible_host: 10.10.6.112
logon_user: "ahmed.ortega"
logon_user_password: "Simspace1!Simspace1!"
```
group_vars/site.yml
```yaml
domain_name: "site.com"
```
