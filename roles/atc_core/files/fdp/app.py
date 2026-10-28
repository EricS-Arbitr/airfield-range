#!/usr/bin/env python3
"""Flight Data Processor (dev stub).

Reads readsb's aircraft.json (live decoded feed) and serves electronic flight
strips + a combined radar/strips/weather ops page for the CWP kiosk, modeled on
a tower Information Display System (IDS): a live weather band across the top
(ATIS code, active runway, wind, altimeter, visibility, ceiling, temp/dewpoint,
flight category) with the full raw+decoded METAR and the TAF below.

Weather is proxied SERVER-SIDE: the FDP fetches METAR/TAF from the weather host
over the prod network and the kiosk browser only ever talks to the FDP, so
there is no cross-origin fetch and no hard dependency on the weather host being
reachable from the kiosk. Landing detection and the fuel-requisition trigger
(design §4C/§4D) are stubbed and gated.

TODO:
  * Persist strips/landings to SQLite (config.db); add the reset path.
  * Landing detection from aircraft.json (alt/gs/on-ground transition at field)
    -> on a HEAVY landing, POST to fuel_requisition_url when enabled.
  * RANGE-VULN (gated by vulns_enabled, default false): SQLi in the strip
    filter, unauthenticated /api/update_squawk. Mark each `# RANGE-VULN:` and
    document in docs/atc_instructor_answer_key.md.

    python3 app.py --config config.json      # serve
    python3 app.py --selftest                 # decode a few METARs and exit
"""
import argparse
import json
import re
import time
import urllib.request

CFG = {}
_WX = {"t": 0.0, "data": None}       # last-good weather, short-cached


# --- aircraft (strips) ------------------------------------------------------
def _aircraft():
    path = CFG.get("aircraft_json", "")
    try:
        with open(path) as fh:
            return json.load(fh).get("aircraft", [])
    except (OSError, ValueError):
        return []


# --- METAR decode -----------------------------------------------------------
_SKY = re.compile(r"^(FEW|SCT|BKN|OVC|VV)(\d{3})$")
_WIND = re.compile(r"^(\d{3}|VRB)(\d{2,3})(?:G(\d{2,3}))?KT$")
_TEMP = re.compile(r"^(M?\d{2})/(M?\d{2})$")
_WX_CODES = re.compile(r"^(\+|-|VC)?(MI|BC|DR|BL|SH|TS|FZ|PR)?"
                       r"(DZ|RA|SN|SG|IC|PL|GR|GS|UP|BR|FG|FU|VA|DU|SA|HZ|PY|PO|SQ|FC|SS|DS)+$")


def _n(tok):
    return -int(tok[1:]) if tok.startswith("M") else int(tok)


def decode_metar(raw):
    """Parse a (self-generated, FAA-style) METAR into display fields."""
    d = {"wind_dir": None, "wind_spd": 0, "wind_gust": 0, "wind_txt": "CALM",
         "vis": None, "vis_txt": "", "wx": "", "sky": [], "ceiling": None,
         "temp": None, "dew": None, "altimeter": None, "cat": "VFR"}
    toks = raw.split()
    i, wx = 1, []                       # tok[0] is the station
    while i < len(toks):
        t = toks[i]
        if t == "RMK":
            break
        if t in ("AUTO", "COR") or re.match(r"^\d{6}Z$", t):
            i += 1
            continue
        m = _WIND.match(t)
        if m:
            if m.group(1) != "VRB":
                d["wind_dir"] = int(m.group(1))
            d["wind_spd"] = int(m.group(2))
            d["wind_gust"] = int(m.group(3)) if m.group(3) else 0
            if d["wind_spd"] == 0:
                d["wind_txt"] = "CALM"
            else:
                hd = "VRB" if m.group(1) == "VRB" else "%03d" % d["wind_dir"]
                d["wind_txt"] = "%s @ %dkt" % (hd, d["wind_spd"])
                if d["wind_gust"]:
                    d["wind_txt"] += " G%d" % d["wind_gust"]
            i += 1
            continue
        # visibility: "10SM", "1/2SM", or "1 1/2SM" (whole + fraction)
        if re.match(r"^\d+$", t) and i + 1 < len(toks) and re.match(r"^\d/\dSM$", toks[i + 1]):
            whole, frac = int(t), toks[i + 1][:-2]
            num, den = frac.split("/")
            d["vis"] = whole + int(num) / int(den)
            d["vis_txt"] = "%s %sSM" % (t, frac)
            i += 2
            continue
        if t.endswith("SM"):
            body = t[:-2].lstrip("MP")
            if "/" in body:
                num, den = body.split("/")
                d["vis"] = int(num) / int(den)
            else:
                d["vis"] = float(body)
            d["vis_txt"] = t
            i += 1
            continue
        m = _SKY.match(t)
        if m:
            base = int(m.group(2)) * 100
            d["sky"].append({"cover": m.group(1), "ft": base})
            if m.group(1) in ("BKN", "OVC", "VV") and d["ceiling"] is None:
                d["ceiling"] = base
            i += 1
            continue
        m = _TEMP.match(t)
        if m:
            d["temp"], d["dew"] = _n(m.group(1)), _n(m.group(2))
            i += 1
            continue
        if re.match(r"^A\d{4}$", t):
            d["altimeter"] = int(t[1:]) / 100.0
            i += 1
            continue
        if t not in ("NSC", "SKC", "CLR", "NCD") and _WX_CODES.match(t):
            wx.append(t)
        i += 1
    d["wx"] = " ".join(wx)
    d["cat"] = _flight_cat(d["ceiling"], d["vis"])
    return d


