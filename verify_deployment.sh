#!/bin/bash
#
# verify_deployment.sh — read-only health check from the Ansible controller.
#
# Walks every tier deployed so far and confirms externally-visible state.
# Uses `ansible -m win_shell` / `vyos_command` / `shell` and greps each
# command's stdout for an expected literal — no JSON parsing, no value
# extraction, much less fragile than the first cut.
#
# Usage:
#   cd /etc/ansible && ./verify_deployment.sh           # summary
#   cd /etc/ansible && ./verify_deployment.sh -v        # show ansible
#                                                       # output for each fail
#
# Exit 0 if every check passes, 1 if any fails.

set -u

VERBOSE=0
case "${1:-}" in
  -v|--verbose) VERBOSE=1 ;;
  -h|--help)    sed -n '2,15p' "$0"; exit 0 ;;
esac

# --- colors --------------------------------------------------------------
if [ -t 1 ]; then
  G=$'\033[32m'; R=$'\033[31m'; Y=$'\033[33m'; B=$'\033[36m'; D=$'\033[2m'; N=$'\033[0m'
else
  G=''; R=''; Y=''; B=''; D=''; N=''
fi

PASS=0
FAIL=0
declare -a FAILURES

pass()    { printf "  ${G}✓${N} %s\n" "$1"; PASS=$((PASS+1)); }
fail() {
  printf "  ${R}✗${N} %s\n" "$1"
  FAIL=$((FAIL+1))
  FAILURES+=("$1")
  if [ "$VERBOSE" -eq 1 ] && [ -n "${2:-}" ]; then
    # collapse any output we got, indent
    printf "      ${D}%s${N}\n" "$2" | head -5
  fi
}
section() { printf "\n${B}━━ %s ━━${N}\n" "$1"; }
note()    { printf "  ${D}%s${N}\n" "$1"; }

A() { ansible "$@" 2>&1; }

n_hosts() {
  ansible "$1" --list-hosts 2>/dev/null | tail -n +2 | sed '/^$/d' | wc -l | tr -d ' '
}

# One reachability probe per group: pass if every host gets back SUCCESS|CHANGED.
# Takes (group, module, command-string-or-empty, label).
probe_group() {
  local group="$1" module="$2" cmd="$3" label="$4"
  local total ok out
  total=$(n_hosts "$group")
  if [ "$total" -eq 0 ]; then
    note "$label: 0 hosts in inventory (skipping)"
    return
  fi
  if [ -n "$cmd" ]; then
    out=$(A "$group" -m "$module" -a "$cmd" --one-line)
  else
    out=$(A "$group" -m "$module" --one-line)
  fi
  ok=$(echo "$out" | grep -cE '\| (SUCCESS|CHANGED)')
  if [ "$ok" -eq "$total" ]; then
    pass "$label: $ok/$total reachable"
  else
    fail "$label: $ok/$total reachable" "$out"
  fi
}

# Probe one Windows host with PowerShell via win_shell (stdout-based check).
# Takes (host, ps-command, expected-grep-pattern, label).
check_ps() {
  local host="$1" ps="$2" expect="$3" label="$4"
  local out
  out=$(A "$host" -m ansible.windows.win_shell -a "$ps" --one-line)
  if echo "$out" | grep -qE "$expect"; then
    pass "$label"
  else
    fail "$label" "$out"
  fi
}

# Probe one VyOS router with a vyos_command, grep stdout for pattern.
check_vyos() {
  local host="$1" cmd="$2" expect="$3" label="$4"
  local out
  out=$(A "$host" -m vyos.vyos.vyos_command -a "commands=\"$cmd\"" --one-line)
  if echo "$out" | grep -qE "$expect"; then
    pass "$label"
  else
    fail "$label" "$out"
  fi
}

# Probe one pfSense with a shell command, grep stdout.
check_pf_shell() {
  local host="$1" cmd="$2" expect="$3" label="$4"
  local out
  out=$(A "$host" -m ansible.builtin.shell -a "$cmd" --one-line)
  if echo "$out" | grep -qE "$expect"; then
    pass "$label"
  else
    fail "$label" "$out"
  fi
}

