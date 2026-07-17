#!/bin/bash
#
# verify_fuel_farm.sh — read-only health check for the Fuel Farm OT
# subsystem after `ansible-playbook fuel_farm_playbook.yml` has run.
#
# Covers systems/fuel_farm/fuel-farm-build-sheet.md §11 acceptance tests
# where they can be probed without a live SCADA client. Checks are grouped:
#
#   1. Inventory reachability — mgmt-plane connectivity to every fuel host
#   2. OT network — bs-modbus-gateway routes + prod-plane bindings
#   3. fuel-db — PostgreSQL 16 + TimescaleDB + audit schema + seeds
#   4. fuel-farm-sim — fuelsim service + venv + replay timeline
#   5. ff-plc-1 — OpenPLC container + Modbus :502 + web :8080
#   6. fuel-hist — Telegraf + InfluxDB 2.7 (:8086) + Grafana (:3000)
#   7. control-room-hmi — FUXA :1881
#
# What this DOESN'T check (yet): live data flow through the whole stack
# (PLC polling sim → historian receiving points → audit DB accumulating
# rows). Those depend on the sim modules being fully authored
# (roles/fuel_sim/files/fuelsim/{modbus,physics,state_machine,replay}.py
# are still TODO per README).
#
# Usage:
#   cd /etc/ansible && ./verify_fuel_farm.sh           # summary
#   cd /etc/ansible && ./verify_fuel_farm.sh -v        # show ansible
#                                                       # output for each fail
#
# Exit 0 if every check passes, 1 if any fails.

set -u

VERBOSE=0
case "${1:-}" in
  -v|--verbose) VERBOSE=1 ;;
  -h|--help)    sed -n '2,30p' "$0"; exit 0 ;;
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
    printf "      ${D}%s${N}\n" "$2" | head -5
  fi
}
section() { printf "\n${B}━━ %s ━━${N}\n" "$1"; }
note()    { printf "  ${D}%s${N}\n" "$1"; }

A() { ansible "$@" 2>&1; }

n_hosts() {
  ansible "$1" --list-hosts 2>/dev/null | tail -n +2 | sed '/^$/d' | wc -l | tr -d ' '
}

# One reachability probe per group.
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

# Probe one Linux host with a shell command, grep stdout.
check_sh() {
  local host="$1" cmd="$2" expect="$3" label="$4"
  local out
  out=$(A "$host" -m ansible.builtin.shell -a "$cmd" --one-line)
  if echo "$out" | grep -qE "$expect"; then
    pass "$label"
  else
    fail "$label" "$out"
  fi
}

# Probe one VyOS router.
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

# =========================================================================
# 1. Inventory reachability
# =========================================================================
section "1. Inventory reachability"

# The `fuel` group in hosts covers ot_fuel (fuel-db, fuel-farm-sim, fuel-hist)
# plus the OT bridge hosts (control-room-hmi shared with power, ff-plc-1).
# bs-modbus-gateway is in [net], probed separately.
probe_group "fuel"              ansible.builtin.ping   ""  "Fuel enclave hosts (ssh)"
probe_group "bs-modbus-gateway" vyos.vyos.vyos_facts   ""  "bs-modbus-gateway (network_cli)"

# =========================================================================
# 2. OT network — bs-modbus-gateway + prod-plane bindings
# =========================================================================
section "2. OT network"

# bs-modbus-gateway is the L3 gateway for all three fuel OT segments:
#   eth1 172.16.45.9  (Fuel-PLC /29)
#   eth2 172.16.45.1  (MTU /29)
#   eth3 172.16.46.16 (Fuel-Sim /24)
# And has default 0.0.0.0/0 → 172.31.1.17 (bs-ops-fw).
check_vyos bs-modbus-gateway \
  "show ip route 0.0.0.0/0" \
  'static|S\*|S>|via 172\.31\.1\.17' \
  "bs-modbus-gateway default route via bs-ops-fw"

for cidr in "172.16.45.9" "172.16.45.1" "172.16.46.16"; do
  check_vyos bs-modbus-gateway \
    "show interfaces" \
    "$(echo "$cidr" | sed 's/\./\\./g')" \
    "bs-modbus-gateway interface bound to $cidr"
done

