#!/usr/bin/env python3
"""Deterministic ATC schedule generator (realistic trajectories).

Builds schedule.json from route templates seeded from airfield.yml so the feed
has dense, repeatable, *plausible* traffic across the whole exercise cycle.

Realism:
  * Arrivals and departures are aligned to the field's runways (straight-in and
    curved base-to-final approaches, runway-heading climb-outs with a turn) — no
    random radial lines.
  * Loiter (tanker/CAP/hold) flies a racetrack — two straight legs joined by
    180 deg turns — not a geometric ring, and holds ~45 min then hands off.
  * Closed-pattern work is a small oval over the field at pattern altitude.
  * En-route transits pass *beside* the field, not through it.
  * Waypoint timing is speed-consistent (offset_s derived from leg distance and
    ground speed), so interpolated motion matches the displayed speed.

Emits the expanded flight schema the feed + FDP consume. Deterministic: same
seed -> identical schedule. Staggered orbit shifts tile the cycle so there is
always ambient traffic without overcrowding the scope.

Usage:
    python3 generate_schedule.py --seed 20260101 --cycle 172800 --out schedule.json
"""
import argparse
import json
import math
import os
import random

# (ac_type, category, heavy, fuel_gal, callsign_prefix, cruise_kt, app_kt)
FLEET = [
    ("C-17",   "heavy",  True,  5000, "RCH",  330, 230),
    ("KC-135", "heavy",  True, 10000, "PAT",  320, 220),
    ("C-130",  "medium", False,   0,  "RCH",  260, 170),
    ("F-16",   "fast",   False,   0,  "VIPER",420, 200),
    ("C-172",  "light",  False,   0,  "N",    110,  70),
    ("UH-60",  "helo",   False,   0,  "ARMY", 120,  80),
]
LOITER = [a for a in FLEET if a[1] in ("heavy", "fast", "medium")]
PATTERN_AC = [a for a in FLEET if a[1] in ("light", "helo")]


def point(lat, lon, brg, dist_nm):
    """Great-circle-ish offset from a point; fine at terminal-area scale."""
    b = math.radians(brg)
    dlat = (dist_nm / 60.0) * math.cos(b)
    dlon = (dist_nm / 60.0) * math.sin(b) / math.cos(math.radians(lat))
    return round(lat + dlat, 5), round(lon + dlon, 5)


def dist_nm(a, b):
    dlat = (b[0] - a[0]) * 60.0
    dlon = (b[1] - a[1]) * 60.0 * math.cos(math.radians((a[0] + b[0]) / 2))
    return math.hypot(dlat, dlon)


def build(af, anchors, t0=0):
    """anchors: list of (brg, dist_nm, alt_ft, gs_kt) relative to the field.
    Returns waypoints with speed-consistent offset_s starting at t0."""
    elev = af["field_elev_ft"]
    wps, t, prev = [], float(t0), None
    for brg, dist, alt, gs in anchors:
        lat, lon = point(af["lat"], af["lon"], brg, dist)
        if prev is not None:
            d = dist_nm((prev[0], prev[1]), (lat, lon))
            avg = max((gs + prev[2]) / 2.0, 25)
            t += d / avg * 3600.0
        wps.append({"lat": lat, "lon": lon, "alt_ft": int(alt),
                    "gs_kt": int(gs), "offset_s": int(round(t)),
                    "on_ground": alt <= elev + 5})
        prev = (lat, lon, gs)
    return wps


def _oval_nodes(clat, clon, axis, leg, r, step_deg=20):
    """Nodes of one holding-pattern/CAP racetrack lap: two straight legs of
    length `leg` offset +/- r from the axis, joined by 180 deg turns of radius r."""
    def pt(along, cross):
        la, lo = point(clat, clon, axis, along)
        return point(la, lo, (axis + 90) % 360, cross)

    nodes = [pt(leg / 2, r), pt(-leg / 2, r)]                  # leg 1 (+r side)
    th = 90 + step_deg                                          # turn 1 at -leg/2 end
    while th < 270:
        nodes.append(pt(-leg / 2 + r * math.cos(math.radians(th)),
                        r * math.sin(math.radians(th))))
        th += step_deg
    nodes += [pt(-leg / 2, -r), pt(leg / 2, -r)]                # leg 2 (-r side)
    th = 270 + step_deg                                         # turn 2 at +leg/2 end
    while th < 450:
        nodes.append(pt(leg / 2 + r * math.cos(math.radians(th)),
                        r * math.sin(math.radians(th))))
        th += step_deg
    return nodes


