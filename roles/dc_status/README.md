# DC_Status Role

## Description
Verifies that Active Directory services are fully operational after domain controller promotion. The role waits for the NTDS (NT Directory Services) service to be running and ensures the domain controller is ready to service authentication and directory requests.

## Variable Definition Location
This role requires no variables - it checks the status of Windows Active Directory services using built-in commands.

## Required Variables
None - this role uses only Windows service monitoring commands.

## Service Verification

The role monitors and verifies the following:

| Service | Name | Description |
|---------|------|-------------|
| NTDS | NT Directory Services | Core Active Directory database service |

## Implementation Details

The role performs two main tasks:

1. **Wait for Active Directory** - Polls the NTDS service status in a loop until it reports as 'Running'
   - Retries up to 30 times
   - 10 second delay between attempts
   - Total wait time up to 5 minutes

2. **Ensure Service Started** - Explicitly starts the NTDS service if not already running
   - Uses Windows service management
   - Ensures service is in started state

## Usage in Playbook

The role is typically executed after domain controller promotion:
```yaml
- name: dcpromo
  hosts: pdc
  gather_facts: false
  roles:
    - dcpromo
  tags:
    - dcpromo

- name: dc_status
  hosts: pdc
  gather_facts: false
  roles:
    - dc_status
  tags:
    - dc_status
```