# Each fuel host's prod NIC bound to the expected /29 or /24 address.
declare -A FUEL_PROD_IP=(
  [ff-plc-1]="172.16.45.10"
  [fuel-farm-sim]="172.16.46.17"
  [fuel-hist]="172.16.46.18"
  [fuel-db]="172.16.46.19"
  [control-room-hmi]="172.16.45.3"
)
for h in "${!FUEL_PROD_IP[@]}"; do
  ip="${FUEL_PROD_IP[$h]}"
  check_sh "$h" \
    "ip -4 addr show | awk '/inet /{print \$2}' | grep -F '$ip'" \
    "$(echo "$ip" | sed 's/\./\\./g')" \
    "$h prod NIC bound to $ip"
done

# =========================================================================
# 3. fuel-db — PostgreSQL 16 + TimescaleDB + audit schema
# =========================================================================
section "3. fuel-db (PostgreSQL 16 + TimescaleDB)"

# Match "active" as a word (not "inactive"). Emit an unambiguous OK/DOWN
# token so the check pattern doesn't need to disambiguate substrings.
check_sh fuel-db \
  "s=\$(systemctl is-active postgresql@16-main 2>/dev/null); [ -z \"\$s\" ] && s=\$(systemctl is-active postgresql 2>/dev/null); [ \"\$s\" = active ] && echo POSTGRES_ACTIVE || echo POSTGRES_DOWN:\"\$s\"" \
  'POSTGRES_ACTIVE' \
  "fuel-db postgresql service active"

# Port bound OR actually accepting TCP -- port-bound alone isn't enough
# (docker-proxy can hold the port while the service inside is still
# initializing), so also verify a TCP connect.
check_sh fuel-db \
  "timeout 2 bash -c 'echo > /dev/tcp/127.0.0.1/5432' 2>/dev/null && echo POSTGRES_ACCEPTS || echo POSTGRES_REFUSED" \
  'POSTGRES_ACCEPTS' \
  "fuel-db :5432 accepting TCP connections"

# TimescaleDB extension check MUST target the fuel_audit database where
# the role enables it -- extension is per-database, not cluster-wide.
# Also emit an unambiguous token so the match doesnt need to parse
# psqls output layout.
check_sh fuel-db \
  "r=\$(sudo -u postgres psql -d fuel_audit -tAc \"SELECT 1 FROM pg_extension WHERE extname='timescaledb';\" 2>/dev/null); [ \"\$r\" = 1 ] && echo TIMESCALE_LOADED || echo TIMESCALE_MISSING" \
  'TIMESCALE_LOADED' \
  "fuel-db TimescaleDB extension loaded (in fuel_audit)"

# Audit schema tables (per build sheet §7). Emit EXISTS/MISSING tokens
# instead of relying on psql -tA outputting just `t` -- ansibles --one-line
# format prepends "(stdout) " so `^t$` never matches.
for tbl in tanks trucks aircraft pads truck_queue load_txn delivery_txn; do
  check_sh fuel-db \
    "r=\$(sudo -u postgres psql -d fuel_audit -tAc \"SELECT to_regclass('public.$tbl') IS NOT NULL;\" 2>/dev/null); [ \"\$r\" = t ] && echo EXISTS || echo MISSING" \
    'EXISTS' \
    "fuel-db fuel_audit.$tbl exists"
done

# Reference tables seeded (non-zero row counts). Same token pattern.
for tbl in tanks trucks aircraft pads; do
  check_sh fuel-db \
    "r=\$(sudo -u postgres psql -d fuel_audit -tAc \"SELECT COUNT(*) > 0 FROM $tbl;\" 2>/dev/null); [ \"\$r\" = t ] && echo SEEDED || echo EMPTY" \
    'SEEDED' \
    "fuel-db $tbl has seed rows"
done

# =========================================================================
# 4. fuel-farm-sim — fuelsim service + venv + replay timeline
# =========================================================================
section "4. fuel-farm-sim (fuelsim service + venv + replay)"

check_sh fuel-farm-sim \
  "test -x /opt/fuelsim/venv/bin/python && echo OK" \
  'OK' \
  "fuel-farm-sim /opt/fuelsim venv present"

check_sh fuel-farm-sim \
  "/opt/fuelsim/venv/bin/pip list 2>/dev/null | grep -Ei '^pymodbus|^psycopg2'" \
  'pymodbus|psycopg2' \
  "fuel-farm-sim venv has pymodbus + psycopg2"

check_sh fuel-farm-sim \
  "s=\$(systemctl is-active fuelsim.service 2>/dev/null); [ \"\$s\" = active ] && echo FUELSIM_ACTIVE || echo FUELSIM_DOWN:\"\$s\"" \
  'FUELSIM_ACTIVE' \
  "fuel-farm-sim fuelsim.service active"

check_sh fuel-farm-sim \
  "test -f /opt/fuelsim/config/fuelsim.yml && echo OK" \
  'OK' \
  "fuel-farm-sim config file deployed"

