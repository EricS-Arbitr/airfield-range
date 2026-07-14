#!/usr/bin/env bash
# fetch-fops-log.sh -- retrieve fops-dc01's persistent Install-ADDSDomain
# log via bs-dc01's SMB access.
#
# Why: if dcpromo(child) hit UNREACHABLE mid-flight, fops-dc01's own WinRM
# may reject NTLM afterward. But the promotion task writes its output to
# C:\ProgramData\dcpromo-install.log on fops-dc01's disk, which is
# accessible from bs-dc01 via the \\172.31.3.11\c$ admin share as long as
# fops-dc01 booted (regardless of WinRM auth state).
#
# Prints the log to stdout. If it errors, that itself is a signal
# (fops-dc01 not booted, SMB port blocked, etc.).

exec ansible bs-dc01 -m ansible.windows.win_shell \
  -e 'ansible_user=Administrator' \
  -e 'ansible_password=Simspace1!Simspace1!' \
  -a 'try {
        Get-Content "\\172.31.3.11\c$\ProgramData\dcpromo-install.log" -ErrorAction Stop
      } catch {
        "ERROR reading fops-dc01 log via SMB: $($_.Exception.Message)"
        "Also trying: does fops-dc01 respond on SMB 445 at all?"
        (Test-NetConnection 172.31.3.11 -Port 445 -InformationLevel Detailed) | Out-String
      }'