# Count Windows hosts in a group that satisfy a PowerShell predicate.
# The PS one-liner should print a single token per host that grep can match.
count_ps_predicate() {
  local group="$1" ps="$2" expect="$3"
  A "$group" -m ansible.windows.win_shell -a "$ps" --one-line \
    | grep -cE "$expect"
}

# =========================================================================
# 1. Inventory reachability
# =========================================================================
section "1. Inventory reachability"

probe_group vyos    vyos.vyos.vyos_facts  ""                "VyOS routers (network_cli)"
probe_group pfsense ansible.builtin.shell "echo ok"         "pfSense firewalls (ssh)"
probe_group linux   ansible.builtin.ping  ""                "Linux hosts (ssh)"
probe_group windows ansible.windows.win_ping ""             "Windows hosts (winrm)"

# =========================================================================
# 2. Network — routing convergence
# =========================================================================
section "2. Network — routing convergence"

check_pf_shell bs-ops-fw \
  'ifconfig vmx1 | awk "/inet /{print \$2; exit}"' \
  '172\.31\.1\.14' \
  "bs-ops-fw vmx1 (SWITCH_3) bound to 172.31.1.14"

check_pf_shell bs-ops-fw \
  'netstat -rn -f inet | awk "/^172.31.2.0/"' \
  '172\.31\.1\.13' \
  "bs-ops-fw kernel FIB has 172.31.2.0/24 via 172.31.1.13 (vmx1)"

# FRR-RIB vs kernel-FIB divergence check -- explicitly catches the
# dhclient-poisoning failure mode (UPSTREAM_FIXES.md 2026-06-30) where FRR
# reports `O>*` for a route (its "installed" marker) but the kernel FIB
# doesn't actually have it. Compare the two views for 172.31.2.0/24
# (the blackstone-DC-reachability route from behind bs-ops-fw). Divergence here
# would break Eng + SOC domain joins.
check_pf_shell bs-ops-fw \
  'frr_installed=$(vtysh -c "show ip route" 2>/dev/null | grep "172.31.2.0/24" | grep -c ">"); kernel_has=$(netstat -rn -f inet 2>/dev/null | grep -c "^172.31.2.0"); if [ "$frr_installed" -ge 1 ] && [ "$kernel_has" -ge 1 ]; then echo "OK_MATCH frr=$frr_installed kernel=$kernel_has"; elif [ "$frr_installed" -ge 1 ] && [ "$kernel_has" -eq 0 ]; then echo "DIVERGENCE frr=$frr_installed kernel=0 (dhclient poisoning? see UPSTREAM_FIXES.md 2026-06-30)"; else echo "NO_ROUTE frr=$frr_installed kernel=$kernel_has"; fi' \
  'OK_MATCH' \
  "bs-ops-fw FRR-RIB and kernel-FIB agree on 172.31.2.0/24 (no zebra poisoning)"

for rtr in bs-edge-rtr bs-core-rtr bs-ops-rtr bs-sec-rtr bs-modbus-gateway; do
  check_vyos "$rtr" \
    "show ip route 0.0.0.0/0" \
    'static|S\\*|S>|ospf|O>' \
    "$rtr default route present"
done

for fw in bs-edge-fw bs-ops-fw; do
  check_pf_shell "$fw" \
    'vtysh -c "show ip ospf neighbor"' \
    'Full/' \
    "$fw OSPF: at least one Full neighbor"
done

check_pf_shell bs-edge-fw \
  'vtysh -c "show ip bgp summary"' \
  'Establ|\(Policy\)' \
  "bs-edge-fw eBGP session Established to bs-edge-rtr"

# =========================================================================
# 3. Active Directory — blackstone.mil + fops.blackstone.mil
# =========================================================================
section "3. Active Directory"

# simspace in Domain Admins on each forest
for dc in bs-dc01:blackstone.mil fops-dc01:fops.blackstone.mil; do
  host="${dc%%:*}"; forest="${dc##*:}"
  check_ps "$host" \
    'Get-ADGroupMember "Domain Admins" | Where-Object { $_.Name -eq "simspace" } | Select-Object -ExpandProperty Name' \
    '\(stdout\)[[:space:]]+simspace' \
    "$forest: simspace is in Domain Admins"