def _flight_cat(ceiling, vis):
    c = ceiling if ceiling is not None else 99999
    v = vis if vis is not None else 99
    if c < 500 or v < 1:
        return "LIFR"
    if c < 1000 or v < 3:
        return "IFR"
    if c <= 3000 or v <= 5:
        return "MVFR"
    return "VFR"


def _angdiff(a, b):
    return abs(((a - b + 180) % 360) - 180)


def active_runway(wind_dir, airfield):
    """Pick the runway end most into the wind. Calm -> the first runway's low end."""
    runways = airfield.get("runways", [])
    if not runways:
        return ""
    ends = []
    for r in runways:
        h = r.get("true_hdg", 0)
        for hdg in (h, (h + 180) % 360):
            num = int(round(hdg / 10.0)) % 36
            ends.append(("%02d" % (36 if num == 0 else num), hdg))
    if wind_dir is None:
        return min(ends, key=lambda e: e[1])[0]       # low-numbered end
    return min(ends, key=lambda e: _angdiff(e[1], wind_dir))[0]


# --- D-ATIS (plain-language text; no audio in the range) --------------------
_PHON = {"A": "ALPHA", "B": "BRAVO", "C": "CHARLIE", "D": "DELTA", "E": "ECHO",
         "F": "FOXTROT", "G": "GOLF", "H": "HOTEL", "I": "INDIA", "J": "JULIET",
         "K": "KILO", "L": "LIMA", "M": "MIKE", "N": "NOVEMBER", "O": "OSCAR",
         "P": "PAPA", "Q": "QUEBEC", "R": "ROMEO", "S": "SIERRA", "T": "TANGO",
         "U": "UNIFORM", "V": "VICTOR", "W": "WHISKEY", "X": "XRAY",
         "Y": "YANKEE", "Z": "ZULU"}
_COVER = {"FEW": "FEW", "SCT": "SCATTERED", "BKN": "BROKEN", "OVC": "OVERCAST",
          "VV": "INDEFINITE CEILING"}
_WXWORD = {"RA": "RAIN", "SN": "SNOW", "DZ": "DRIZZLE", "BR": "MIST", "FG": "FOG",
           "HZ": "HAZE", "FU": "SMOKE", "GR": "HAIL", "GS": "SMALL HAIL",
           "PL": "ICE PELLETS", "SG": "SNOW GRAINS", "UP": "UNKNOWN PRECIP"}
_WXDESC = {"SH": "SHOWERS", "TS": "THUNDERSTORM", "FZ": "FREEZING",
           "MI": "SHALLOW", "BC": "PATCHY", "BL": "BLOWING", "DR": "DRIFTING"}


