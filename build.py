# /// script
# dependencies = ["shapely"]
# ///
"""Rebuild zones.geojson: download no-fly datasets, add 5 km aerodrome circles, merge overlaps. Run: uv run build.py"""
import json, math, time, urllib.request
from shapely.geometry import shape, mapping, Point
from shapely.ops import transform, unary_union

DATASETS = ["d_15457a8f67905fb6ed890fca2ebac5f7",  # NParks no-drone parks
            "d_3d05a22ad76368500bd6d5ef72367123"]  # Air Navigation Act protected areas
# CAAS: no drones within 5 km of an aerodrome. ponytail: hand-placed centres, swap for official polygons if precision matters
AERODROMES = [(103.9915, 1.3644), (103.8678, 1.4170), (103.9097, 1.3604), (103.7086, 1.3871), (103.8130, 1.4253)]

# Local metres projection; fine at Singapore's size (~50 km across)
KX, KY = 111320 * math.cos(math.radians(1.35)), 110574
to_m = lambda x, y, z=None: (x * KX, y * KY)
to_deg = lambda x, y, z=None: (x / KX, y / KY)


def fetch(url):
    return json.load(urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "sg-fly-zones"})))


shapes = []
for ds in DATASETS:
    url = fetch(f"https://api-open.data.gov.sg/v1/public/api/datasets/{ds}/poll-download")["data"]["url"]
    shapes += [transform(to_m, shape(f["geometry"])).buffer(0) for f in fetch(url)["features"]]
    time.sleep(3)  # data.gov.sg rate limit
shapes += [Point(to_m(*c)).buffer(5000, 64) for c in AERODROMES]

merged = transform(to_deg, unary_union(shapes).simplify(2))  # 2 m tolerance
geom = json.loads(json.dumps(mapping(merged)), parse_float=lambda s: round(float(s), 5))
json.dump({"type": "Feature", "properties": {}, "geometry": geom}, open("zones.geojson", "w"), separators=(",", ":"))
print(f"{len(shapes)} zones -> {len(getattr(merged, 'geoms', [merged]))} blocks")
