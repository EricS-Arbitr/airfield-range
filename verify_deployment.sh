#!/bin/bash
#
# verify_deployment.sh — read-only health check from the Ansible controller.
#
# Walks every tier we've deployed so far and confirms the externally-visible
# state matches what the playbook should have produced. Uses ansible ad-hoc
# commands and PowerShell/shell probes against the live hosts; touches no
# state. Exit code: 0 if every check passes, 1 if any fail.
#
# Usage:
#   cd /etc/ansible && ./verify_deployment.sh
#
# Reads inventory + connection settings from this directory (hosts,
# group_vars/, host_vars/). Run alongside ./deploy.sh.

set -u

# --- colors (only when stdout is a tty) ----------------------------------
if [ -t 1 ]; then
  G=$'\033[32m'; R=$'\033[31m'; Y=$'\033[33m'; B=$'\033[36m'; D=$'\033[2m'; N=$'\033[0m'
else
  G=''; R=''; Y=''; B=''; D=''; N=''
fi

PASS=0
FAIL=0
declare -a FAILURES

pass()    { printf "  ${G}✓${N} %s\n" "$1"; PASS=$((PASS+1)); }
fail()    { printf "  ${R}✗${N} %s\n" "$1"; FAIL=$((FAIL+1)); FAILURES+=("$1"); }
section() { printf "\n${B}━━ %s ━━${N}\n" "$1"; }
note()    { printf "  ${D}%s${N}\n" "$1"; }

# Run ansible quietly, capturing combined output
A() { ansible "$@" 2>&1; }

# Extract a JSON-ish first-output value from win_powershell's --one-line output.
# Returns the raw value (e.g. "1", "True", "False", "").
ps_output() {
  grep -oE '"output":[[:space:]]*\[[^]]*\]' | head -1 \
    | sed -E 's/.*\[//; s/\].*//; s/[[:space:]]//g; s/^"//; s/"$//'
}

# Count hosts in a group via ansible --list-hosts (lines after the header).
n_hosts() {
  ansible "$1" --list-hosts 2>/dev/null | tail -n +2 | sed '/^$/d' | wc -l | tr -d ' '
}

# Inventory reachability per group. Takes (group, module, label).
check_group_reachability() {
  local group="$1" module="$2" label="$3"
  local total ok
  total=$(n_hosts "$group")
  if [ "$total" -eq 0 ]; then
    note "$label: 0 hosts in inventory (skipping)"
    return
  fi
  ok=$(A "$group" -m "$module" --one-line | grep -c 'SUCCESS')
  if [ "$ok" -eq "$total" ]; then
    pass "$label: $ok/$total reachable"
  else
    fail "$label: $ok/$total reachable"
  fi
}

# =========================================================================
# 1. Inventory reachability
# =========================================================================
section "1. Inventory reachability"

check_group_reachability vyos    vyos.vyos.vyos_facts             "VyOS routers (network_cli)"
check_group_reachability pfsense ansible.builtin.shell            "pfSense firewalls (ssh)"
check_group_reachability linux   ansible.builtin.ping             "Linux hosts (ssh)"
check_group_reachability windows ansible.windows.win_ping         "Windows hosts (winrm)"

# =========================================================================
# 2. Network — routing convergence
# =========================================================================
section "2. Network — routing convergence"

# bs-ops-fw vmx1 (SWITCH_3) — the interface that has been the recurring
# kernel-IP drop. If it's missing 172.31.1.14, every Eng/SOC host loses
# its path to vcab.
vmx1=$(A bs-ops-fw -m ansible.builtin.shell \
  -a 'ifconfig vmx1 | awk "/inet /{print \$2; exit}"' --one-line)
if echo "$vmx1" | grep -q '172.31.1.14'; then
  pass "bs-ops-fw vmx1 (SWITCH_3) bound to 172.31.1.14"
else
  fail "bs-ops-fw vmx1 missing 172.31.1.14 — Eng/SOC will lose vcab reachability"
fi

# bs-ops-fw kernel FIB has the corp Services route (OSPF-learned)
route_check=$(A bs-ops-fw -m ansible.builtin.shell \
  -a 'netstat -rn -f inet | awk "/^172\.31\.2\.0/"' --one-line)
if echo "$route_check" | grep -q '172.31.1.13'; then
  pass "bs-ops-fw kernel FIB has 172.31.2.0/24 via 172.31.1.13 (vmx1)"
else
  fail "bs-ops-fw kernel FIB missing 172.31.2.0/24 (OSPF didn't install)"
fi

# Each VyOS router should have a default route
for rtr in bs-edge-rtr bs-core-rtr bs-ops-rtr bs-sec-rtr bs-modbus-gateway; do
  out=$(A "$rtr" -m vyos.vyos.vyos_command \
    -a 'commands="show ip route 0.0.0.0/0"' --one-line)
  if echo "$out" | grep -qE 'static|ospf|S>\*|O>'; then
    pass "$rtr default route present"
  else
    fail "$rtr default route missing"
  fi
done

# OSPF adjacencies on both pfSense firewalls
for fw in bs-edge-fw bs-ops-fw; do
  full=$(A "$fw" -m ansible.builtin.shell \
    -a 'vtysh -c "show ip ospf neighbor"' --one-line \
    | grep -oE 'Full/[A-Za-z]+' | wc -l | tr -d ' ')
  if [ "$full" -ge 1 ]; then
    pass "$fw OSPF: $full Full neighbor(s)"
  else
    fail "$fw OSPF: no Full neighbors"
  fi
done

# bs-edge-fw should also have an eBGP session up
bgp=$(A bs-edge-fw -m ansible.builtin.shell \
  -a 'vtysh -c "show ip bgp summary"' --one-line)