done

# Per-workstation domain users (one per Windows workstation, all in Domain
# Admins -- see group_vars/{blackstone,fops}.yml DomainUsers lists).
# blackstone.mil expects >= 27 user members (simspace + 26 named workstation users);
# fops.blackstone.mil expects >= 13 (simspace + 12 named). The builtin Administrator
# is typically also a DA member -- the floor checks catch a partial create_users
# run without false-failing on count drift.
check_ps bs-dc01 \
  '$c=(Get-ADGroupMember "Domain Admins" -Recursive | Where-Object {$_.objectClass -eq "user"}).Count; if ($c -ge 27) {"OK_$c"} else {"LOW_$c"}' \
  '\(stdout\)[[:space:]]+OK_' \
  "blackstone.mil: >= 27 named users in Domain Admins (simspace + 26 workstation users)"

check_ps fops-dc01 \
  '$c=(Get-ADGroupMember "Domain Admins" -Recursive | Where-Object {$_.objectClass -eq "user"}).Count; if ($c -ge 13) {"OK_$c"} else {"LOW_$c"}' \
  '\(stdout\)[[:space:]]+OK_' \
  "fops.blackstone.mil: >= 13 named users in Domain Admins (simspace + 12 workstation users)"

# Spot-check one specific named user exists + enabled on each domain.
# ahmed.ortega is a PowerPlant-roster name (validates the main path);
# emma.rodriguez is one of the 5 names new to airfield-range (validates
# the new-names branch in fops.yml).
check_ps bs-dc01 \
  'try { (Get-ADUser ahmed.ortega -Properties Enabled).Enabled } catch { "MISSING" }' \
  '\(stdout\)[[:space:]]+True' \
  "blackstone.mil: ahmed.ortega exists and is enabled (PowerPlant-roster user)"

check_ps fops-dc01 \
  'try { (Get-ADUser emma.rodriguez -Properties Enabled).Enabled } catch { "MISSING" }' \
  '\(stdout\)[[:space:]]+True' \
  "fops.blackstone.mil: emma.rodriguez exists and is enabled (new airfield-range user)"

# Both additional DCs promoted (PartOfDomain == True)
for adc in bs-dc02 fops-dc02; do
  check_ps "$adc" \
    '(Get-WmiObject Win32_ComputerSystem).PartOfDomain' \
    '\(stdout\)[[:space:]]+True' \
    "$adc: PartOfDomain True (additional DC promoted)"
done

# Parent/child trust — automatic in a single AD forest. Query Get-ADTrust
# from either side; expect the OTHER domain listed as a `ParentChild` trust
# (direction: BiDirectional). No shared secret involved.
check_ps bs-dc01 \
  '(Get-ADTrust -Filter * | Where-Object {$_.Target -eq "fops.blackstone.mil"}).TrustType' \
  '\(stdout\)[[:space:]]+(ParentChild|4)' \
  "blackstone.mil forest root sees fops child domain via ParentChild trust"

check_ps fops-dc01 \
  '(Get-ADTrust -Filter * | Where-Object {$_.Target -eq "blackstone.mil"}).TrustType' \
  '\(stdout\)[[:space:]]+(ParentChild|4)' \
  "fops.blackstone.mil child sees blackstone.mil parent via ParentChild trust"

# Member join counts (each host echoes True/False to its stdout)
for grp in members_blackstone members_fops; do
  total=$(n_hosts "$grp")
  joined=$(count_ps_predicate "$grp" \
    '(Get-WmiObject Win32_ComputerSystem).PartOfDomain' \
    '\(stdout\)[[:space:]]+True')
  if [ "$joined" -eq "$total" ] && [ "$total" -gt 0 ]; then
    pass "$grp: $joined/$total hosts domain-joined"
  else
    fail "$grp: $joined/$total hosts domain-joined"
  fi
done

# DNS forwarders set on each PDC
for dc in bs-dc01 fops-dc01; do
  check_ps "$dc" \
    '(Get-DnsServerForwarder).IPAddress.IPAddressToString -join ","' \
    '8\.8\.8\.8.*1\.1\.1\.1|1\.1\.1\.1.*8\.8\.8\.8' \
    "$dc DNS forwarders → is-inet aliases (8.8.8.8 / 8.8.4.4 / 1.1.1.1)"
