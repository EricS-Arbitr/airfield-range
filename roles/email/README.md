# email role

## Description

Ansible role for deploying and configuring the email container on the `is-inet` VM. Manages the Docker Compose service definition, provisions mailbox users, generates SSL certificates signed by the range root CA, and configures Dovecot and Postfix for one or more domains.

## Variable Definition Location

Variables for this role are defined in:
- **group_vars/all.yml** — `email_domains` list of FQDN mail domains
- **group_vars/[domain].yml** — `DomainUsers` list of mailbox accounts to provision

## Required Variables

### In group_vars/all.yml

| Variable | Required | Description |
|----------|----------|-------------|
| `email_domains` | Yes | List of domain objects where `name` is the full mail FQDN (e.g., `mail.site.com`). Drives cert generation, Postfix config, Dovecot SSL blocks, and webmail connection configs. |

### In group_vars/[domain].yml

| Variable | Required | Description |
|----------|----------|-------------|
| `DomainUsers` | Yes | List of users to provision as mailbox accounts. Each entry requires `name` and `password`. |

## Optional Variables

None — this role uses only the required variables listed above.

## Role Files

| File | Description |
|------|-------------|
| `files/root_ca.crt` | Range root CA certificate used to sign mail domain certs |
| `files/root_ca.key` | Range root CA private key used to sign mail domain certs |
| `files/add_users.sh` | Idempotent script that creates system users and sets passwords inside the email container |

## Host Volume Layout

All Ansible-managed config is written to `/opt/email/` on the host and bind-mounted into the container:

| Host Path | Container Path |
|-----------|----------------|
| `/opt/email/ssl/certs/<domain>.crt` | `/etc/ssl/certs/<domain>.crt` |
| `/opt/email/ssl/private/<domain>.key` | `/etc/ssl/private/<domain>.key` |
| `/opt/email/dovecot/10-ssl.conf` | `/etc/dovecot/conf.d/10-ssl.conf` |
| `/opt/email/webmail/domains/<domain>.ini` | `/var/www/html/data/_data_/_default_/domains/<domain>.ini` |
| `/opt/email/users.txt` | `/users.txt` |
| `/opt/email/add_users.sh` | `/add_users.sh` |

`/etc/postfix` is not volume-mounted. `main.cf` and `vmail_ssl.map` are managed inside the container via `docker_container_exec` with idempotency guards.

## Complete Example Configuration

### group_vars/all.yml
```yaml
email_domains:
  - name: "mail.site.com"
  - name: "mail.finco.com"
```

### group_vars/site.yml
```yaml
DomainUsers:
  - name: "charity.bowen"
    fullname: "Charity Bowen"
    password: "Simspace1!Simspace1!"
    groups:
      - Domain Admins
  - name: "ahmed.ortega"
    fullname: "Ahmed Ortega"
    password: "Simspace1!Simspace1!"
    groups:
      - Domain Admins
```

## Running the Role

```bash
ansible-playbook playbook.yaml --tags email
```
