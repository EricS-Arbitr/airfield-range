# Sysmon Role

## Description
Installs and configures Microsoft Sysinternals Sysmon for advanced Windows system monitoring and security event logging. The role downloads Sysmon through the corporate proxy, deploys a customized configuration file based on SwiftOnSecurity's sysmon-config, and creates the Sysmon64 service for continuous monitoring.

## Variable Definition Location
Variables for this role are defined in:
- **group_vars/windows.yml** for the Sysmon installer URL
- **group_vars/all.yml** for proxy configuration settings

## Required Variables

### In group_vars/windows.yml

| Variable | Required | Description |
|----------|----------|-------------|
| sysmon_installer | Yes | URL to the Sysmon executable in the internal repository |

### In group_vars/all.yml

| Variable | Required | Description |
|----------|----------|-------------|
| inet_proxy_addr | Yes | IP address of the proxy server |
| inet_proxy_port | Yes | Port number for the proxy server |

## Optional Variables

None - this role uses only the required variables listed above.

## Complete Example Configuration

### group_vars/windows.yml
```yaml
# Sysmon installer location
sysmon_installer: "https://nexus.dev.ng.simspace.lan/repository/ng_raw/installers/Microsoft/sysmon/sysmon64.exe"
```
group_vars/all.yml
```yaml
# Proxy configuration
inet_proxy_addr: "10.255.240.1"
inet_proxy_port: "3128"
