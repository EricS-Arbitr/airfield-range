#!/usr/bin/env python3
"""Weather (METAR/TAF) feed for the ATC range.

Serves a deterministic, continuously-evolving observation for the field over
HTTP so the ATC display has a realistic weather consumer and the weather-denial
scenario has an observable effect.

Everything is driven by the SAME epoch-based exercise clock as the ADS-B feed:

    cycle_position = (now_utc - exercise_epoch) % cycle_seconds

so the weather timeline and the flight timeline always line up, survive
restarts, and repeat identically each cycle. There is no wall-clock leakage and
no random() at request time — a given second of the cycle always yields the
same report.

Synoptic model (one pass per cycle, with a diurnal overlay):
  * A frontal system crosses mid-cycle (pressure falls, wind veers S->NW,
    ceilings lower, rain/mist, then clearing) — `syn`.
  * Afternoon convective buildups peak ~1600 each day — `conv`.
  * Temperature follows a diurnal sine (coldest ~0500, warmest ~1500) with a
    few degrees of post-frontal cooling on day two.
  * Dewpoint spread collapses toward saturation in precip/low vis.
  * Altimeter and sea-level pressure track the front; RMK carries AO2, the SLP
    group, the precise T-group, and a PRESRR/PRESFR tendency.

The output is well-formed US METAR / TAF (FAA-style, statute-mile visibility).
This is still a dev stub — the full NEXRAD/TAF build is a separate weather-radar
sheet — but it reads like a real station's hourly sequence.

    python3 metar_feed.py --config config.json      # serve
    python3 metar_feed.py --selftest 48             # print a 48 h METAR sweep
    python3 metar_feed.py --selftest 48 --taf        # ... and the TAF
"""
import argparse
import json
import math
from datetime import datetime, timezone

CFG = {}
EPOCH_TS = 0.0


# --- helpers ----------------------------------------------------------------
def _clamp(x, lo, hi):
    return lo if x < lo else hi if x > hi else x


def _epoch_ts(cfg):
    e = cfg.get("exercise_epoch", "2026-01-01T00:00:00Z")
    return datetime.fromisoformat(e.replace("Z", "+00:00")).timestamp()


def _now_ts():
    return datetime.now(timezone.utc).timestamp()


# --- the model: cycle-second -> met state -----------------------------------
def conditions(cpos, cycle):
    """Deterministic synoptic + diurnal state at `cpos` seconds into the cycle.

    Returns a dict of raw meteorology (SI-ish units) that the formatters turn
    into METAR/TAF tokens."""
    cyc_h = cycle / 3600.0
    t = cpos / 3600.0                       # hours into the cycle
    day_t = t % 24.0                        # synthetic hour-of-day (UTC)

    # Synoptic front: 0 at the cycle edges, 1 at mid-cycle.
    syn = 0.5 - 0.5 * math.cos(2 * math.pi * t / cyc_h)
    # Afternoon convective enhancement, peaking ~1600 each day.
    conv = math.exp(-((day_t - 16.0) / 3.5) ** 2)
    # Weather severity 0 (CAVOK) .. 1 (low IFR), bounded.
    w = _clamp(0.10 + 0.55 * syn + 0.60 * conv * syn, 0.0, 1.0)

    # Temperature: diurnal sine, min ~0500 / max ~1500, cooler behind the front.
    base = 12.0 - 3.0 * max(0.0, (t - cyc_h / 2.0) / (cyc_h / 2.0))
    temp = base + 8.5 * -math.cos(2 * math.pi * (day_t - 3.0) / 24.0)
    # Dewpoint spread: wide when dry, ~1 C near saturation.
    dew = temp - _clamp(11.0 - 10.0 * w, 1.0, 12.0)

    # Wind: slowly veers S -> NW over the cycle, stronger/gustier near the front.
    wdir = (190.0 + 170.0 * (t / cyc_h)) % 360.0
    wspd = 4.0 + 11.0 * syn + 8.0 * conv * syn
    gust = wspd + 8.0 if (conv * syn > 0.35 or wspd > 17.0) else 0.0

    # Pressure: fair ~30.08", frontal trough ~29.64".
    inhg = 30.08 - 0.44 * syn

    return {"w": w, "temp": temp, "dew": dew,
            "wdir": wdir, "wspd": wspd, "gust": gust, "inhg": inhg}