done

# =========================================================================
# 4. File services
# =========================================================================
section "4. File services"

for dc in bs-dc01:blackstone.mil fops-dc01:fops.blackstone.mil; do
  host="${dc%%:*}"; forest="${dc##*:}"
  check_ps "$host" \
    'Get-GPO -All | Where-Object { $_.DisplayName -eq "Mapped Network Drives" } | Select-Object -ExpandProperty DisplayName' \
    '\(stdout\)[[:space:]]+Mapped Network Drives' \
    "$forest: 'Mapped Network Drives' GPO exists"
done

for fs in bs-file01:blackstone.mil fops-file01:fops.blackstone.mil; do
  host="${fs%%:*}"; forest="${fs##*:}"
  check_ps "$host" \
    'Get-SmbShare -Name "Share" -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Name' \
    '\(stdout\)[[:space:]]+Share' \
    "$forest: \\\\$host.$forest\\Share is exposed"
done

# =========================================================================
# 5. SOC tier — syslog collector
# =========================================================================
section "5. SOC tier — syslog collector"

# rsyslog collector listens on UDP+TCP 514 (per syslog_server role).
check_pf_shell soc-syslog \
  'ss -lnu | grep -qE ":514\\b" && ss -lnt | grep -qE ":514\\b" && echo LISTENERS_OK || echo LISTENERS_MISSING' \
  'LISTENERS_OK' \
  "soc-syslog listening on UDP+TCP 514"

# Each pfSense firewall has a per-host log file under /var/log/remote/, and
# that file was written to within the last 5 minutes. "Stale or missing"
# would mean either rsyslog stopped routing pfSense messages correctly or
# pfSense lost its remote-syslog config.
for fw in bs-edge-fw bs-ops-fw; do
  check_pf_shell soc-syslog \
    "test -f /var/log/remote/$fw/syslog.log && age=\$((\$(date +%s) - \$(stat -c %Y /var/log/remote/$fw/syslog.log))) && [ \$age -lt 300 ] && echo OK_FRESH || echo STALE_OR_MISSING" \
    'OK_FRESH' \
    "soc-syslog receiving from $fw (log mtime <5min)"
done

# =========================================================================
# 6. SOC tier — Splunk SIEM
# =========================================================================
section "6. SOC tier — Splunk SIEM"

# Indexer service active and listening on receiver (9997) + web (8000) + REST (8089).
# Pattern: emit a sentinel from the remote shell then grep for it -- avoids
# the trap where `ss` multi-line output is joined with literal "\n" in
# ansible's --one-line format, defeating ^/$ anchors in the controller regex.
check_pf_shell soc-splunk \
  'systemctl is-active splunk' \
  'active' \
  "soc-splunk Splunk indexer service active"

check_pf_shell soc-splunk \
  'ss -lnt | grep -qE ":9997\\b" && echo OK_9997 || echo MISSING_9997' \
  'OK_9997' \
  "soc-splunk listening on :9997 (receiver — UF target)"

check_pf_shell soc-splunk \
  'ss -lnt | grep -qE ":8000\\b" && echo OK_8000 || echo MISSING_8000' \
  'OK_8000' \
  "soc-splunk listening on :8000 (Splunk Web)"

# soc-syslog forwarder up + has an ESTABLISHED conn to the indexer on 9997.
# Catches "service running but indexer unreachable" silently-broken state.
check_pf_shell soc-syslog \
  'systemctl is-active SplunkForwarder' \
  'active' \
  "soc-syslog SplunkForwarder service active"

check_pf_shell soc-syslog \
  'c=$(ss -ant | grep "172.31.7.19:9997" | grep -c ESTAB); [ "$c" -ge 1 ] && echo OK_ESTAB || echo NO_ESTAB' \
  'OK_ESTAB' \
  "soc-syslog UF has ESTABLISHED connection to indexer :9997"

