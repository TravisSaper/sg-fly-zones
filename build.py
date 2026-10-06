# /// script
# dependencies = ["shapely"]
# ///
"""Rebuild zones.geojson (merged no-fly zones) and spots.geojson (open spaces outside them). Run: uv run build.py"""
import json, math, time, urllib.parse, urllib.request
from shapely.geometry import shape, mapping, Point, Polygon, LineString
from shapely.ops import transform, unary_union, polygonize

DATASETS = ["d_15457a8f67905fb6ed890fca2ebac5f7",  # NParks no-drone parks
            "d_3d05a22ad76368500bd6d5ef72367123"]  # Air Navigation Act protected areas
# CAAS: no drones within 5 km of an aerodrome. ponytail: hand-placed centres, swap for official polygons if precision matters
AERODROMES = [(103.9915, 1.3644), (103.8678, 1.4170), (103.9097, 1.3604), (103.7086, 1.3871), (103.8130, 1.4253)]

# Local metres projection; fine at Singapore's size (~50 km across)
KX, KY = 111320 * math.cos(math.radians(1.35)), 110574
to_m = lambda x, y, z=None: (x * KX, y * KY)
to_deg = lambda x, y, z=None: (x / KX, y / KY)


def fetch(url, data=None):
    req = urllib.request.Request(url, data, headers={"User-Agent": "sg-fly-zones"})
    return json.load(urllib.request.urlopen(req, timeout=300))


def save(path, features):
    for f in features:  # 5 decimals ≈ 1 m
        f["geometry"] = json.loads(json.dumps(mapping(transform(to_deg, f["geometry"]))), parse_float=lambda s: round(float(s), 5))
    json.dump({"type": "FeatureCollection", "features": features}, open(path, "w"), separators=(",", ":"))


def parts(g):
    return [p for p in getattr(g, "geoms", [g]) if p.geom_type == "Polygon"]


shapes = []
for ds in DATASETS:
    url = fetch(f"https://api-open.data.gov.sg/v1/public/api/datasets/{ds}/poll-download")["data"]["url"]
    shapes += [transform(to_m, shape(f["geometry"])).buffer(0) for f in fetch(url)["features"]]
    time.sleep(3)  # data.gov.sg rate limit
shapes += [Point(to_m(*c)).buffer(5000, 64) for c in AERODROMES]

# Close hairline gaps between neighbouring zones (+/-3 m), then drop leftover slivers/holes under 5000 m²
merged = unary_union(shapes).buffer(3).buffer(-3)
merged = unary_union([Polygon(p.exterior, [h for h in p.interiors if Polygon(h).area >= 5000])
                      for p in getattr(merged, "geoms", [merged])])
merged = merged.simplify(2)  # 2 m tolerance
save("zones.geojson", [{"type": "Feature", "properties": {}, "geometry": merged}])
print(f"{len(shapes)} zones -> {len(parts(merged))} blocks")

# Open public spaces from OpenStreetMap, minus every no-fly zone
OVERPASS = """[out:json][timeout:180];
(
  nwr["leisure"~"^(park|recreation_ground|common|pitch)$"]["access"!~"^(private|no|customers)$"](1.15,103.59,1.48,104.1);
  nwr["natural"="beach"](1.15,103.59,1.48,104.1);
);
out geom;"""
MIN_AREA = 3000  # m²; ponytail: one size fits all, tune if tiny pitches or huge parks dominate
for mirror in ["https://overpass-api.de", "https://overpass.private.coffee", "https://overpass.kumi.systems"]:
    try:  # Overpass servers 504 when busy; try the next one
        osm = fetch(f"{mirror}/api/interpreter", urllib.parse.urlencode({"data": OVERPASS}).encode())["elements"]
        break
    except OSError as e:  # HTTP errors and timeouts
        print(f"{mirror}: {e}")
else:
    raise SystemExit("all Overpass mirrors failed")
spots = []
for e in osm:
    if e["type"] == "way" and len(e.get("geometry", [])) >= 4:
        lines = [e["geometry"]]
    elif e["type"] == "relation":  # multipolygon: stitch outer rings
        lines = [m["geometry"] for m in e.get("members", []) if m.get("role") == "outer" and m.get("geometry")]
    else:
        continue
    poly = unary_union(list(polygonize([LineString([to_m(p["lon"], p["lat"]) for p in l]) for l in lines if len(l) > 1])))
    tags = e.get("tags", {})
    kind = "beach" if tags.get("natural") == "beach" else tags.get("leisure")
    spots.append(({"name": tags.get("name", ""), "kind": kind}, poly.buffer(0).simplify(2).difference(merged)))  # simplify before cutting so edges match zones exactly

# Biggest first; smaller spaces lose whatever a bigger one already covers, so nothing draws twice
# ponytail: O(n²) running union, fine for ~4k shapes; STRtree if it gets slow
taken, features = Polygon(), []
for props, g in sorted(spots, key=lambda s: -s[1].area):
    rest = g.difference(taken)
    taken = taken.union(g)
    features += [{"type": "Feature", "properties": props, "geometry": p} for p in parts(rest) if p.area >= MIN_AREA]
spots = features
# Self-check: a "spot" must never sit inside a no-fly zone (1 m² slack for float noise)
assert all(f["geometry"].intersection(merged).area < 1 for f in spots), "spot overlaps a no-fly zone"
save("spots.geojson", spots)
print(f"{len(osm)} OSM spaces -> {len(spots)} spots outside no-fly zones")
