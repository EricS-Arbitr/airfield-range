#!/usr/bin/env bash
# ATC subsystem verification (airfield-range) — run on the controller
# (tarball extracted to /etc/ansible):
#     ./verify_atc.sh
# Checks the whole chain over the mgmt plane. Uses wget (never curl) and forces
# the `minimal` callback so remote stdout is visible. Exit 0 iff all pass.
set -u
cd "$(dirname "$0")" || exit 1

export ANSIBLE_STDOUT_CALLBACK=minimal
export ANSIBLE_LOAD_CALLBACK_PLUGINS=0

RADAR_IP=172.31.5.10 ; FDP=8080
WX_IP=172.31.5.11    ; WX=8081

pass=0 ; fail=0
ok(){ printf '  [PASS] %s\n' "$1" ; pass=$((pass+1)) ; }
no(){ printf '  [FAIL] %s\n' "$1" ; fail=$((fail+1)) ; }

# chk <desc> <host> <remote-test-command>   (command must avoid nested quotes)
chk(){
  local desc=$1 host=$2 cmd=$3 out
  out=$(ansible "$host" -b -m shell -a "$cmd && echo __OK__ || echo __FAIL__" 2>/dev/null)
  if printf '%s' "$out" | grep -q __OK__ ; then ok "$desc" ; else no "$desc" ; fi
}

echo "===== ATC subsystem verification (airfield-range) ====="

echo "-- services --"
chk "atc-feed active"    atc-radar   "systemctl is-active --quiet atc-feed"
chk "readsb active"      atc-radar   "systemctl is-active --quiet readsb"
chk "atc-fdp active"     atc-radar   "systemctl is-active --quiet atc-fdp"
chk "tar1090 active"     atc-radar   "systemctl is-active --quiet tar1090"
chk "atc-weather active" atc-weather "systemctl is-active --quiet atc-weather"

echo "-- feed -> readsb --"
chk "aircraft.json present" atc-radar "test -f /run/readsb/aircraft.json"
chk "aircraft.json parses"  atc-radar "jq -e . /run/readsb/aircraft.json >/dev/null"
cnt=$(ansible atc-radar -b -m shell -a "jq '.aircraft|length' /run/readsb/aircraft.json" 2>/dev/null | tail -1)
echo "  (info) aircraft currently tracked: ${cnt:-?}"

echo "-- FDP --"
chk "FDP health ok"       atc-radar "wget -qO- http://localhost:$FDP/healthz | grep -q ok"
chk "FDP strips endpoint" atc-radar "wget -qO- http://localhost:$FDP/api/strips | jq . >/dev/null"

echo "-- tar1090 scope --"
chk "tar1090 served"      atc-radar "wget -qO- http://localhost/tar1090/ | grep -qi tar1090"

echo "-- weather --"
chk "weather METAR"       atc-weather "wget -qO- http://localhost:$WX/metar | grep -q station"
chk "weather TAF"         atc-weather "wget -qO- http://localhost:$WX/taf | grep -q TAF"
chk "FDP weather proxy"   atc-radar   "wget -qO- http://localhost:$FDP/api/weather | grep -q '\"ok\": true'"

echo "-- cross-host (prod subnet) --"
chk "prod subnet route"   atc-station "ip route | grep -q 172.31.5.0/24"
chk "station -> FDP"      atc-station "wget -qO- http://$RADAR_IP:$FDP/healthz | grep -q ok"
chk "station -> tar1090"  atc-station "wget -qO- http://$RADAR_IP/tar1090/ | grep -qi tar1090"

echo "-- CWP --"
chk "kiosk autostart"     atc-station "test -f /home/simspace/.config/autostart/atc-kiosk.desktop"
chk "browser present"     atc-station "command -v firefox"

echo "-- flight workstations (display shortcut) --"
out=$(ansible fops-flight01 -m win_stat -a "path=C:\\Users\\Public\\Desktop\\ATC Operations.url" 2>/dev/null)
if printf '%s' "$out" | grep -q '"exists": true'; then ok "fops-flight01 ATC shortcut"; else no "fops-flight01 ATC shortcut"; fi

echo
echo "===== $pass passed, $fail failed ====="
echo "Displays (open from a flight workstation or atc-station):"
echo "  CWP / FDP ops page : http://$RADAR_IP:$FDP/   (IDS weather band + radar + strips)"
echo "  tar1090 scope only : http://$RADAR_IP/tar1090/"
echo "  Weather METAR JSON : http://$WX_IP:$WX/metar"
echo "  Weather TAF  JSON  : http://$WX_IP:$WX/taf"
[ "$fail" -eq 0 ]
