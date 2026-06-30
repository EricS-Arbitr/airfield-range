# Splunk Role

**NOTICE - This role needs to be updated to pull required packages from Nexus and not the /var/share directory on the ansible server.**

## Description
Installs and configures Splunk Enterprise on Linux systems as the central log collection and analysis platform for the cyber range. The role deploys Splunk from a Debian package, applies licensing, creates indices, configures system settings, and establishes user accounts for security monitoring and analysis.

## Variable Definition Location
Variables for this role are defined in:
- **host_vars/[hostname].yml** for Splunk server-specific configuration
- **group_vars/all.yml** for installer paths and staging directories

## Required Variables

### In host_vars/[hostname].yml

| Variable | Required | Description |
|----------|----------|-------------|
| hostname | Yes | Hostname for the Splunk server |
| splunk_admin | Yes | Splunk admin username |
| splunk_admin_password | Yes | Splunk admin password |
| ram | Yes | Amount of RAM (used for tuning) |
| cpu | Yes | Number of CPUs (used for tuning) |
| indices | Yes | List of indices to create |

### In group_vars/all.yml

| Variable | Required | Description |
|----------|----------|-------------|
| ansible_installers | Yes | Base path for installer files |
| staging_dir_linux | Yes | Linux staging directory for temporary files |

## Optional Variables

### In host_vars/[hostname].yml

| Variable | Description |
|----------|-------------|
| users | List of additional Splunk users to create |
| admin_users | List of admin users to create |

## Complete Example Configuration

### host_vars/site-splunk.yml
```yaml
ansible_host: 10.10.2.20
hostname: "site-splunk"
splunk_admin: "admin"
splunk_admin_password: "simspace1"
ram: 16
cpu: 16

users:
  - name: "analyst"
    password: "simspace1"
  - name: "analyst2"
    password: "simspace1"

indices:
  - name: "windows"
  - name: "linux"
  - name: "proxy"
  - name: "zeek"
  - name: "onion"
  - name: "vyatta"
  - name: "sysmon"
  - name: "email"
  - name: "smtp"
  - name: "syslog"

admin_users:
  - name: "admin"
    password: "simspace1"
  - name: "admin2"
    password: "simspace1"
```
group_vars/all.yml
```yaml
ansible_installers: "/var/share/installers"
staging_dir_linux: "/var/tmp"
