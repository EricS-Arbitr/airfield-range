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
# 3. Active Directory — vcab.lan + flightops.lan
# =========================================================================
section "3. Active Directory"

# simspace in Domain Admins on each forest
for dc in bs-dc01:vcab.lan fops-dc01:flightops.lan; do
  host="${dc%%:*}"; forest="${dc##*:}"
  check_ps "$host" \
    'Get-ADGroupMember "Domain Admins" | Where-Object { $_.Name -eq "simspace" } | Select-Object -ExpandProperty Name' \
    '\(stdout\)[[:space:]]+simspace' \
    "$forest: simspace is in Domain Admins"
done

# Both additional DCs promoted (PartOfDomain == True)
for adc in bs-dc02 fops-dc02; do
  check_ps "$adc" \
    '(Get-WmiObject Win32_ComputerSystem).PartOfDomain' \
    '\(stdout\)[[:space:]]+True' \
    "$adc: PartOfDomain True (additional DC promoted)"
done

# Cross-forest trust — outgoing on vcab, incoming on flightops
check_ps bs-dc01 \
  'Get-ADTrust -Filter * | Select-Object -ExpandProperty Target' \
  '\(stdout\)[^|]*flightops\.lan' \
  "Cross-forest trust on vcab side (outgoing to flightops.lan)"

check_ps fops-dc01 \
  'Get-ADTrust -Filter * | Select-Object -ExpandProperty Target' \
  '\(stdout\)[^|]*vcab\.lan' \
  "Cross-forest trust on flightops side (incoming from vcab.lan)"

# Member join counts (each host echoes True/False to its stdout)
for grp in members_vcab members_flightops; do
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

for dc in bs-dc01:vcab.lan fops-dc01:flightops.lan; do
  host="${dc%%:*}"; forest="${dc##*:}"
  check_ps "$host" \
    'Get-GPO -All | Where-Object { $_.DisplayName -eq "Mapped Network Drives" } | Select-Object -ExpandProperty DisplayName' \
    '\(stdout\)[[:space:]]+Mapped Network Drives' \
    "$forest: 'Mapped Network Drives' GPO exists"
done

for fs in bs-file01:vcab.lan fops-file01:flightops.lan; do
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
check_pf_shell soc-splunk \
  'systemctl is-active splunk' \
  'active' \
  "soc-splunk Splunk indexer service active"

check_pf_shell soc-splunk \
  'ss -lnt | awk "{print \$4}"' \
  ':9997$|:9997[[:space:]]' \
  "soc-splunk listening on :9997 (receiver — UF target)"

check_pf_shell soc-splunk \
  'ss -lnt | awk "{print \$4}"' \
  ':8000$|:8000[[:space:]]' \
  "soc-splunk listening on :8000 (Splunk Web)"

# soc-syslog forwarder up + has an ESTABLISHED conn to the indexer on 9997.
# Catches "service running but indexer unreachable" silently-broken state.
check_pf_shell soc-syslog \
  'systemctl is-active SplunkForwarder' \
  'active' \
  "soc-syslog SplunkForwarder service active"

check_pf_shell soc-syslog \
  'ss -ant | grep "172.31.7.19:9997" | grep -c ESTAB' \
  '^[1-9]' \
  "soc-syslog UF has ESTABLISHED connection to indexer :9997"

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
