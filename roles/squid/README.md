# Squid Role

## Description
Installs and configures Squid proxy server along with Apache web server for WPAD (Web Proxy Auto-Discovery) support on Linux systems. The role sets up Squid as a forward proxy, configures Apache to serve the WPAD file, and applies necessary proxy settings for apt package management.

## Variable Definition Location
Variables for this role are defined in:
- **group_vars/all.yml** for upstream proxy configuration
- **group_vars/[domain].yml** for domain-specific proxy settings

## Required Variables

### In group_vars/all.yml

| Variable | Required | Description |
|----------|----------|-------------|
| inet_proxy_addr | Yes | IP address of the upstream proxy server |
| inet_proxy_port | Yes | Port number for the upstream proxy server |

### In group_vars/[domain].yml

| Variable | Required | Description |
|----------|----------|-------------|
| domain_name | Yes | Domain name for WPAD configuration |
| inventory_hostname | Yes | Hostname for Apache server configuration (automatically available) |

## Optional Variables

### In group_vars/[domain].yml

| Variable | Description |
|----------|-------------|
| proxy_server | IP address of the proxy server for WPAD configuration |

## Complete Example Configuration

### group_vars/all.yml
```yaml
# Upstream proxy configuration
inet_proxy_addr: "10.255.240.1"
inet_proxy_port: "3128"
```
group_vars/site.yml
```yaml
domain_name: "site.com"
proxy_server: "172.16.2.6"
```
host_vars/site-proxy.yml
```yaml
ansible_host: 10.10.2.6
network_interfaces:
  - name: "eth0"
    ipv4:
      type: "ethernet"
      address: "10.10.2.6"
      netmask: "255.255.0.0"
  - name: "eth1"
    ipv4:
      type: "ethernet"
      address: "172.16.2.6"
      netmask: "255.255.255.0"
      gateway: "172.16.2.1"
    dns:
      - "8.8.8.8"
      - "172.16.2.7"