def _inhg_at(cpos, cycle):
    return conditions(cpos % cycle, cycle)["inhg"]


# --- token formatters -------------------------------------------------------
def _fmt_wind(wdir, wspd, gust):
    if wspd < 3.0:
        return "00000KT"
    dd = int(round(wdir / 10.0)) * 10 % 360
    dd = 360 if dd == 0 else dd
    out = "%03d%02d" % (dd, int(round(wspd)))
    if gust and gust > wspd + 4.0:
        out += "G%02d" % int(round(gust))
    return out + "KT"


def _sky(w):
    """(visibility token, [sky layers], weather token) for severity w."""
    base = max(2, int(round(45 - 42 * w)))          # ceiling, hundreds of feet
    if w < 0.20:
        vis, layers, wx = "10SM", [("FEW", base)], ""
    elif w < 0.40:
        vis, layers, wx = "10SM", [("SCT", base)], ""
    elif w < 0.58:
        vis, layers, wx = "8SM", [("SCT", max(base - 10, 8)), ("BKN", base + 20)], ""
    elif w < 0.72:
        vis, layers, wx = "6SM", [("BKN", base), ("OVC", base + 25)], "BR"
    elif w < 0.85:
        vis, layers, wx = "4SM", [("OVC", base)], "-RA BR"
    elif w < 0.93:
        vis, layers, wx = "2SM", [("OVC", base)], "RA BR"
    else:
        vis, layers, wx = "1 1/2SM", [("OVC", base)], "+RA BR"
    sky = ["%s%03d" % (c, h) for c, h in layers]
    return vis, sky, wx


def _fmt_temp(c):
    c = int(round(c))
    return "M%02d" % abs(c) if c < 0 else "%02d" % c


def _tgroup(temp, dew):
    def g(x):
        v = int(round(x * 10))
        return "%d%03d" % (1 if v < 0 else 0, abs(v))
    return "T" + g(temp) + g(dew)


def _slp(inhg):
    hpa = inhg * 33.8639
    return "SLP%03d" % (int(round(hpa * 10)) % 1000)


# --- METAR ------------------------------------------------------------------
def metar_at(cpos, cycle, station):
    c = conditions(cpos, cycle)
    obs = datetime.fromtimestamp(EPOCH_TS + cpos, timezone.utc)
    stamp = obs.strftime("%d%H") + "53Z"
    vis, sky, wx = _sky(c["w"])

    parts = [station, stamp, "AUTO", _fmt_wind(c["wdir"], c["wspd"], c["gust"]), vis]
    if wx:
        parts.append(wx)
    parts += sky
    parts.append("%s/%s" % (_fmt_temp(c["temp"]), _fmt_temp(c["dew"])))
    parts.append("A%04d" % int(round(c["inhg"] * 100)))

    rmk = ["RMK", "AO2", _slp(c["inhg"])]
    dp = c["inhg"] - _inhg_at(cpos - 3600, cycle)
    if dp <= -0.02:
        rmk.append("PRESFR")
    elif dp >= 0.02:
        rmk.append("PRESRR")
    rmk.append(_tgroup(c["temp"], c["dew"]))
    parts += rmk
    return " ".join(parts)


# --- TAF --------------------------------------------------------------------
def _taf_vis(w):
    return "P6SM" if w < 0.40 else _sky(w)[0]


def _taf_line(w, wdir, wspd, gust):
    vis, sky, wx = _sky(w)
    seg = [_fmt_wind(wdir, wspd, gust), _taf_vis(w)]
    if wx:
        seg.append(wx)
    seg += sky
    return " ".join(seg)