# Total forwarder count on soc-splunk. Expected ≥ 30 once the Windows UF
# rollout is done (Linux UFs alone give us ~10; Windows UFs push us to
# 50+). Threshold set at 30 to prove Windows UF landed successfully.
# LOW_UFS_<n> below 30 usually means the Windows UF play never ran or the
# MSI install failed on most hosts.
check_pf_shell soc-splunk \
  'c=$(ss -ant | grep ":9997 " | grep -c ESTAB); [ "$c" -ge 30 ] && echo "OK_UFS_$c" || echo "LOW_UFS_$c"' \
  'OK_UFS_' \
  "soc-splunk: ≥ 30 UFs ESTABLISHED on :9997 (Linux + Windows rollout done)"

# Windows UF service spot checks — one per domain. Catches "MSI installed
# but service failed to start" which the ESTAB count wouldn't distinguish
# from "host offline."
check_ps bs-hq01 \
  '(Get-Service SplunkForwarder -ErrorAction SilentlyContinue).Status' \
  '\(stdout\)[[:space:]]+Running' \
  "bs-hq01 (blackstone): SplunkForwarder service running"

check_ps fops-ops01 \
  '(Get-Service SplunkForwarder -ErrorAction SilentlyContinue).Status' \
  '\(stdout\)[[:space:]]+Running' \
  "fops-ops01 (fops): SplunkForwarder service running"

# Sysmon service spot checks — proves the sysmon role landed the config +
# started Sysmon64 service. Sysmon events land in index=sysmon via the UF's
# templates/inputs.conf Microsoft-Windows-Sysmon/Operational stanza.
check_ps bs-hq01 \
  '(Get-Service Sysmon64 -ErrorAction SilentlyContinue).Status' \
  '\(stdout\)[[:space:]]+Running' \
  "bs-hq01 (blackstone): Sysmon64 service running"

check_ps fops-ops01 \
  '(Get-Service Sysmon64 -ErrorAction SilentlyContinue).Status' \
  '\(stdout\)[[:space:]]+Running' \
  "fops-ops01 (fops): Sysmon64 service running"

# =========================================================================
# 7. Enterprise services — root certs, AUE lockdown, autologin, squid, global_dns
# =========================================================================
section "7. Enterprise services"

# root_certs role installed the SimSpace lab-CA (root_ca.crt) into every
# Windows host's Trusted Root store. Spot-check on bs-hq01.
check_ps bs-hq01 \
  'if (Get-ChildItem Cert:\LocalMachine\Root -ErrorAction SilentlyContinue | Where-Object {$_.Subject -match "SimSpace|root_ca|simspace"}) { "PRESENT" } else { "MISSING" }' \
  '\(stdout\)[[:space:]]+PRESENT' \
  "bs-hq01 (blackstone): SimSpace root CA installed in Trusted Root store"

# AUE lockdown -- disable_uac role sets EnableLUA=0
check_ps bs-hq01 \
  '(Get-ItemProperty -Path "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\System" -ErrorAction SilentlyContinue).EnableLUA' \
  '\(stdout\)[[:space:]]+0' \
  "bs-hq01 (blackstone): UAC disabled (proves disable_uac / AUE lockdown ran)"

# autologin role sets DefaultUserName in Winlogon to the host's logon_user
# (host_vars mapping: bs-hq01 -> ahmed.ortega).
check_ps bs-hq01 \
  '(Get-ItemProperty -Path "HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Winlogon" -ErrorAction SilentlyContinue).DefaultUserName' \
  '\(stdout\)[[:space:]]+ahmed\.ortega' \
  "bs-hq01 (blackstone): autologin configured for ahmed.ortega"

# Chrome role installed the browser. Test-Path is more portable than
# Get-ItemProperty for install detection.
check_ps bs-hq01 \
  'if (Test-Path "C:\Program Files\Google\Chrome\Application\chrome.exe") { "INSTALLED" } elseif (Test-Path "C:\Program Files (x86)\Google\Chrome\Application\chrome.exe") { "INSTALLED" } else { "MISSING" }' \
  '\(stdout\)[[:space:]]+INSTALLED' \
  "bs-hq01 (blackstone): Chrome installed (proves chrome / AUE ran)"

# bs-proxy — squid service active + listening on :3128
check_pf_shell bs-proxy \
  'systemctl is-active squid' \
  'active' \
  "bs-proxy: squid service active"

