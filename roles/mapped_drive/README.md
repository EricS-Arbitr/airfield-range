# Mapped_Drive Role

## Description
Creates and configures a Group Policy Object (GPO) to automatically map network drives for domain users at login. The role sets up the necessary registry entries through GPO to establish persistent network drive mappings across the domain.

## Variable Definition Location
Variables for this role should be defined in **group_vars/[domain].yml** where [domain] matches your AD domain inventory group name (e.g., site.yml, inet.yml)

## Required Variables

### In group_vars/[domain].yml

| Variable | Required | Description |
|----------|----------|-------------|
| map_drive_letter | Yes | Drive letter to be mapped (e.g., "S", "T", "U") |
| map_drive_path | Yes | UNC path to the network share (e.g., `\\site-file.site.com\share`) |
| short_domain_name | Yes | NetBIOS name of the domain (e.g., "site") |
| domain_tld_name | Yes | Top-level domain extension (e.g., "com") |

## Optional Variables

None - this role uses only the required variables listed above.

## Complete Example Configuration

### group_vars/site.yml
```yaml
domain_name: "site.com"
short_domain_name: "site"
domain_tld_name: "com"

# Drive mapping configuration
map_drive_letter: S
map_drive_path: \\site-file.site.com\share
```
group_vars/inet.yml
```yaml
domain_name: "inet.com"
short_domain_name: "inet"
domain_tld_name: "com"

# Drive mapping configuration
map_drive_letter: T
map_drive_path: \\inet-share.inet.com\public