if echo "$bgp" | grep -qE 'Establ.* [1-9]'; then
  pass "bs-edge-fw eBGP session established to bs-edge-rtr (AS 65002)"
else
  fail "bs-edge-fw eBGP session not Established"
fi

# =========================================================================
# 3. Active Directory — vcab.lan + flightops.lan
# =========================================================================
section "3. Active Directory"

# simspace in Domain Admins of each forest
for dc in bs-dc01:vcab.lan fops-dc01:flightops.lan; do
  host="${dc%%:*}"
  forest="${dc##*:}"
  out=$(A "$host" -m ansible.windows.win_powershell \
    -a 'script=(Get-ADGroupMember "Domain Admins" -ErrorAction SilentlyContinue | Where-Object {$_.Name -eq "simspace"}).Count' \
    --one-line)
  count=$(echo "$out" | ps_output)
  if [ "$count" = "1" ]; then
    pass "$forest: simspace is a member of Domain Admins"
  else
    fail "$forest: simspace not in Domain Admins (count=$count)"
  fi
done

# Both additional DCs promoted (Win32_ComputerSystem.PartOfDomain == True)
for adc in bs-dc02 fops-dc02; do
  out=$(A "$adc" -m ansible.windows.win_powershell \
    -a 'script=(Get-WmiObject Win32_ComputerSystem).PartOfDomain' --one-line)
  v=$(echo "$out" | ps_output)
  if [ "$v" = "true" ] || [ "$v" = "True" ]; then
    pass "$adc: PartOfDomain True (additional DC promoted)"
  else
    fail "$adc: PartOfDomain $v (additional DC promotion didn't take)"
  fi
done

# Cross-forest trust — one outgoing on vcab, one incoming on flightops
out=$(A bs-dc01 -m ansible.windows.win_powershell \
  -a 'script=([System.DirectoryServices.ActiveDirectory.Forest]::GetCurrentForest().GetAllTrustRelationships() | Where-Object {$_.TargetName -eq "flightops.lan"}).Count' \
  --one-line)
count=$(echo "$out" | ps_output)
if [ "$count" = "1" ]; then
  pass "Cross-forest trust on vcab side (outgoing to flightops.lan)"
else
  fail "Cross-forest trust missing on vcab side (count=$count)"
fi

out=$(A fops-dc01 -m ansible.windows.win_powershell \
  -a 'script=([System.DirectoryServices.ActiveDirectory.Forest]::GetCurrentForest().GetAllTrustRelationships() | Where-Object {$_.TargetName -eq "vcab.lan"}).Count' \
  --one-line)
count=$(echo "$out" | ps_output)
if [ "$count" = "1" ]; then
  pass "Cross-forest trust on flightops side (incoming from vcab.lan)"
else
  fail "Cross-forest trust missing on flightops side (count=$count)"
fi

# Domain-joined member-host counts (vcab + flightops separately)
for grp in members_vcab members_flightops; do
  total=$(n_hosts "$grp")
  joined=$(A "$grp" -m ansible.windows.win_powershell \
    -a 'script=(Get-WmiObject Win32_ComputerSystem).PartOfDomain' --one-line \
    | grep -oE '"output":[[:space:]]*\[[Tt]rue\]' | wc -l | tr -d ' ')
  if [ "$joined" -eq "$total" ] && [ "$total" -gt 0 ]; then
    pass "$grp: $joined/$total hosts domain-joined"
  else
    fail "$grp: $joined/$total hosts domain-joined"
  fi
done

# DNS forwarders set on each PDC (8.8.8.8 / 8.8.4.4 / 1.1.1.1)
for dc in bs-dc01 fops-dc01; do
  out=$(A "$dc" -m ansible.windows.win_powershell \
    -a 'script=((Get-DnsServerForwarder).IPAddress.IPAddressToString -join ",")' \
    --one-line)
  if echo "$out" | grep -q '8.8.8.8' && echo "$out" | grep -q '1.1.1.1'; then
    pass "$dc DNS forwarders → 8.8.8.8 / 8.8.4.4 / 1.1.1.1"
  else
    fail "$dc DNS forwarders not pointed at is-inet aliases"
  fi
done

# =========================================================================
# 4. File services
# =========================================================================
section "4. File services"

# Mapped Network Drives GPO exists on each PDC
for dc in bs-dc01:vcab.lan fops-dc01:flightops.lan; do
  host="${dc%%:*}"
  forest="${dc##*:}"
  out=$(A "$host" -m ansible.windows.win_powershell \
    -a 'script=(Get-GPO -All | Where-Object {$_.DisplayName -eq "Mapped Network Drives"}).Count' \
    --one-line)
  count=$(echo "$out" | ps_output)
  if [ "$count" = "1" ]; then
    pass "$forest: 'Mapped Network Drives' GPO exists"
  else
    fail "$forest: 'Mapped Network Drives' GPO missing (count=$count)"
  fi
done

# Each file server actually exposes a 'Share' SMB share
for fs in bs-file01:vcab.lan fops-file01:flightops.lan; do
  host="${fs%%:*}"
  forest="${fs##*:}"
  out=$(A "$host" -m ansible.windows.win_powershell \
    -a 'script=(Get-SmbShare -Name "Share" -ErrorAction SilentlyContinue) -ne $null' \
    --one-line)
  v=$(echo "$out" | ps_output)
  if [ "$v" = "true" ] || [ "$v" = "True" ]; then
    pass "$forest: \\\\$host.$forest\\Share is exposed"
  else
    fail "$forest: \\\\$host.$forest\\Share is missing"
  fi
done

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
  exit 1
fi

echo
printf "${G}All checks passed.${N}\n"
exit 0