def _spoken_wx(wx):
    out = []
    for tok in wx.split():
        pre, t = "", tok
        if t[:1] == "+":
            pre, t = "HEAVY ", t[1:]
        elif t[:1] == "-":
            pre, t = "LIGHT ", t[1:]
        elif t[:2] == "VC":
            pre, t = "VICINITY ", t[2:]
        words = [_WXWORD.get(t[i:i + 2], _WXDESC.get(t[i:i + 2], t[i:i + 2]))
                 for i in range(0, len(t), 2)]
        out.append((pre + " ".join(words)).strip())
    return ", ".join(out)


def _spoken_sky(sky):
    if not sky:
        return "SKY CLEAR"
    segs, ceiling_done = [], False
    for lyr in sky:
        word = _COVER.get(lyr["cover"], lyr["cover"])
        if lyr["cover"] in ("BKN", "OVC", "VV") and not ceiling_done:
            segs.append("CEILING %d %s" % (lyr["ft"], word))
            ceiling_done = True
        else:
            segs.append("%d %s" % (lyr["ft"], word))
    return ", ".join(segs)


def datis_text(d, letter, active_rwy, field_name, raw):
    """Assemble D-ATIS phraseology from the decoded METAR + active runway."""
    word = _PHON.get(letter, letter)
    m = re.search(r"\b\d{2}(\d{2})(\d{2})Z\b", raw)
    zt = ("%s%sZ" % (m.group(1), m.group(2))) if m else ""
    if d["wind_spd"] == 0:
        wind = "WIND CALM"
    elif d["wind_dir"] is None:
        wind = "WIND VARIABLE AT %d" % d["wind_spd"]
    else:
        wind = "WIND %03d AT %d" % (d["wind_dir"], d["wind_spd"])
        if d["wind_gust"]:
            wind += " GUST %d" % d["wind_gust"]
    vis = "VISIBILITY %s" % d["vis_txt"].replace("SM", "").strip() if d["vis_txt"] else ""
    if d["wx"]:
        vis += ", " + _spoken_wx(d["wx"])

    def _t(v):
        return ("MINUS %d" % abs(v)) if v is not None and v < 0 else str(v)

    temp = "TEMPERATURE %s, DEWPOINT %s" % (_t(d["temp"]), _t(d["dew"]))
    altim = "ALTIMETER %04d" % int(round((d["altimeter"] or 0) * 100))
    rwy = "LANDING AND DEPARTING RUNWAY %s." % active_rwy if active_rwy else ""
    lines = [
        "%s ATIS INFORMATION %s. %s." % (field_name.upper(), word, zt),
        "%s. %s." % (wind, vis),
        "%s." % _spoken_sky(d["sky"]),
        "%s. %s." % (temp, altim),
        rwy,
        "ADVISE ON INITIAL CONTACT YOU HAVE INFORMATION %s." % word,
    ]
    return "\n".join(ln for ln in lines if ln.strip())


# --- weather proxy (server-side, short-cached, last-good on failure) --------
def _get_json(url):
    with urllib.request.urlopen(url, timeout=1.5) as r:
        return json.loads(r.read().decode())


def weather():
    now = time.time()
    if _WX["data"] and now - _WX["t"] < 10:
        return _WX["data"]
    out = {"ok": False, "raw": "", "taf": "", "atis": "", "datis": "",
           "decoded": None, "active_rwy": "", "stale": _WX["data"] is not None}
    try:
        m = _get_json(CFG.get("weather_metar_url", ""))
        out["raw"] = m.get("raw", "")
        out["atis"] = m.get("atis", "")
        out["decoded"] = decode_metar(out["raw"]) if out["raw"] else None
        if out["decoded"]:
            af = CFG.get("airfield", {})
            out["active_rwy"] = active_runway(out["decoded"]["wind_dir"], af)
            out["datis"] = datis_text(out["decoded"], out["atis"],
                                      out["active_rwy"],
                                      af.get("name", af.get("ident_icao", "")),
                                      out["raw"])
        try:
            out["taf"] = _get_json(CFG.get("weather_taf_url", "")).get("raw", "")
        except Exception:
            out["taf"] = _WX["data"]["taf"] if _WX["data"] else ""
        out["ok"], out["stale"] = True, False
        _WX["data"], _WX["t"] = out, now
        return out
    except Exception:
        if _WX["data"]:                                # serve last-good, flagged
            stale = dict(_WX["data"], stale=True)
            return stale
        return out