check_pf_shell bs-proxy \
  'ss -lnt | grep -qE ":3128\\b" && echo OK_3128 || echo MISSING_3128' \
  'OK_3128' \
  "bs-proxy: listening on :3128 (squid HTTP proxy)"

# is-inet global_dns -- unbound should resolve www.faa.gov to the value
# in group_vars/all.yml global_dns_records (70.39.65.10). Resolves via
# soc-syslog's configured DNS (blackstone DCs -> 8.8.8.8 forwarder alias ->
# is-inet unbound). Fall back to getent (nss) if dig isn't installed.
check_pf_shell soc-syslog \
  'r=$(getent hosts www.faa.gov 2>/dev/null | awk "{print \$1}"); [ "$r" = "70.39.65.10" ] && echo "OK_$r" || echo "GOT_$r"' \
  'OK_70\.39\.65\.10' \
  "is-inet: unbound resolves www.faa.gov -> 70.39.65.10 (global_dns loaded)"

# =========================================================================
# 8. Public web + email (Blackstone rebrand)
# =========================================================================
section "8. Public web + email"

# bs-www serves the blackstone_www role's index.html. Reachable via the
# DMZ IP directly (172.31.12.3) or via bs-edge-fw's NAT reflection at
# 199.252.163.1 (which internal test clients can't easily reach, so we
# probe the DMZ IP for a smoke test).
check_pf_shell bs-www \
  'systemctl is-active nginx' \
  'active' \
  "bs-www: nginx service active"

check_pf_shell bs-www \
  'curl -s -o /dev/null -w "%{http_code}" http://localhost/' \
  '^200$|200' \
  "bs-www: landing page returns HTTP 200"

# is-inet unbound has the apex A records for both mail domains + bare
# blackstone.mil. If any of these are missing, mail login and public
# name resolution break.
check_pf_shell soc-syslog \
  'r=$(getent hosts blackstone.mil 2>/dev/null | awk "{print \$1}"); [ "$r" = "52.96.223.2" ] && echo "OK_$r" || echo "GOT_$r"' \
  'OK_52\.96\.223\.2' \
  "is-inet: unbound resolves blackstone.mil apex -> 52.96.223.2"

check_pf_shell soc-syslog \
  'r=$(getent hosts fops.blackstone.mil 2>/dev/null | awk "{print \$1}"); [ "$r" = "52.96.223.2" ] && echo "OK_$r" || echo "GOT_$r"' \
  'OK_52\.96\.223\.2' \
  "is-inet: unbound resolves fops.blackstone.mil apex -> 52.96.223.2"

check_pf_shell soc-syslog \
  'r=$(getent hosts www.blackstone.mil 2>/dev/null | awk "{print \$1}"); [ "$r" = "199.252.163.1" ] && echo "OK_$r" || echo "GOT_$r"' \
  'OK_199\.252\.163\.1' \
  "is-inet: unbound resolves www.blackstone.mil -> 199.252.163.1 (bs-edge-fw WAN)"

# Email container up + Dovecot listening + our bob.burke test user exists.
check_pf_shell is-inet \
  'docker ps --filter name=email --format "{{.Status}}" 2>&1 | head -1' \
  'Up' \
  "is-inet: email container running"

check_pf_shell is-inet \
  'docker exec email getent passwd bob.burke 2>&1 | head -1' \
  'bob.burke' \
  "is-inet: bob.burke unix user exists in email container (mailbox provisioned)"

# =========================================================================
# Summary
# =========================================================================
section "Summary"

total=$((PASS+FAIL))
printf "  Total checks : %d\n" "$total"
printf "  ${G}Pass${N}         : %d\n" "$PASS"
printf "  ${R}Fail${N}         : %d\n" "$FAIL"

if [ "$FAIL" -gt 0 ]; then
  echo
  echo "${R}Failed checks:${N}"
  for f in "${FAILURES[@]}"; do echo "  • $f"; done
  if [ "$VERBOSE" -eq 0 ]; then
    echo
    echo "${D}Re-run with -v to see ansible's output for each failure.${N}"
  fi
  exit 1
fi

echo
printf "${G}All checks passed.${N}\n"
exit 0
