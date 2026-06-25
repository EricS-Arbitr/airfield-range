# DCPromo Role

## Description
Promotes a Windows Server to a Primary Domain Controller (PDC) for an Active Directory domain. The role installs required RSAT tools, creates a new forest and domain, configures DNS services, and handles Server 2019/2022 compatibility issues.

## Variable Definition Location
Variables for this role should be defined in **group_vars/[domain].yml** where [domain] matches your AD domain inventory group name (e.g., site.yml, inet.yml)

## Required Variables

### In group_vars/[domain].yml

| Variable | Required | Description |
|----------|----------|-------------|
| domain_name | Yes | Fully qualified domain name for the Active Directory domain |
| domain_admin_password | Yes | Safe mode administrator password for the domain controller |

## Complete Example Configuration

### group_vars/site.yml
```yaml
domain_name: "site.com"
domain_admin_password: "Simspace1!Simspace1!"
```
group_vars/inet.yml
```yaml
domain_name: "inet.com"
domain_admin_password: "Simspace1!Simspace1!"
```
Optional Related Variables
These variables are often defined alongside domain settings for use by other roles:
group_vars/site.yml
```yaml
domain_name: "site.com"
short_domain_name: "site"  # NetBIOS name
domain_tld_name: "com"      # Top-level domain
domain_admin: "simspace"    # Default admin username
domain_admin_password: "Simspace1!Simspace1!"