# --- ops page (IDS-style) ---------------------------------------------------
OPS_PAGE = """<!doctype html><html><head><meta charset="utf-8">
<title>Blackstone AUX — ATC</title>
<style>
:root{--bg:#0b0f14;--fg:#cfe3ff;--line:#1d2a3a;--hdr:#6fb3ff;--dim:#5a7694}
*{box-sizing:border-box}body{background:var(--bg);color:var(--fg);font:14px monospace;margin:0}
.ids{display:flex;flex-wrap:wrap;align-items:stretch;gap:1px;background:var(--line);
     border-bottom:1px solid var(--line)}
.ids .cell{background:#0e151e;padding:6px 12px;flex:1 1 auto;min-width:92px}
.ids .lbl{color:var(--dim);font-size:11px;letter-spacing:.06em}
.ids .val{font-size:18px;font-weight:bold;white-space:nowrap}
.atis{font-size:26px}.cat{font-size:20px;padding:6px 14px}
.VFR{color:#3ad17a}.MVFR{color:#4aa3ff}.IFR{color:#ff5d5d}.LIFR{color:#ff6ad5}
.stale{color:#e0a53a}
.wrap{display:flex;height:calc(100vh - 56px)}
.scope{flex:3;border-right:1px solid var(--line)}.scope iframe{width:100%;height:100%;border:0}
.right{flex:2;display:flex;flex-direction:column;min-width:0}
.strips{flex:1;padding:12px;overflow:auto}.wxbox{border-top:1px solid var(--line);
    padding:10px 12px;max-height:40%;overflow:auto}
h2{color:var(--hdr);margin:0 0 8px;font-size:15px}
table{width:100%;border-collapse:collapse}td,th{border-bottom:1px solid var(--line);padding:4px;text-align:left}
.datis{color:#e6eefc;white-space:pre-wrap;word-break:break-word;margin:2px 0 8px;line-height:1.35}
.raw{color:#9fe0b0;word-break:break-word;margin:4px 0}
.taf{color:#9db9e0;word-break:break-word;white-space:pre-wrap;margin:2px 0}
.k{color:var(--dim)}
</style></head><body>

<div class="ids" id="ids">
  <div class="cell"><div class="lbl">ATIS</div><div class="val atis" id="atis">–</div></div>
  <div class="cell"><div class="lbl">ACTIVE RWY</div><div class="val" id="rwy">–</div></div>
  <div class="cell"><div class="lbl">WIND</div><div class="val" id="wind">–</div></div>
  <div class="cell"><div class="lbl">ALTIMETER</div><div class="val" id="alt">–</div></div>
  <div class="cell"><div class="lbl">VISIBILITY</div><div class="val" id="vis">–</div></div>
  <div class="cell"><div class="lbl">CEILING</div><div class="val" id="ceil">–</div></div>
  <div class="cell"><div class="lbl">TEMP / DEW</div><div class="val" id="td">–</div></div>
  <div class="cell"><div class="lbl">CATEGORY</div><div class="val cat" id="cat">–</div></div>
</div>

<div class="wrap">
  <div class="scope">
  {% if tar %}<iframe src="{{ tar }}"></iframe>
  {% else %}<div style="padding:12px"><h2>RADAR — {{ af.ident_icao }}</h2>
  <p>tar1090 URL not configured.</p></div>{% endif %}</div>
  <div class="right">
    <div class="strips"><h2>FLIGHT STRIPS — {{ af.ident_icao }}</h2>
      <table><thead><tr><th>Callsign</th><th>Hex</th><th>Alt</th><th>GS</th><th>Squawk</th></tr></thead>
      <tbody id="strips"></tbody></table>
      <p id="foot" class="k">loading…</p></div>
    <div class="wxbox"><h2>WEATHER — {{ af.ident_icao }} <span id="wxflag"></span></h2>
      <div class="k">D-ATIS</div><div class="datis" id="datis">—</div>
      <div class="k">METAR</div><div class="raw" id="metar">METAR unavailable</div>
      <div class="k">TAF</div><div class="taf" id="taf">—</div></div>
  </div>
</div>

<script>
function txt(id,v){document.getElementById(id).textContent=v;}
async function strips(){
  try{
    const ac = await (await fetch('/api/strips')).json();
    document.getElementById('strips').innerHTML = ac.map(a =>
      `<tr><td>${a.flight||''}</td><td>${a.hex||''}</td><td>${a.alt_baro||''}</td>`+
      `<td>${a.gs||''}</td><td>${a.squawk||''}</td></tr>`).join('');
    txt('foot', `updated every 2s · ${ac.length} tracked`);
  }catch(e){ txt('foot','feed unreachable'); }
}
async function wx(){
  try{
    const w = await (await fetch('/api/weather')).json();
    const d = w.decoded || {};
    txt('atis', w.atis ? w.atis : '–');
    txt('rwy', w.active_rwy ? ('RWY '+w.active_rwy) : '–');
    txt('wind', d.wind_txt || '–');
    txt('alt', d.altimeter ? d.altimeter.toFixed(2)+'"' : '–');
    txt('vis', (d.vis_txt||'–') + (d.wx ? ' '+d.wx : ''));
    txt('ceil', d.ceiling!=null ? (d.ceiling+' ft') : 'unlimited');
    txt('td', (d.temp!=null) ? (d.temp+'° / '+d.dew+'°C') : '–');
    const cat = document.getElementById('cat');
    cat.textContent = d.cat || '–';
    cat.className = 'val cat ' + (d.cat||'');
    txt('datis', w.datis || '—');
    txt('metar', w.raw || 'METAR unavailable');
    txt('taf', w.taf || '—');
    const flag = document.getElementById('wxflag');
    flag.textContent = w.stale ? '(last known — feed stale)' : '';
    flag.className = w.stale ? 'stale' : '';
  }catch(e){ document.getElementById('wxflag').textContent='(unavailable)'; }
}
strips(); setInterval(strips, 2000);
wx(); setInterval(wx, 15000);
</script></body></html>"""


