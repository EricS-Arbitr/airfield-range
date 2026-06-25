# Create_Users Role

## Description
Creates Active Directory domain users and assigns them to specified groups. The role automatically disables password complexity requirements in the domain GPO to allow simpler passwords if needed. All users are created with passwords that never expire.

## Variable Definition Location
Variables for this role should be defined in **group_vars/[domain].yml** where [domain] matches your AD domain inventory group name (e.g., site.yml, inet.yml)

## Required Variables

### In group_vars/[domain].yml

#### DomainUsers
List of users to create in Active Directory.

| Field | Required | Description |
|-------|----------|-------------|
| name | Yes | Username (SAM account name) |
| password | Yes | User password |
| fullname | No | User's full display name |
| groups | No | List of AD groups to assign user to |

## Complete Example Configuration

### group_vars/site.yml
```yaml
DomainUsers:
  - name: "simspace"
    fullname: "simspace"
    password: "Simspace1!Simspace1!"
    groups:
      - "Domain Admins"
      
  - name: "charity.bowen"
    fullname: "Charity Bowen"
    password: "Simspace1!Simspace1!"
    groups:
      - "Domain Admins"
         
  - name: "bob.burke"
    fullname: "Bob Burke"
    password: "Simspace1!Simspace1!"
    
  - name: "lara.whitaker"
    fullname: "Lara Whitaker"
    password: "Simspace1!Simspace1!"
```
Minimal Configuration
group_vars/site.yml
```yaml
DomainUsers:
  - name: "testuser"
    password: "TestPass123!"
```
Users with Different Group Memberships
group_vars/site.yml
```yaml
DomainUsers:
  # Domain Administrator
  - name: "admin.user"
    fullname: "Admin User"
    password: "AdminPass123!"
    groups:
      - "Domain Admins"
      - "Enterprise Admins"
      
  # Help Desk User
  - name: "helpdesk.user"
    fullname: "Help Desk User"
    password: "HelpDesk123!"
    groups:
      - "Account Operators"
      - "Remote Desktop Users"
      
  # Regular User - No groups specified
  - name: "regular.user"
    fullname: "Regular User"
    password: "UserPass123!"
      
  # Service Account
  - name: "svc.backup"
    fullname: "Backup Service Account"
    password: "BackupSvc123!"
    groups:
      - "Backup Operators"
