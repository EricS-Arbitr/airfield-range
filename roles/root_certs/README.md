# Root_Certs Role

## Description
Installs trusted root certificates on Windows systems. The role copies certificate files from the role's files directory to the target system and installs them into the Windows Trusted Root certificate store (LocalMachine).

## Variable Definition Location
This role requires no variables - it automatically processes all certificate files placed in the role's files directory.

## Required Variables
None - this role automatically processes certificate files placed in `roles/root_certs/files/`

## Certificate File Requirements
Place certificate files in `roles/root_certs/files/` with the following supported extensions:
- `.crt`
- `.cer`
- `.pem`

## Notes
- Certificates are installed to the LocalMachine Trusted Root store
- All certificate files matching the supported extensions are automatically processed
- The role creates a temporary directory (C:\temp) for certificate staging
- Certificates become trusted for all users on the system
