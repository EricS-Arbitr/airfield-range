# Chrome Role

## Description
Downloads and installs Google Chrome browser on Windows systems. The role downloads the Chrome installer from a Nexus repository through the cyber range proxy and performs a silent installation.

## Variable Definition Location
Variables for this role should be defined in:
- **group_vars/windows.yml** - Chrome installer URL
- **group_vars/all.yml** - Proxy settings

## Required Variables

### In group_vars/windows.yml

| Variable | Required | Description |
|----------|----------|-------------|
| chrome_installer | Yes | URL to Chrome MSI installer in Nexus repository |

### In group_vars/all.yml

| Variable | Required | Description |
|----------|----------|-------------|
| inet_proxy_addr | Yes | Proxy server address for external communication |
| inet_proxy_port | Yes | Proxy server port |

## Complete Example Configuration

### group_vars/windows.yml
```yaml
chrome_installer: "https://nexus.dev.ng.simspace.lan/repository/ng_raw/installers/Google/Chrome/113.0.5672.93/googlechromestandaloneenterprise64.msi"
```
group_vars/all.yml
```yaml
inet_proxy_addr: "10.255.240.1"
inet_proxy_port: "3128"
