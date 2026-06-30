#!/bin/sh
# airfield_iface_watchdog.sh — runs forever, re-binds drifted data-plane vmx
# interfaces every 30 seconds. Background daemon launched by the rc.d script
# at /usr/local/etc/rc.d/airfield_iface_watchdog.
#
# The SimSpace RC_pfSense:1.0.0 image periodically drops static IPv4
# bindings on data-plane interfaces (most often vmx1=SWITCH_3 on bs-ops-fw).
# The pfsense_firewall role's pre-handler + post-handler tasks catch drift
# DURING a deploy, but only this loop catches drift between deploys (or
# between the pre-AD rebind play and downstream plays like Join Domain).
# See airfield-range/UPSTREAM_FIXES.md 2026-06-29 entry.
#
# Logs each rebind to syslog (tag: airfield-iface-watchdog).

while :; do
  # Kill dhclient on data-plane interfaces (vmx1+). SimSpace's pfSense
  # image spawns dhclient on every vmx at boot, regardless of static
  # config. The transient DHCP leases poison zebra's connected-route
  # view and prevent OSPF routes from being installed in the kernel.
  # See UPSTREAM_FIXES.md 2026-06-30 entry.
  if pgrep -f "dhclient.*vmx[1-9]" >/dev/null 2>&1; then
    pkill -f "dhclient.*vmx[1-9]" 2>/dev/null
    logger -t airfield-iface-watchdog "Killed dhclient on data-plane interfaces (vmx1+)"
  fi

  /usr/local/bin/php -r '
    require_once("/etc/inc/config.inc");
    require_once("/etc/inc/interfaces.inc");
    $ifs = config_get_path("interfaces", []);
    foreach ($ifs as $key => $cfg) {
      $phys    = $cfg["if"]     ?? "";
      $want_ip = $cfg["ipaddr"] ?? "";
      $subnet  = $cfg["subnet"] ?? "";
      $descr   = $cfg["descr"]  ?? $key;
      if ($phys === "vmx0") continue;
      if (!$want_ip || $want_ip === "dhcp" || !$phys || !$subnet) continue;
      $cur = trim(shell_exec("ifconfig " . escapeshellarg($phys) .
        " 2>/dev/null | awk \"/inet /{print \\$2; exit}\""));
      if ($cur !== $want_ip) {
        shell_exec("pkill -f \"dhclient.*" . escapeshellarg($phys) . "\" 2>/dev/null");
        interface_configure($key);
        shell_exec("ifconfig " . escapeshellarg($phys) . " inet " .
          escapeshellarg($want_ip) . "/" . intval($subnet) . " up 2>&1");
        shell_exec("logger -t airfield-iface-watchdog \"Re-bound " . $phys .
          " (" . $descr . ") to " . $want_ip . " (was: \\\"" . $cur . "\\\")\"");
      }
    }
  ' 2>&1
  sleep 30
done
