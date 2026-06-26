# Fileserver Role

## Description
Creates and configures Windows file shares on designated file servers. The role provisions a local directory and creates a Windows SMB share with configurable permissions, enabling centralized file storage accessible by domain-joined systems.

## Variable Definition Location
Variables for this role should be defined in:
- **host_vars/[hostname].yml** for host-specific share configuration
- **group_vars/[domain].yml** where [domain] matches your AD domain inventory group name (e.g., site.yml, inet.yml) for domain-wide file server settings

## Required Variables

### In host_vars/[hostname].yml

| Variable | Required | Description |
|----------|----------|-------------|
| share_name | Yes | Name of the Windows share as it appears on the network |
| share_path | Yes | Local filesystem path where the share directory will be created |

## Optional Variables

### In group_vars/[domain].yml (e.g., site.yml, inet.yml)

| Variable | Required | Description |
|----------|----------|-------------|
| map_drive_letter | No | Drive letter for GPO-based drive mapping (e.g., "S") |
| map_drive_path | No | UNC path for drive mapping (e.g., `\\site-file.site.com\share`) |
| file_server_alias | No | DNS alias for the file server |
| file_server_ip | No | IP address of the file server for DNS records |

### Automatically Available Variables

| Variable | Description |
|----------|-------------|
| inventory_hostname | Hostname from Ansible inventory |
| domain_name | Domain name from group_vars/[domain].yml |

## Complete Example Configuration

### host_vars/site-file.yml
```yaml
ansible_host: 10.10.2.3
network_interfaces:
  - name: "Ethernet0"
    ipv4:
      address: "10.10.2.3"
      netmask: "255.255.0.0"
      gateway: ""
  - name: "Ethernet1"
    ipv4:
      address: "172.16.2.3"
      netmask: "255.255.255.0"
      gateway: "172.16.2.1"
    dns:
      - "172.16.2.7"

# File share configuration
share_name: "Share"
share_path: 'C:\share'
```
group_vars/site.yml (domain-wide configuration)
```yaml
domain_name: "site.com"
short_domain_name: "site"
domain_tld_name: "com"

# File server configuration
file_server_alias: "file"
file_server_ip: "172.16.2.3"

# Drive mapping configuration
map_drive_letter: S
map_drive_path: \\site-file.site.com\share
```
Minimal Configuration
host_vars/backup-file.yml
```yaml
ansible_host: 10.10.3.5
share_name: "Backup"
share_path: 'D:\backup'
```
host_vars/dept-file.yml
```yaml
ansible_host: 10.10.4.10
share_name: "DepartmentFiles"
share_path: 'C:\DeptShare'