# --- serve ------------------------------------------------------------------
def serve():
    from flask import Flask, jsonify, render_template_string
    app = Flask(__name__)

    @app.route("/")
    def ops():
        return render_template_string(OPS_PAGE, af=CFG.get("airfield", {}),
                                      tar=CFG.get("tar1090_url", ""))

    @app.route("/api/strips")
    def strips_api():
        return jsonify(_aircraft())

    @app.route("/api/weather")
    def weather_api():
        return jsonify(weather())

    @app.route("/healthz")
    def healthz():
        return jsonify(status="ok", vulns=CFG.get("vulns_enabled", False),
                       count=len(_aircraft()))

    app.run(host="0.0.0.0", port=int(CFG.get("port", 8080)))


def main():
    global CFG
    ap = argparse.ArgumentParser()
    ap.add_argument("--config")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        af = {"ident_icao": "KBAX", "name": "Blackstone Aux",
              "runways": [{"ident": "02/20", "true_hdg": 20},
                          {"ident": "11/29", "true_hdg": 110}]}
        samples = [
            "KBAX 010053Z AUTO 19004KT 10SM FEW041 06/M04 A3008 RMK AO2 SLP186 T00601040",
            "KBAX 011653Z AUTO 25018G26KT 1 1/2SM +RA BR OVC005 20/19 A2975 RMK AO2 SLP075 PRESFR",
            "KBAX 012053Z AUTO 26016KT 4SM -RA BR OVC013 14/11 A2967 RMK AO2 SLP047",
            "KBAX 020653Z AUTO 30013KT 8SM SCT011 BKN041 05/00 A2970 RMK AO2 SLP059",
        ]
        for s in samples:
            d = decode_metar(s)
            rwy = active_runway(d["wind_dir"], af)
            print("%-5s rwy%s  %-14s vis=%-7s ceil=%-7s %s/%s %.2f"
                  % (d["cat"], rwy, d["wind_txt"], d["vis_txt"],
                     ("%dft" % d["ceiling"]) if d["ceiling"] else "none",
                     d["temp"], d["dew"], d["altimeter"]))
            print(datis_text(d, "V", rwy, af["name"], s))
            print()
        return
    CFG = json.load(open(a.config))
    serve()


if __name__ == "__main__":
    main()
