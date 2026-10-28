#!/usr/bin/env python3
"""ATC synthetic ADS-B feed engine.

Emits **Beast-format DF17 extended squitter** (real ADS-B frames) on the beast
port AND decoded SBS/BaseStation text on the sbs port, both bound to all
interfaces with multiple concurrent clients. readsb ingests Beast (beast_in);
SBS stays for simple human taps. The exercise clock is EPOCH-BASED so it
survives restarts and stays aligned range-wide:

    cycle_position = (now_utc - epoch) % cycle_seconds

DF17 detail: Mode S CRC-24 (poly 0x1FFF409); airborne position uses CPR and we
emit a matching even+odd pair each tick (identical underlying lat/lon) so
readsb's global decode is unambiguous. TC4 identification, TC11 airborne
position, TC19 velocity (ground-speed subtype).

TODO: Mode-A squawk via DF21, surface position (TC5-8) for on-ground aircraft,
scripted emergencies (7700), optional primary-radar/MLAT feed.
"""
import argparse
import json
import math
import socket
import threading
import time
from datetime import datetime, timezone

NZ = 15  # CPR latitude zones


# --- Mode S / ADS-B encoding ------------------------------------------------
def _crc24(data):
    """Mode S CRC-24 over `data` bytes (poly 0x1FFF409). For a complete DF17
    message (14 bytes) this returns 0; for the first 11 bytes it is the parity."""
    msg = int.from_bytes(data, "big") << 24
    nbits = len(data) * 8
    for p in range(nbits + 24 - 1, 23, -1):
        if msg & (1 << p):
            msg ^= 0x1FFF409 << (p - 24)
    return msg & 0xFFFFFF


def df17(icao, me):
    """Assemble a 14-byte DF17 message from a 24-bit ICAO and 56-bit ME."""
    b = bytearray(14)
    b[0] = 0x8D                       # DF=17, CA=5
    b[1], b[2], b[3] = (icao >> 16) & 0xFF, (icao >> 8) & 0xFF, icao & 0xFF
    b[4:11] = me.to_bytes(7, "big")
    crc = _crc24(bytes(b[0:11]))
    b[11], b[12], b[13] = (crc >> 16) & 0xFF, (crc >> 8) & 0xFF, crc & 0xFF
    return bytes(b)


def _c6(ch):
    if "A" <= ch <= "Z":
        return ord(ch) - ord("A") + 1
    if "0" <= ch <= "9":
        return ord(ch) - ord("0") + 48
    return 32  # space


def me_ident(callsign):
    cs = (callsign + "        ")[:8]
    me = (4 << 51)                    # TC=4, category=0
    for i, ch in enumerate(cs):
        me |= _c6(ch) << (42 - i * 6)
    return me


def _encode_alt(ft):
    n = int((ft + 1000) / 25)         # 25-ft resolution, Q-bit = 1
    return (((n & 0x7F0) << 1) | 0x10 | (n & 0x0F)) & 0xFFF


def _cpr_nl(lat):
    if lat == 0:
        return 59
    if abs(lat) >= 87:
        return 1 if abs(lat) > 87 else 2
    a = 1 - math.cos(math.pi / (2 * NZ))
    b = math.cos(math.radians(abs(lat))) ** 2
    return int(math.floor(2 * math.pi / math.acos(1 - a / b)))


def _cpr(lat, lon, odd):
    i = 1 if odd else 0
    dlat = 360.0 / (4 * NZ - i)
    yz = int(math.floor(2 ** 17 * ((lat % dlat) / dlat) + 0.5)) & 0x1FFFF
    rlat = dlat * (yz / 2 ** 17 + math.floor(lat / dlat))
    nl = max(_cpr_nl(rlat) - i, 1)
    dlon = 360.0 / nl
    xz = int(math.floor(2 ** 17 * ((lon % dlon) / dlon) + 0.5)) & 0x1FFFF
    return yz, xz


def me_position(lat, lon, alt, odd):
    me = (11 << 51)                   # TC=11, SS=0, SAF=0
    me |= (_encode_alt(alt) & 0xFFF) << 36
    me |= (1 if odd else 0) << 34     # F (CPR odd/even); T=0
    yz, xz = _cpr(lat, lon, odd)
    me |= (yz & 0x1FFFF) << 17
    me |= (xz & 0x1FFFF)
    return me


def me_velocity(gs, track):
    tr = math.radians(track)
    vew, vns = gs * math.sin(tr), gs * math.cos(tr)
    me = (19 << 51) | (1 << 48)       # TC=19, subtype=1 (ground speed)
    me |= (0 if vew >= 0 else 1) << 42
    me |= (min(int(round(abs(vew))) + 1, 1023) & 0x3FF) << 32
    me |= (0 if vns >= 0 else 1) << 31
    me |= (min(int(round(abs(vns))) + 1, 1023) & 0x3FF) << 21
    return me                         # vertical rate left as "no info"


