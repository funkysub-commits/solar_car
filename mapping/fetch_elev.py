#!/usr/bin/env python3
"""Fetch driving-segment elevations from OpenTopoData ned10m (10 m USGS 3DEP).

Usage:
    python fetch_elev.py                # all days -> all_elev.json
    python fetch_elev.py --day 1       # just Day 1 (same JSON shape)
    python fetch_elev.py -o out.json   # different output file

Public API limits: 100 locations/request, 1 req/s, 1000 req/day. The full
route is ~13.2k pts -> ~133 requests. Reads route.kml; the output is the
shape viewer.html / build_viewer.py consume ({"grand": ..., "days": [...]}).
Per-day cumulative distance resets to 0; each driving segment starts where
the previous ended (no phantom trailer-gap distance).
"""
import argparse
import xml.etree.ElementTree as ET
import json, math, time, sys, urllib.request, urllib.parse

KML = "route.kml"
NS = {"k": "http://www.opengis.net/kml/2.2"}
API = "https://api.opentopodata.org/v1/ned10m"
BATCH = 100          # max locations per request
SLEEP = 1.1          # respect 1 req/s public limit


def hav(a, b):
    R = 6371000.0
    la1, lo1, la2, lo2 = map(math.radians, [a[1], a[0], b[1], b[0]])
    h = math.sin((la2-la1)/2)**2 + math.cos(la1)*math.cos(la2)*math.sin((lo2-lo1)/2)**2
    return 2*R*math.asin(math.sqrt(h))


def parse_driving_by_day():
    root = ET.parse(KML).getroot()
    doc = root.find(".//k:Document", NS)
    def txt(e, tag):
        x = e.find("k:"+tag, NS)
        return x.text.strip() if x is not None and x.text else ""
    days = []
    for f in doc.findall("k:Folder", NS):
        fname = txt(f, "name")
        if fname.endswith("Stops") or not fname.startswith("Day"):
            continue
        segs = []
        for pm in f.findall("k:Placemark", NS):
            name = txt(pm, "name")
            if "Driving" not in name:
                continue
            ls = pm.find(".//k:LineString", NS)
            coords = [tuple(map(float, t.split(",")[:2]))
                      for t in ls.find("k:coordinates", NS).text.split()]
            segs.append({"name": name, "coords": coords})
        if segs:
            days.append({"day": fname, "segments": segs})
    return days


def fetch_elev(coords):
    """coords: list of (lon,lat). Returns list of elevations (float or None)."""
    out = []
    n = len(coords)
    for i in range(0, n, BATCH):
        chunk = coords[i:i+BATCH]
        locs = "|".join(f"{lat:.6f},{lon:.6f}" for lon, lat in chunk)
        url = API + "?" + urllib.parse.urlencode({"locations": locs})
        for attempt in range(5):
            try:
                with urllib.request.urlopen(url, timeout=60) as r:
                    data = json.load(r)
                if data.get("status") != "OK":
                    raise RuntimeError(data.get("error", data.get("status")))
                out.extend(p["elevation"] for p in data["results"])
                break
            except Exception as e:
                wait = SLEEP * (attempt + 1) * 2
                print(f"    retry {attempt+1} ({e}); sleeping {wait:.1f}s", file=sys.stderr)
                time.sleep(wait)
        else:
            raise SystemExit(f"failed batch at {i}")
        print(f"    {min(i+BATCH, n)}/{n} pts", file=sys.stderr)
        time.sleep(SLEEP)
    return out


def interp_nulls(eles):
    eles = list(eles); n = len(eles)
    for i in range(n):
        if eles[i] is None:
            j = i-1
            while j >= 0 and eles[j] is None: j -= 1
            k = i+1
            while k < n and eles[k] is None: k += 1
            if j >= 0 and k < n:
                eles[i] = eles[j] + (eles[k]-eles[j])*(i-j)/(k-j)
            elif j >= 0: eles[i] = eles[j]
            elif k < n: eles[i] = eles[k]
            else: eles[i] = 0.0
    return eles


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--day", type=int, default=None,
                    help="fetch only this day (e.g. --day 1); default: all days")
    ap.add_argument("-o", "--out", default="all_elev.json",
                    help="output file (default: all_elev.json)")
    args = ap.parse_args()

    days = parse_driving_by_day()
    if args.day is not None:
        want = f"Day {args.day}"
        days = [d for d in days if d["day"] == want]
        if not days:
            raise SystemExit(f"no '{want}' driving segments found in {KML}")

    total_pts = sum(len(s["coords"]) for d in days for s in d["segments"])
    print(f"Days: {len(days)}; total driving pts: {total_pts} "
          f"(~{-(-total_pts // BATCH)} requests at {SLEEP:.1f}s each)",
          file=sys.stderr)
    out_days = []
    for d in days:
        print(f"=== {d['day']} ({sum(len(s['coords']) for s in d['segments'])} pts) ===",
              file=sys.stderr)
        cum = 0.0
        out_segs = []
        for s in d["segments"]:
            print(f"  {s['name']} ({len(s['coords'])} pts)...", file=sys.stderr)
            eles = interp_nulls(fetch_elev(s["coords"]))
            prev = None
            pts = []
            for (lon, lat), ele in zip(s["coords"], eles):
                if prev is not None:
                    cum += hav(prev, (lon, lat))
                prev = (lon, lat)
                pts.append({"lat": round(lat, 6), "lon": round(lon, 6),
                            "ele": round(ele, 1), "d": round(cum, 1)})
            out_segs.append({"name": s["name"], "points": pts})
        flat = [p for s in out_segs for p in s["points"]]
        eles = [p["ele"] for p in flat]
        ascent = sum(max(0, eles[i+1]-eles[i]) for i in range(len(eles)-1))
        descent = sum(max(0, eles[i]-eles[i+1]) for i in range(len(eles)-1))
        bounds = [{"name": s["name"], "at_km": round(s["points"][-1]["d"]/1000, 2)}
                  for s in out_segs]
        out_days.append({
            "day": d["day"],
            "summary": {
                "n_points": len(flat),
                "distance_km": round(cum/1000, 2),
                "min_ele": round(min(eles), 1), "max_ele": round(max(eles), 1),
                "ascent_m": round(ascent), "descent_m": round(descent),
                "segment_bounds": bounds,
            },
            "segments": out_segs,
        })
        print(f"  -> {out_days[-1]['summary']['distance_km']} km, "
              f"+{out_days[-1]['summary']['ascent_m']}/-{out_days[-1]['summary']['descent_m']} m",
              file=sys.stderr)

    grand = {
        "source": "USGS 3DEP 10 m via OpenTopoData ned10m",
        "distance_km": round(sum(d["summary"]["distance_km"] for d in out_days), 2),
        "ascent_m": sum(d["summary"]["ascent_m"] for d in out_days),
        "descent_m": sum(d["summary"]["descent_m"] for d in out_days),
        "n_points": sum(d["summary"]["n_points"] for d in out_days),
    }
    json.dump({"grand": grand, "days": out_days},
              open(args.out, "w"), separators=(",", ":"))
    print(json.dumps(grand, indent=2), file=sys.stderr)
    print(f"Wrote {args.out}", file=sys.stderr)


if __name__ == "__main__":
    main()