def racetrack(af, center_brg, center_dist, axis, leg, r, alt, gs, dur, t0=0):
    """A racetrack loiter (CAP/tanker track or hold), looping for `dur` seconds.
    Speed-consistent offset_s so the displayed motion matches gs."""
    clat, clon = point(af["lat"], af["lon"], center_brg, center_dist)
    nodes = _oval_nodes(clat, clon, axis, leg, r)
    nodes.append(nodes[0])                                      # close the loop
    wps, t, prev, i, end = [], float(t0), None, 0, t0 + dur
    while t <= end:
        n = nodes[i % len(nodes)]
        if prev is not None:
            t += dist_nm(prev, n) / max(gs, 1) * 3600.0
            if t > end:
                break
        wps.append({"lat": n[0], "lon": n[1], "alt_ft": int(alt),
                    "gs_kt": int(gs), "offset_s": int(round(t)), "on_ground": False})
        prev, i = n, i + 1
    return wps


# --- templates (return anchors relative to the field) -----------------------
def arrival_straight(af, ac, H):
    app, v = (H + 180) % 360, ac[6]
    return [(app, 22, 9000, v + 90), (app, 13, 5000, v + 40), (app, 8, 3000, v + 15),
            (app, 4, 1600, v), (app, 1, af["field_elev_ft"] + 250, v - 15), (0, 0.01, af["field_elev_ft"], 0)]


def arrival_curved(af, ac, H):
    app, v = (H + 180) % 360, ac[6]
    off = (app + rng_sign() * 35) % 360          # offset downwind/base entry
    mid = (app + rng_sign() * 12) % 360
    return [(off, 20, 7000, v + 60), (off, 11, 4000, v + 25), (mid, 7, 2500, v + 5),
            (app, 4, 1600, v), (app, 1, af["field_elev_ft"] + 250, v - 15), (0, 0.01, af["field_elev_ft"], 0)]


def departure(af, ac, H):
    elev, vc, va = af["field_elev_ft"], ac[5], ac[6]
    turn = (H + rng.choice([-35, -25, 25, 35])) % 360
    return [(0, 0.01, elev, 0), (H, 2, 1500, va), (H, 5, 4500, va + 50),
            (turn, 13, 10000, vc - 40), (turn, 26, 16000, vc)]


def pattern(af, ac, H):
    """Closed traffic: a small oval over the field at pattern altitude."""
    pa = af["field_elev_ft"] + af.get("pattern_alt_ft_agl", 1000)
    v = ac[6]
    side = rng_sign()
    # four corners of a 2.5 x 1.2 nm oval aligned with the runway
    a = []
    for off, dist, alt in [((H) % 360, 1.3, pa), ((H + side * 60) % 360, 1.4, pa),
                           ((H + side * 120) % 360, 1.4, pa), ((H + 180) % 360, 1.3, pa),
                           ((H + 180 - side * 60) % 360, 1.4, pa),
                           ((H + 180 - side * 120) % 360, 1.4, pa)]:
        a.append((off, dist, alt, v))
    return a * 2 + [(0, 0.01, af["field_elev_ft"], 0)]   # two circuits, then land


def rng_sign():
    return rng.choice([-1, 1])


def make(af, n, t0, kind, H, dur=0):
    # waypoint offset_s are RELATIVE (0-based); departure_offset_s positions them.
    if kind == "arrival":
        ac = rng.choice(FLEET)
        wps = build(af, (arrival_straight if rng.random() < 0.6 else arrival_curved)(af, ac, H))
        rule = "inbound"
    elif kind == "departure":
        ac = rng.choice(FLEET)
        wps = build(af, departure(af, ac, H))
        rule = "outbound"
    elif kind == "pattern":
        ac = rng.choice(PATTERN_AC)
        wps = build(af, pattern(af, ac, H))
        rule = "local"
    elif kind == "transit":
        ac = rng.choice(LOITER)
        hdg = rng.uniform(0, 360)
        off = (hdg + 90) % 360
        offd = rng.uniform(9, 16)
        clat, clon = point(af["lat"], af["lon"], off, offd)
        a = rng.choice([28000, 33000, 37000]); g = ac[5] + 60
        p0 = point(clat, clon, (hdg + 180) % 360, 32)
        p1 = point(clat, clon, hdg, 32)
        wps = [{"lat": p0[0], "lon": p0[1], "alt_ft": a, "gs_kt": g, "offset_s": 0, "on_ground": False},
               {"lat": clat, "lon": clon, "alt_ft": a, "gs_kt": g,
                "offset_s": int(dist_nm(p0, (clat, clon)) / g * 3600), "on_ground": False},
               {"lat": p1[0], "lon": p1[1], "alt_ft": a, "gs_kt": g,
                "offset_s": int(dist_nm(p0, p1) / g * 3600), "on_ground": False}]
        rule = "local"
    else:  # orbit — a racetrack loiter (CAP/tanker track), not a geometric ring
        ac = rng.choice(LOITER)
        wps = racetrack(af, rng.uniform(0, 360), rng.uniform(10, 16),
                        rng.uniform(0, 360), rng.uniform(6, 11), rng.uniform(2.2, 3.6),
                        rng.choice([20000, 24000, 27000]), ac[5] - 40, dur)
        rule = "local"

    fl = {"callsign": _callsign(ac[4]), "icao_hex": f"AE{n:04X}", "squawk": _squawk(),
          "ac_type": ac[0], "category": ac[1], "flight_rule": rule,
          "origin": af["ident_icao"] if rule != "inbound" else "KADW",
          "destination": af["ident_icao"] if rule == "inbound" else "KADW",
          "heavy": ac[2], "departure_offset_s": t0, "waypoints": wps}
    if ac[2] and ac[3]:
        fl["fuel_request_gal"] = rng.randint(int(ac[3] * 0.8), ac[3])
    return fl


