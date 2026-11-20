#!/usr/bin/env python3
"""Download the EOX Sentinel-2 cloudless 2023 tiles for the main map.

Tiles: data/tiles/{prefix}_z{z}_{x}_{y}.jpg (GoogleMapsCompatible, EPSG:3857).
Fetches the full rectangles the main map needs — z10 for the whole of
Türkiye, z11 for the three insets — 4,204 tiles, ~70 MB. Only tiles
not already on disk are fetched, so re-running it is cheap.

EOX serves a 116-byte placeholder PNG for 39 z10 Black Sea tiles. All 39
are open sea, so each is replaced with a flat tile in the sea colour
(data/sea-fill.jpg) — invisible against the surrounding water. make_vrt.py
picks those up automatically (mainfill_z10_*.jpg).

Layer name must be s2cloudless-2023_3857 (the plain s2cloudless-2023 layer
serves a different matrix set and returns placeholder PNGs for
GoogleMapsCompatible tiles).

Usage: python3 download_tiles.py   (writes data/tiles/*.jpg)
"""
import os
import shutil
import subprocess
from concurrent.futures import ThreadPoolExecutor

BASE = os.path.dirname(os.path.abspath(__file__))
TILES = os.path.join(BASE, "data", "tiles")
URL = ("https://tiles.maps.eox.at/wmts/1.0.0/s2cloudless-2023_3857/"
       "default/GoogleMapsCompatible/{z}/{y}/{x}.jpg")

# (prefix, z, x0, x1, y0, y1)
JOBS = [
    # main map, z10, whole Türkiye (aspect-expanded extent)
    ("main", 10, 583, 642, 374, 404),
    # insets B (Ionia and Caria) and C (Antalya and the Lake District), z11
    ("detail_west", 11, 1157, 1212, 763, 802),
    # inset A (Şanlıurfa – Taş Tepeler), z11
    ("detail_east", 11, 1238, 1250, 790, 797),
]


def fetch(pair):
    path, url = pair
    try:
        subprocess.run(["curl", "-sL", "-m", "60", "-o", path, url],
                       check=False, capture_output=True, timeout=75)
    except Exception:
        pass
    return path


def ok(path):
    return os.path.exists(path) and os.path.getsize(path) > 500


def fill_placeholders():
    """Replace the 39 placeholder Black Sea tiles with flat sea-colour
    tiles (see module docstring)."""
    src = os.path.join(BASE, "data", "sea-fill.jpg")
    n = 0
    for name in sorted(os.listdir(TILES)):
        if not name.startswith("main_z10_") or not name.endswith(".jpg"):
            continue
        path = os.path.join(TILES, name)
        if os.path.getsize(path) < 500:
            x, y = name[len("main_z10_"):-4].split("_")
            shutil.copy(src, os.path.join(
                TILES, f"mainfill_z10_{x}_{y}.jpg"))
            n += 1
    print(f"placeholder sea tiles filled: {n}")


def main():
    os.makedirs(TILES, exist_ok=True)
    todo = []
    for prefix, z, x0, x1, y0, y1 in JOBS:
        for x in range(x0, x1 + 1):
            for y in range(y0, y1 + 1):
                path = os.path.join(TILES, f"{prefix}_z{z}_{x}_{y}.jpg")
                if not os.path.exists(path):
                    todo.append((path, URL.format(z=z, y=y, x=x)))
    print(f"missing tiles to fetch: {len(todo)}", flush=True)
    for attempt in (1, 2):
        remaining = [p for p in todo if not ok(p[0])]
        if not remaining:
            break
        print(f"pass {attempt}: {len(remaining)} tiles", flush=True)
        with ThreadPoolExecutor(max_workers=16) as ex:
            list(ex.map(fetch, remaining))
    bad = [(p, os.path.getsize(p) if os.path.exists(p) else -1)
           for p, _ in todo if not ok(p)]
    print(f"tiles fetched: {len(todo) - len(bad)}; suspicious (<500 B): {len(bad)}")
    for p, s in bad[:20]:
        print("  BAD", p, s)
    fill_placeholders()


if __name__ == "__main__":
    main()
