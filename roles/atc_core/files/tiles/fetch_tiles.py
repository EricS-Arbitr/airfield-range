#!/usr/bin/env python3
"""Download XYZ map tiles for a bounded area into a local directory.

DEV convenience so tar1090 has an offline basemap without browser egress. The
same {z}/{x}/{y}.png tree is what an air-gapped range stages from Nexus — only
the source changes. Idempotent (skips tiles already present). Honours
http(s)_proxy from the environment. Bounded area + modest zoom, one-time: do not
point this at a public tile server for anything beyond a small dev fetch.
"""
import argparse
import math
import os
import time
import urllib.request


def deg2num(lat, lon, z):
    n = 2 ** z
    x = int((lon + 180.0) / 360.0 * n)
    la = math.radians(lat)
    y = int((1.0 - math.log(math.tan(la) + 1.0 / math.cos(la)) / math.pi) / 2.0 * n)
    return max(0, min(n - 1, x)), max(0, min(n - 1, y))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", required=True)   # URL template with {z}/{x}/{y}
    ap.add_argument("--out", required=True)
    ap.add_argument("--lat-min", type=float, required=True)
    ap.add_argument("--lat-max", type=float, required=True)
    ap.add_argument("--lon-min", type=float, required=True)
    ap.add_argument("--lon-max", type=float, required=True)
    ap.add_argument("--zmin", type=int, default=6)
    ap.add_argument("--zmax", type=int, default=11)
    ap.add_argument("--delay", type=float, default=0.05)
    ap.add_argument("--ua", default="atc-range-dev/1.0 (lab offline basemap)")
    a = ap.parse_args()

    got = skip = err = 0
    for z in range(a.zmin, a.zmax + 1):
        x0, y0 = deg2num(a.lat_max, a.lon_min, z)   # NW corner
        x1, y1 = deg2num(a.lat_min, a.lon_max, z)   # SE corner
        for x in range(min(x0, x1), max(x0, x1) + 1):
            for y in range(min(y0, y1), max(y0, y1) + 1):
                dest = os.path.join(a.out, str(z), str(x), f"{y}.png")
                if os.path.exists(dest) and os.path.getsize(dest) > 0:
                    skip += 1
                    continue
                os.makedirs(os.path.dirname(dest), exist_ok=True)
                url = a.src.replace("{z}", str(z)).replace("{x}", str(x)).replace("{y}", str(y))
                try:
                    req = urllib.request.Request(url, headers={"User-Agent": a.ua})
                    with urllib.request.urlopen(req, timeout=20) as r:
                        data = r.read()
                    with open(dest, "wb") as f:
                        f.write(data)
                    got += 1
                    time.sleep(a.delay)
                except Exception as e:  # noqa: BLE001 - best-effort; keep going
                    err += 1
                    if err <= 5:
                        print(f"  skip {url}: {e}")
        print(f"  z{z} done (got={got} skip={skip} err={err})", flush=True)
    print(f"tiles: fetched={got} skipped={skip} errors={err} -> {a.out}")


if __name__ == "__main__":
    main()