# Timeline was populated (wc returns N). Match the count with the (stdout)
# prefix ansible --one-line prepends -- the previous `^[1-9]` anchor
# assumed the count would be at line start, which it never is.
check_sh fuel-farm-sim \
  "test -f /opt/fuelsim/timelines/fuel_ops_timeline.jsonl && wc -l < /opt/fuelsim/timelines/fuel_ops_timeline.jsonl" \
  '\(stdout\) [1-9]' \
  "fuel-farm-sim replay timeline deployed"

# Field-bus Modbus slave on :502 (fuel-farm-sim exposes this to the PLC).
# Bound to prod NIC (172.16.46.17). Only meaningful once fuelsim/modbus.py
# is authored; today the service will start but no port opens. This check
# will FAIL until modbus.py is fleshed out.
check_sh fuel-farm-sim \
  "timeout 2 bash -c 'echo > /dev/tcp/172.16.46.17/502' 2>/dev/null && echo MODBUS_ACCEPTS || echo MODBUS_REFUSED" \
  'MODBUS_ACCEPTS' \
  "fuel-farm-sim field-bus Modbus :502 accepting TCP (needs modbus.py)"

# =========================================================================
# 5. ff-plc-1 — OpenPLC v4 + Modbus :502 + web :8080
# =========================================================================
section "5. ff-plc-1 (OpenPLC v4)"

check_sh ff-plc-1 \
  "docker ps --format '{{.Names}} {{.Status}}' | grep -Ei 'openplc.*Up' || echo NONE" \
  'openplc.*Up' \
  "ff-plc-1 OpenPLC container running"

# OpenPLC container publishes ports to the prod-NIC IP (172.16.45.10) only,
# not 0.0.0.0 or 127.0.0.1. Check against that IP directly. After the
# fuel_plc role's openplc_bootstrap.py runs (upload fuel_farm.st, compile,
# /start_plc), the runtime opens :502 and serves the SCADA-bus register
# image defined by the .st program.
check_sh ff-plc-1 \
  "timeout 2 bash -c 'echo > /dev/tcp/172.16.45.10/502' 2>/dev/null && echo MODBUS_ACCEPTS || echo MODBUS_REFUSED" \
  'MODBUS_ACCEPTS' \
  "ff-plc-1 SCADA-bus Modbus :502 accepting TCP"

check_sh ff-plc-1 \
  "timeout 2 bash -c 'echo > /dev/tcp/172.16.45.10/8080' 2>/dev/null && echo WEB_ACCEPTS || echo WEB_REFUSED" \
  'WEB_ACCEPTS' \
  "ff-plc-1 OpenPLC web :8080 accepting TCP"

check_sh ff-plc-1 \
  "curl -sS -o /dev/null -w 'code=%{http_code}\n' --max-time 5 http://172.16.45.10:8080/" \
  'code=(200|302|401)' \
  "ff-plc-1 OpenPLC web :8080 returns HTTP 200/302/401"

# Cross-plane Modbus round-trip: fuel-farm-sim already has pymodbus in its
# venv, and sits one hop from ff-plc-1 via bs-modbus-gateway. Reading HR 0
# (LR1_PRESET_GAL) proves three things at once:
#   - :502 is bound (implicit from a successful TCP connect)
#   - a program is loaded (raw connect works, but read returns Modbus
#     "illegal function" if no program's mapped anything)
#   - the register map matches group_vars/fuel.yml
# This is stricter than the local TCP-only check above, and catches the
# "container up but no program loaded" state without requiring an HTTP
# dashboard scrape.
check_sh fuel-farm-sim \
  "/opt/fuelsim/venv/bin/python -c \"
from pymodbus.client import ModbusTcpClient as C
c = C('172.16.45.10', port=502, timeout=5)
if not c.connect(): print('CONNECT_FAILED'); exit()
r = c.read_holding_registers(address=0, count=1, slave=1)
c.close()
print('READ_OK' if not r.isError() else 'READ_ERROR:' + str(r))
\"" \
  'READ_OK' \
  "ff-plc-1 Modbus HR 0 (LR1_PRESET_GAL) readable from fuel-farm-sim"

# =========================================================================
# 6. fuel-hist — Telegraf + InfluxDB 2.7 + Grafana
# =========================================================================
section "6. fuel-hist (Telegraf + InfluxDB 2.7 + Grafana)"

for svc in telegraf influxdb grafana; do
  check_sh fuel-hist \
    "docker ps --format '{{.Names}} {{.Status}}' | grep -Ei '$svc.*Up' || echo NONE" \
    "$svc.*Up" \
    "fuel-hist $svc container running"