def beast(msg):
    """Wrap a 14-byte Mode S long message in a Beast binary frame (type 0x33)."""
    ts = int(time.time() * 12e6) & 0xFFFFFFFFFFFF
    payload = ts.to_bytes(6, "big") + b"\xc8" + msg
    out = bytearray([0x1A, 0x33])
    for byte in payload:
        if byte == 0x1A:
            out += b"\x1a\x1a"
        else:
            out.append(byte)
    return bytes(out)


# --- SBS/BaseStation (secondary, human-readable) ----------------------------
def _sbs(mtype, hexid, rest):
    gen = datetime.now(timezone.utc)
    ds, ts = gen.strftime("%Y/%m/%d"), gen.strftime("%H:%M:%S.000")
    base = ["MSG", str(mtype), "1", "1", hexid.upper(), "1", ds, ts, ds, ts]
    return (",".join(base + rest) + "\r\n").encode()


# --- TCP fan-out ------------------------------------------------------------
class Server:
    def __init__(self, bind, port):
        self._socks, self._lock = [], threading.Lock()
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind((bind, port))
        srv.listen(16)
        self._srv = srv
        threading.Thread(target=self._accept, daemon=True).start()

    def _accept(self):
        while True:
            try:
                conn, _ = self._srv.accept()
            except OSError:
                break
            with self._lock:
                self._socks.append(conn)

    def send(self, data):
        with self._lock:
            dead = []
            for s in self._socks:
                try:
                    s.sendall(data)
                except OSError:
                    dead.append(s)
            for s in dead:
                self._socks.remove(s)
                try:
                    s.close()
                except OSError:
                    pass


# --- trajectory -------------------------------------------------------------
def _interp(wps, t):
    if not wps or t < wps[0]["offset_s"] or t > wps[-1]["offset_s"]:
        return None
    for a, b in zip(wps, wps[1:]):
        if a["offset_s"] <= t <= b["offset_s"]:
            span = (b["offset_s"] - a["offset_s"]) or 1
            f = (t - a["offset_s"]) / span
            return {k: a[k] + (b[k] - a[k]) * f for k in ("lat", "lon", "alt_ft", "gs_kt")}
    return dict(wps[-1])


def _bearing(a, b):
    dlon = math.radians(b["lon"] - a["lon"])
    la1, la2 = math.radians(a["lat"]), math.radians(b["lat"])
    y = math.sin(dlon) * math.cos(la2)
    x = math.cos(la1) * math.sin(la2) - math.sin(la1) * math.cos(la2) * math.cos(dlon)
    return (math.degrees(math.atan2(y, x)) + 360) % 360


def run(cfg):
    epoch = datetime.fromisoformat(cfg["exercise_epoch"].replace("Z", "+00:00")).timestamp()
    cycle = cfg["cycle_seconds"]
    flights = json.load(open(cfg["schedule"]))["flights"]
    period = 1.0 / max(cfg.get("rate_hz", 1), 1)
    field_elev = cfg.get("airfield", {}).get("field_elev_ft", 0)

    bsrv = Server(cfg["bind"], cfg["beast_port"])
    ssrv = Server(cfg["bind"], cfg["sbs_port"])
    print(f"[atc_feed] Beast DF17 on {cfg['bind']}:{cfg['beast_port']} + "
          f"SBS on {cfg['bind']}:{cfg['sbs_port']} "
          f"({len(flights)} flights, {cycle}s cycle)", flush=True)

    tick = 0
    while True:
        pos = (time.time() - epoch) % cycle
        for fl in flights:
            wps = fl.get("waypoints", [])
            t = pos - fl.get("departure_offset_s", 0)
            p = _interp(wps, t)
            if p is None:
                continue
            icao = int(fl["icao_hex"], 16)
            hx = fl["icao_hex"]
            ahead = _interp(wps, t + 1) or p
            track = _bearing(p, ahead) if ahead is not p else 0.0
            gs, alt = p["gs_kt"], p["alt_ft"]
            on_gnd = "1" if alt <= field_elev + 100 else "0"

            # identification every 5s
            if tick % 5 == 0:
                bsrv.send(beast(df17(icao, me_ident(fl["callsign"]))))
                ssrv.send(_sbs(1, hx, ["", "", fl["callsign"]] + [""] * 9))
            # airborne position: matching even+odd pair (same lat/lon)
            bsrv.send(beast(df17(icao, me_position(p["lat"], p["lon"], alt, False))))
            bsrv.send(beast(df17(icao, me_position(p["lat"], p["lon"], alt, True))))
            # velocity
            bsrv.send(beast(df17(icao, me_velocity(gs, track))))
            # SBS mirrors (position + velocity)
            ssrv.send(_sbs(3, hx, ["", str(int(alt)), "", "",
                                   f"{p['lat']:.5f}", f"{p['lon']:.5f}",
                                   "", fl.get("squawk", ""), "", "", "", on_gnd]))
            ssrv.send(_sbs(4, hx, ["", "", str(int(gs)), f"{int(track)}",
                                   "", "", "", "", "", "", "", ""]))
        tick += 1
        time.sleep(period)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", required=True)
    run(json.load(open(ap.parse_args().config)))
