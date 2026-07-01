# Drainhole Role

## Description
Installs the drainhole network manager application and configures it to run at startup for all users. The application is downloaded from Nexus repository through the cyber range proxy and placed in the Windows Startup folder.

## Variable Definition Location
Variables for this role should be defined in:
- **group_vars/windows.yml** - Drainhole installer URL
- **group_vars/all.yml** - Proxy settings

## Required Variables

### In group_vars/windows.yml

| Variable | Required | Description |
|----------|----------|-------------|
| drainhole_installer | Yes | URL to drainhole executable in Nexus repository |

### In group_vars/all.yml

| Variable | Required | Description |
|----------|----------|-------------|
| inet_proxy_addr | Yes | Proxy server address for external communication |
| inet_proxy_port | Yes | Proxy server port |

## Complete Example Configuration

### group_vars/windows.yml
```yaml
drainhole_installer: "https://nexus.dev.ng.simspace.lan/repository/ng_raw/installers/drainhole/network_mgr.exe"
```
group_vars/all.yml
```yaml
inet_proxy_addr: "10.255.240.1"
inet_proxy_port: "3128"