done

# InfluxDB and Grafana containers publish to the prod-NIC IP (172.16.46.18).
check_sh fuel-hist \
  "timeout 2 bash -c 'echo > /dev/tcp/172.16.46.18/8086' 2>/dev/null && echo INFLUX_ACCEPTS || echo INFLUX_REFUSED" \
  'INFLUX_ACCEPTS' \
  "fuel-hist InfluxDB :8086 accepting TCP"

check_sh fuel-hist \
  "timeout 2 bash -c 'echo > /dev/tcp/172.16.46.18/3000' 2>/dev/null && echo GRAFANA_ACCEPTS || echo GRAFANA_REFUSED" \
  'GRAFANA_ACCEPTS' \
  "fuel-hist Grafana :3000 accepting TCP"

check_sh fuel-hist \
  "curl -sS -o /dev/null -w 'code=%{http_code}\n' --max-time 5 http://172.16.46.18:8086/health" \
  'code=200' \
  "fuel-hist InfluxDB /health returns 200"

check_sh fuel-hist \
  "curl -sS -o /dev/null -w 'code=%{http_code}\n' --max-time 5 http://172.16.46.18:3000/api/health" \
  'code=200' \
  "fuel-hist Grafana /api/health returns 200"

# Bucket existence via server-side --name filter. If the bucket exists,
# --name matches ONE record and the CLI prints a table row containing
# "fuel" (regardless of tab/space alignment). If not, it prints only the
# header row (no "fuel" anywhere). `grep -qw fuel` is safe because the
# CLI wouldn't have any other reason to mention the word "fuel" in its
# own output. No quote-nesting through ansible, no --json parsing, no
# tab-separator gymnastics. If this still misfires, the underlying
# `docker exec` is failing silently and we need to look at that instead.
check_sh fuel-hist \
  "docker exec influxdb influx bucket list --name fuel --token Simspace1SimspaceFuelHistorianAdminToken --org airfield 2>&1 | grep -qw fuel && echo BUCKET_PRESENT || echo BUCKET_MISSING" \
  'BUCKET_PRESENT' \
  "fuel-hist InfluxDB 'fuel' bucket exists"

# =========================================================================
# 7. control-room-hmi — FUXA
# =========================================================================
section "7. control-room-hmi (FUXA)"

check_sh control-room-hmi \
  "docker ps --format '{{.Names}} {{.Status}}' | grep -Ei 'fuxa.*Up' || echo NONE" \
  'fuxa.*Up' \
  "control-room-hmi FUXA container running"

# FUXA container publishes to prod-NIC IP (172.16.45.3).
check_sh control-room-hmi \
  "timeout 2 bash -c 'echo > /dev/tcp/172.16.45.3/1881' 2>/dev/null && echo FUXA_ACCEPTS || echo FUXA_REFUSED" \
  'FUXA_ACCEPTS' \
  "control-room-hmi FUXA :1881 accepting TCP"

check_sh control-room-hmi \
  "curl -sS -o /dev/null -w 'code=%{http_code}\n' --max-time 5 http://172.16.45.3:1881/" \
  'code=(200|302)' \
  "control-room-hmi FUXA :1881 returns HTTP 200/302"

# Cross-plane: control-room-hmi should be able to reach ff-plc-1:502 for
# Modbus polling. Prod path: control-room-hmi eth0 172.16.45.3 (MTU /29) →
# bs-modbus-gateway .45.1 → .45.9 → ff-plc-1 .45.10:502.
check_sh control-room-hmi \
  "timeout 3 bash -c 'echo > /dev/tcp/172.16.45.10/502' && echo REACHABLE || echo BLOCKED" \
  'REACHABLE' \
  "control-room-hmi can reach ff-plc-1:502 (Modbus master path)"

# =========================================================================
# Summary
# =========================================================================
section "Summary"
TOTAL=$((PASS + FAIL))
printf "  Total checks : %d\n" "$TOTAL"
printf "  ${G}Pass${N}         : %d\n" "$PASS"
printf "  ${R}Fail${N}         : %d\n" "$FAIL"

if [ "$FAIL" -gt 0 ]; then
  printf "\nFailed checks:\n"
  for f in "${FAILURES[@]}"; do
    printf "  ${R}•${N} %s\n" "$f"
  done
  printf "\nRe-run with -v to see ansible's output for each failure.\n"
  exit 1
else
  printf "\n${G}All checks passed.${N}\n"
  exit 0
fi
