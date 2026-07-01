# AUE Agent Role

## Description
This role downloads and installs the aue agent.

## Variable Definition Location
Variables for this role are defined in:
- **group_vars/windows.yml** for the aue agent installer URL
- **group_vars/all.yml** for proxy configuration settings

## Required Variables

### In group_vars/windows.yml

| Variable | Required | Description |
|----------|----------|-------------|
| aue_agent | Yes | The name of the aue agent executable in the internal repository |
| aue_agent_installer | Yes | the full URL to the aue agent executable in the internal repository |

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
# aue agent installer and installer location
aue_agent: "aue-agent-0.4.2-setup-x86_64.exe"
aue_agent_installer: "https://nexus.dev.ng.simspace.lan/repository/ng_raw/installers/aue-agent/0.4.2/{{ aue_agent }}"
```
group_vars/all.yml
```yaml
# Proxy configuration
inet_proxy_addr: "10.255.240.1"
inet_proxy_port: "3128"