def _callsign(p):
    if p == "N":
        L = "ABCDEGHJKLMPRSTUVWXYZ"
        return f"N{rng.randint(100,999)}{rng.choice(L)}{rng.choice(L)}"
    return f"{p}{rng.randint(1,99):02d}" if p in ("VIPER", "ARMY", "PAT") else f"{p}{rng.randint(100,999)}"


def _squawk():
    return "1200" if rng.random() < 0.12 else f"{rng.randint(1,6)}{rng.randint(0,7)}{rng.randint(0,7)}{rng.randint(0,7)}"


rng = random.Random(0)


def main():
    global rng
    ap = argparse.ArgumentParser()
    here = os.path.dirname(os.path.abspath(__file__))
    ap.add_argument("--airfield", default=os.path.join(here, "airfield.yml"))
    ap.add_argument("--cycle", type=int, default=172800)
    ap.add_argument("--seed", type=int, default=20260101)
    ap.add_argument("--interval", type=int, default=1200, help="mean s between movements")
    ap.add_argument("--orbit-dur", type=int, default=2700, help="how long each loiter holds (s)")
    ap.add_argument("--out", default=os.path.join(here, "schedule.json"))
    a = ap.parse_args()
    rng = random.Random(a.seed)

    try:
        import yaml
        af = yaml.safe_load(open(a.airfield))["airfield"]
    except ImportError:
        af = _mini_airfield(a.airfield)

    rwys = []
    for r in af["runways"]:
        rwys += [r["true_hdg"], (r["true_hdg"] + 180) % 360]
    active = rng.choice(rwys)

    flights, n = [], 1
    # Loiter racetracks tile the cycle so there is always >=1 ambient target, but
    # each holds only ~45 min and a fresh one starts before the last departs
    # (slight overlap = a believable on-station handoff), with varied
    # track/altitude so it is never the same ring parked overhead for hours.
    t = 0
    while t < a.cycle:
        dur = min(a.orbit_dur, a.cycle - t)
        if dur >= 1200:
            flights.append(make(af, n, t, "orbit", active, dur)); n += 1
        t += int(a.orbit_dur * rng.uniform(0.75, 0.95))

    # discrete movements, runway-aligned (mostly the active runway)
    t = 0
    kinds = ["arrival", "departure", "arrival", "departure", "pattern", "transit"]
    while t < a.cycle:
        kind = rng.choice(kinds)
        H = active if rng.random() < 0.8 else rng.choice(rwys)
        fl = make(af, n, t, kind, H)
        if t + fl["waypoints"][-1]["offset_s"] <= a.cycle:
            flights.append(fl); n += 1
        t += int(rng.uniform(a.interval * 0.6, a.interval * 1.4))

    flights.sort(key=lambda f: f["departure_offset_s"])
    out = {"_generated": {"seed": a.seed, "cycle_s": a.cycle, "active_rwy": active,
                          "flights": len(flights)}, "flights": flights}
    json.dump(out, open(a.out, "w"), indent=1)
    print(f"wrote {a.out}: {len(flights)} flights/{a.cycle}s, active rwy hdg {active} (seed {a.seed})")


def _mini_airfield(path):
    af = {}
    for line in open(path):
        s = line.split("#", 1)[0].rstrip()
        if not s.strip() or s.strip() == "airfield:":
            continue
        if s.lstrip().startswith("- {"):
            body = s.lstrip()[3:].rstrip().rstrip("}")        # inside the { }
            d = {}
            for pair in body.split(","):
                if ":" not in pair:
                    continue
                k, v = [x.strip() for x in pair.split(":", 1)]
                v = v.strip().strip('"').strip("'")
                for cast in (int, float):
                    try:
                        v = cast(v)
                        break
                    except ValueError:
                        pass
                d[k] = v
            af.setdefault("runways", []).append(d)
            continue
        if ":" in s:
            k, v = [x.strip() for x in s.split(":", 1)]
            if v in ("", "[]"):
                continue
            for cast in (int, float):
                try:
                    af[k] = cast(v); break
                except ValueError:
                    af[k] = v.strip('"')
    return af


if __name__ == "__main__":
    main()
