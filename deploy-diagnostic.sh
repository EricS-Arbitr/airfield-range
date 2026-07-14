#!/usr/bin/env bash
# deploy-diagnostic.sh -- run the airfield-range deploy ONCE with verbose
# output, no retry loop, and unconditionally collect post-deploy state
# from bs-dc01 (which stays reachable even when fops-dc01 falls over).
#
# Use this instead of ./deploy.sh when you want to CAPTURE a failure --
# deploy.sh's 3-attempt retry loop obscures the first attempt's real
# error by overwriting logs and re-running paths that have side effects.
#
# Log lands at /tmp/deploy-diag-<timestamp>.log
# Post-deploy state snapshot appended to same log.

set -o pipefail

TS=$(date +%Y%m%d-%H%M%S)
LOG="/tmp/deploy-diag-${TS}.log"

echo "=== DEPLOY-DIAGNOSTIC START $(date -Is) ===" | tee "$LOG"
echo "Log file: $LOG" | tee -a "$LOG"

# Refresh Galaxy collections (mirrors deploy.sh behavior)
echo "=== Installing/refreshing Ansible Galaxy collections ===" | tee -a "$LOG"
ansible-galaxy collection install -r /etc/ansible/requirements.yml 2>&1 | tee -a "$LOG" || true

# Single ansible-playbook invocation. -vv gives task-level detail without
# drowning us in per-connection chatter (-vvv adds that; use if needed).
echo "=== ansible-playbook (single attempt, no retry) $(date -Is) ===" | tee -a "$LOG"
sudo ansible-playbook /etc/ansible/site.yml -vv 2>&1 | tee -a "$LOG"
ANSIBLE_EXIT=$?
echo "=== ANSIBLE EXIT CODE: $ANSIBLE_EXIT ===" | tee -a "$LOG"

# Post-deploy state snapshot -- always runs, regardless of exit code.
# Delegates to bs-dc01 because fops-dc01 may be in a wedged auth state.
echo "" | tee -a "$LOG"
echo "=== POST-DEPLOY STATE SNAPSHOT $(date -Is) ===" | tee -a "$LOG"

ansible bs-dc01 -m ansible.windows.win_shell \
  -e 'ansible_user=Administrator' \
  -e 'ansible_password=Simspace1!Simspace1!' \
  -a '"--- forest domains ---"
      try { (Get-ADForest -ErrorAction Stop).Domains } catch { "ERROR: $($_.Exception.Message)" }
      "--- CrossRefs ---"
      try { Get-ADObject -SearchBase "CN=Partitions,CN=Configuration,DC=blackstone,DC=mil" -Filter * -Properties dnsRoot -ErrorAction Stop |
              Select Name,dnsRoot | Format-Table -AutoSize | Out-String } catch { "ERROR: $($_.Exception.Message)" }
      "--- DNS delegation ---"
      try { Get-DnsServerResourceRecord -ZoneName blackstone.mil -RRType NS -ErrorAction Stop |
              Select HostName | Format-Table | Out-String } catch { "ERROR: $($_.Exception.Message)" }
      "--- 172.31.3.11:389 up? ---"
      (Test-NetConnection 172.31.3.11 -Port 389 -InformationLevel Quiet -WarningAction SilentlyContinue)
      "--- 10.255.240.120:5985 (fops-dc01 WinRM) up? ---"
      (Test-NetConnection 10.255.240.120 -Port 5985 -InformationLevel Quiet -WarningAction SilentlyContinue)
      "--- fops-dc01 dcpromo-install log via SMB ---"
      try { Get-Content "\\172.31.3.11\c$\ProgramData\dcpromo-install.log" -ErrorAction Stop } catch { "ERROR: $($_.Exception.Message)" }' \
  2>&1 | tee -a "$LOG" || echo "!! bs-dc01 diagnostic failed (bs-dc01 also unreachable?)" | tee -a "$LOG"

echo "" | tee -a "$LOG"
echo "=== DEPLOY-DIAGNOSTIC END $(date -Is) ===" | tee -a "$LOG"
echo ""
echo "Full log: $LOG"
echo "Quick summary:"
echo "  grep -E '^PLAY \[|fatal:|UNREACHABLE|PROMOTION_(OK|FAILED|EXCEPTION)|DCPROMO_' $LOG"

exit $ANSIBLE_EXIT