def _band(w):
    for i, edge in enumerate((0.20, 0.40, 0.58, 0.72, 0.85, 0.93)):
        if w < edge:
            return i
    return 6


def taf_at(cpos, cycle, station):
    obs = datetime.fromtimestamp(EPOCH_TS + cpos, timezone.utc)
    issue = obs.strftime("%d%H") + "20Z"
    start = (int(cpos // 3600) + 1) * 3600            # next whole hour
    s_dt = datetime.fromtimestamp(EPOCH_TS + start, timezone.utc)
    e_dt = datetime.fromtimestamp(EPOCH_TS + start + 24 * 3600, timezone.utc)
    valid = "%s/%s" % (s_dt.strftime("%d%H"), e_dt.strftime("%d%H"))

    c0 = conditions(start % cycle, cycle)
    lines = ["TAF", station, issue, valid,
             _taf_line(c0["w"], c0["wdir"], c0["wspd"], c0["gust"])]
    last = _band(c0["w"])
    for h in range(1, 24):
        cp = (start + h * 3600) % cycle
        c = conditions(cp, cycle)
        if _band(c["w"]) != last:
            fm = datetime.fromtimestamp(EPOCH_TS + start + h * 3600, timezone.utc)
            lines.append("FM%s00" % fm.strftime("%d%H"))
            lines.append(_taf_line(c["w"], c["wdir"], c["wspd"], c["gust"]))
            last = _band(c["w"])
    return " ".join(lines)


# --- current-time wrappers --------------------------------------------------
def _cpos():
    cyc = CFG.get("cycle_seconds", 172800)
    return (_now_ts() - EPOCH_TS) % cyc, cyc


def atis_at(cpos):
    """ATIS phonetic letter. Routine observations are hourly, so the ATIS code
    advances each hour and wraps A..Z — 'Information Quebec', etc."""
    return chr(ord("A") + (int(cpos // 3600) % 26))


def current_metar():
    cpos, cyc = _cpos()
    return metar_at(cpos, cyc, CFG.get("station", "KBAX"))


def current_atis():
    cpos, _ = _cpos()
    return atis_at(cpos)


def current_taf():
    cpos, cyc = _cpos()
    return taf_at(cpos, cyc, CFG.get("station", "KBAX"))


# --- serve ------------------------------------------------------------------
def serve():
    from flask import Flask, jsonify
    app = Flask(__name__)

    @app.route("/metar")
    def metar():
        return jsonify(station=CFG.get("station"), raw=current_metar(),
                       atis=current_atis())

    @app.route("/taf")
    def taf():
        return jsonify(station=CFG.get("station"), raw=current_taf())

    @app.route("/healthz")
    def healthz():
        return jsonify(status="ok")

    app.run(host="0.0.0.0", port=int(CFG.get("port", 8081)))


def main():
    global CFG, EPOCH_TS
    ap = argparse.ArgumentParser()
    ap.add_argument("--config")
    ap.add_argument("--selftest", type=int, metavar="HOURS",
                    help="print a METAR sweep over N hours of the cycle and exit")
    ap.add_argument("--taf", action="store_true", help="with --selftest, also print the TAF")
    a = ap.parse_args()

    if a.selftest:
        CFG = {"station": "KBAX", "cycle_seconds": 172800}
        EPOCH_TS = _epoch_ts(CFG)
        cyc = CFG["cycle_seconds"]
        for h in range(a.selftest):
            print(metar_at(h * 3600, cyc, "KBAX"))
        if a.taf:
            print("\n" + taf_at(0, cyc, "KBAX"))
            print(taf_at(15 * 3600, cyc, "KBAX"))
        return

    CFG = json.load(open(a.config))
    EPOCH_TS = _epoch_ts(CFG)
    serve()


if __name__ == "__main__":
    main()
