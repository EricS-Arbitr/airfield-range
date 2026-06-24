# VyOS Role

## Description
Configures VyOS routers with network interfaces, routing protocols (OSPF, BGP), static routes, and NAT for cyber range environments.

## Variable Definition Location
Variables for this role should be defined in the **host_vars file** for each VyOS router (e.g., `host_vars/site-rtr.yml`).

## Required Variables

### network_interfaces
List of network interfaces to configure on the router.

| Field | Required | Description |
|-------|----------|-------------|
| name | Yes | Interface name (e.g., eth0, eth1) |
| ipv4.address | Yes | IPv4 address or "dhcp" |
| ipv4.netmask | Yes (if not DHCP) | Subnet mask in dotted decimal notation |
| ipv4.ospf | No | Set to `true` to enable OSPF on this interface |

## Optional Variables

### static_route
Static routes to configure on the router.

| Field | Required | Description |
|-------|----------|-------------|
| route | Yes | Destination network in CIDR notation |
| next_hop | Yes | Next hop IP address |
| redistribute | No | Redistribute static route into OSPF Value "ospf" |

### stand alone static_route
Used to always redistribute a default route into OSPF, used without static routes values, this should only be used on a single router in an OSPF area if needed

| Field | Required | Description |
|-------|----------|-------------|
| originate | Yes | "true" |

| always | No | Set to `true` to advertise even without default route |

### bgp
BGP configuration including redistribution settings.

| Field | Required | Description |
|-------|----------|-------------|
| as | Yes | Local AS number |
| neighbor.ip | Yes | Neighbor IP address |
| neighbor.as | Yes | Neighbor AS number |

Note: BGP automatically configures:
- `redistribute connected` - Advertise directly connected networks
- `redistribute static` - Advertise static routes
- Access list 100 for filtering advertisements

### source_nat
Source NAT (masquerade) rules for outbound traffic.

| Field | Required | Description |
|-------|----------|-------------|
| rule | Yes | Rule number (10, 20, etc.) |
| outbound_interface | Yes | Outbound interface (e.g., eth0) |
| source_address | No | Source network to NAT (default: all) |
| translation_address | No | Translation type ("masquerade" or specific IP) |

### destination_nat
Destination NAT rules for inbound traffic (port forwarding).

| Field | Required | Description |
|-------|----------|-------------|
| rule | Yes | Rule number |
| inbound_interface | Yes | Inbound interface |
| protocol | No | Protocol (tcp, udp, all) |
| destination_port | No | Destination port to forward |
| translation_address | Yes | Internal IP to forward to |
| translation_port | No | Internal port (if different) |


## Complete Example Configuration

### host_vars/site-rtr.yml
```yaml
ansible_host: 10.10.0.1

# Network Interfaces Configuration
network_interfaces:
  # WAN/Internet interface
  - name: "eth0"
    ipv4:
      address: "dhcp"
      
  # LAN interface with OSPF
  - name: "eth1"
    ipv4:
      address: "172.16.1.1"
      netmask: "255.255.255.0"
      ospf: true
      
  # DMZ interface with OSPF
  - name: "eth2"
    ipv4:
      address: "192.168.1.1"
      netmask: "255.255.255.0"
      ospf: true
      
  # Management interface
  - name: "eth3"
    ipv4:
      address: "10.10.0.1"
      netmask: "255.255.0.0"

# Static Routes Configuration
static_route:
  - route: "192.168.100.0/24"
    next_hop: "172.16.1.254"   
  - route: "0.0.0.0/0"  # Default route
    next_hop: "10.10.0.254"

# OSPF Default Route Origination
static_route:
  - route: "0.0.0.0/0"  # Default route
    next_hop: "10.10.0.254"
  redistribute: "ospf"

# OSPF Default Information Originat Always
static_route:
  originate: "true"

# BGP Configuration with Redistribution
bgp:
  - as: 65001
    neighbor:
      ip: "10.10.0.254"
      as: 65002
    # Note: redistribute static and connected are automatically configured

# Source NAT Configuration (Masquerade)
source_nat:
  - rule: 10
    outbound_interface: "eth0"
    source_address: "172.16.1.0/24"
    translation_address: "masquerade"
    
  - rule: 20
    outbound_interface: "eth0"
    source_address: "192.168.1.0/24"
    translation_address: "masquerade"

# Destination NAT Configuration (Port Forwarding)
destination_nat:
  - rule: 10
    inbound_interface: "eth0"
    protocol: "tcp"
    destination_port: "80"
    translation_address: "172.16.1.10"
    translation_port: "80"
    
  - rule: 20
    inbound_interface: "eth0"
    protocol: "tcp"
    destination_port: "443"
    translation_address: "172.16.1.10"
    translation_port: "443"
    
  - rule: 30
    inbound_interface: "eth0"
    protocol: "tcp"
    destination_port: "3389"
    translation_address: "172.16.1.20"


