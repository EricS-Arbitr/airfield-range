# ae_gpo Role

## Description
Configures domain-level Group Policy settings to reduce security restrictions and support automated attack scenarios in the cyber range. The role disables password complexity requirements and modifies User Account Control (UAC) settings through the Default Domain Policy to enable seamless execution of attack simulations.

## Variable Definition Location
Variables for this role should be defined in **group_vars/[domain].yml** where [domain] matches your AD domain inventory group name (e.g., site.yml, inet.yml)

## Required Variables

### In group_vars/[domain].yml

| Variable | Required | Description |
|----------|----------|-------------|
| domain_name | Yes | The Active Directory domain name (e.g., "site.com") |

## Optional Variables

None - this role uses only the domain_name variable.

## GPO Settings Applied

The role configures the following settings in the Default Domain Policy:

| Setting | Value | Description |
|---------|-------|-------------|
| Password Complexity | Disabled | Removes password complexity requirements |
| ConsentPromptBehaviorAdmin | 0 | Elevates without prompting for administrators |
| ConsentPromptBehaviorUser | 0 | Automatically denies elevation requests for standard users |
| EnableLUA | 0 | Disables UAC (User Account Control) |

## Complete Example Configuration

### group_vars/site.yml
```yaml
domain_name: "site.com"
```
group_vars/inet.yml
```yaml
domain_name: "inet.com"
