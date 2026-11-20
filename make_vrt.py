#!/usr/bin/env python3
"""Build VRT mosaics from the downloaded EOX Sentinel-2 tiles.

Tiles: data/tiles/{name}_z{z}_{x}_{y}.jpg (GoogleMapsCompatible, EPSG:3857).
Outputs:
  data/basemap_main.vrt   — z10, whole Türkiye (for the main map)
  data/basemap_detail.vrt — z11, west (insets B/C) and east (inset A)
                            rectangles in one mosaic; the gap
                            between them is nodata (transparent).
"""
import json
import math
import os

BASE = os.path.dirname(os.path.abspath(__file__))
TILES = os.path.join(BASE, "data", "tiles")
R = 20037508.342789244  # half the Earth's circumference in EPSG:3857


def tile_lonlat(z, x, y):
    n = 2.0 ** z
    lon0 = x / n * 360.0 - 180.0
    lon1 = (x + 1) / n * 360.0 - 180.0
    def lat_of(ty):
        return math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * ty / n))))
    lat1 = lat_of(y)      # north edge
    lat0 = lat_of(y + 1)  # south edge
    return lon0, lat0, lon1, lat1


def lonlat_to_3857(lon, lat):
    x = lon * R / 180.0
    y = math.log(math.tan(math.pi / 4 + math.radians(lat) / 2)) * R / math.pi
    return x, y


def build_vrt(tile_files, out_path, fixes=None):
    """tile_files: list of (z, x, y, path).
    fixes: optional dict {(z, x, y): [(path, dst_w, dst_h, off_x, off_y), ...]}
    replacing a tile with resampled sub-tiles (e.g. 4 z11 children for a
    missing z10 tile, each 128x128 px at the right offset)."""
    fixes = fixes or {}
    # union bounds in EPSG:3857
    minx = miny = 1e18
    maxx = maxy = -1e18
    infos = []
    missing = []
    for z, x, y, path in tile_files:
        if not os.path.exists(path):
            missing.append(path)
            continue
        lon0, lat0, lon1, lat1 = tile_lonlat(z, x, y)
        ax, ay = lonlat_to_3857(lon0, lat0)
        bx, by = lonlat_to_3857(lon1, lat1)
        minx, miny = min(minx, ax), min(miny, ay)
        maxx, maxy = max(maxx, bx), max(maxy, by)
        infos.append((z, x, y, path, ax, ay, bx, by))
    if not infos:
        raise SystemExit("no tiles found for " + out_path)
    # pixel size from the first tile
    z0 = infos[0][0]
    res = 2 * R / (256 * (2 ** z0))  # metres per pixel
    width = int(round((maxx - minx) / res))
    height = int(round((maxy - miny) / res))
    lines = [
        '<VRTDataset rasterXSize="%d" rasterYSize="%d">' % (width, height),
        '  <SRS dataAxisToSRSAxisMapping="1,2">PROJCS["WGS 84 / Pseudo-Mercator",'
        'GEOGCS["WGS 84",DATUM["WGS_1984",SPHEROID["WGS 84",6378137,298.257223563]],'
        'PRIMEM["Greenwich",0],UNIT["degree",0.0174532925199433]],'
        'PROJECTION["Mercator_Auxiliary_Sphere"],PARAMETER["central_meridian",0],'
        'PARAMETER["scale_factor",1],PARAMETER["false_easting",0],'
        'PARAMETER["false_northing",0],UNIT["metre",1]]</SRS>',
        '  <GeoTransform>%.6f, %.6f, 0, %.6f, 0, %.6f</GeoTransform>'
        % (minx, res, maxy, -res),
    ]
    for band in (1, 2, 3):
        lines.append('  <VRTRasterBand dataType="Byte" band="%d">' % band)
        lines.append('    <NoDataValue>0</NoDataValue>')
        lines.append('    <ColorInterp>Red</ColorInterp>' if band == 1 else
                     '    <ColorInterp>Green</ColorInterp>' if band == 2 else
                     '    <ColorInterp>Blue</ColorInterp>')
        for z, x, y, path, ax, ay, bx, by in infos:
            dx = int(round((ax - minx) / res))
            dy = int(round((maxy - by) / res))
            key = (z, x, y)
            if key in fixes:
                for fpath, dw, dh, ox, oy in fixes[key]:
                    lines.append('    <SimpleSource>')
                    lines.append('      <SourceFilename relativeToVRT="0">%s</SourceFilename>' % fpath)
                    lines.append('      <SourceBand>%d</SourceBand>' % band)
                    lines.append('      <SrcRect xOff="0" yOff="0" xSize="256" ySize="256" />')
                    lines.append('      <DstRect xOff="%d" yOff="%d" xSize="%d" ySize="%d" />'
                                 % (dx + ox, dy + oy, dw, dh))
                    lines.append('    </SimpleSource>')
            else:
                lines.append('    <SimpleSource>')
                lines.append('      <SourceFilename relativeToVRT="0">%s</SourceFilename>' % path)
                lines.append('      <SourceBand>%d</SourceBand>' % band)
                lines.append('      <SrcRect xOff="0" yOff="0" xSize="256" ySize="256" />')
                lines.append('      <DstRect xOff="%d" yOff="%d" xSize="256" ySize="256" />' % (dx, dy))
                lines.append('    </SimpleSource>')
        lines.append('  </VRTRasterBand>')
    lines.append('</VRTDataset>')
    with open(out_path, "w") as f:
        f.write("\n".join(lines))
    print(f"{out_path}: {width}x{height} px, {len(infos)} tiles, {len(missing)} missing")
    return len(missing)


def _glob_tiles(prefix, z):
    """All tiles on disk for a prefix, parsed from their filenames.

    (prefix, z, x, y, path). No external job file needed — the VRT is
    built from whatever the cache holds, so extra edge columns downloaded
    later are picked up automatically on the next run.
    """
    import glob
    import re
    out = []
    for path in glob.glob(os.path.join(TILES, f"{prefix}_z{z}_*.jpg")):
        m = re.match(r".*_z(\d+)_(\d+)_(\d+)\.jpg$", path)
        if m:
            out.append((int(m.group(1)), int(m.group(2)), int(m.group(3)), path))
    return out


def main():
    # main map: every z10 tile on disk (the aspect-expanded main extent
    # reaches lon ~25.0-46.0, i.e. x 583-642; the cache now holds those
    # edge columns — see download_missing_tiles.py).
    main_tiles = _glob_tiles("main", 10)
    # detail: west + east z11 tiles (north-strip tiles, if present, are
    # also picked up). The VRT union covers the aspect-expanded
    # extents of the insets; the gap between the west and east
    # rectangles is nodata (transparent).
    detail_tiles = (_glob_tiles("detail_west", 11)
                    + _glob_tiles("detail_east", 11)
                    + _glob_tiles("detail_north", 11))
    # EOX serves a 116-byte placeholder PNG for 39 z10 Black Sea tiles.
    # Their z11 children exist but are systematically darker (blue channel
    # ~32 vs ~64), so they show as black rectangles. All 39 are pure open
    # sea (no land), so substitute a flat tile in the mean colour of the
    # neighbouring sea tiles — invisible against the surrounding water.
    fixes = {}
    for z, x, y, path in main_tiles:
        if os.path.getsize(path) < 500:
            fpath = os.path.join(TILES, f"mainfill_z10_{x}_{y}.jpg")
            fixes[(z, x, y)] = [(fpath, 256, 256, 0, 0)]
    print("z10 placeholder tiles filled with sea-colour tiles:", len(fixes))
    m1 = build_vrt(main_tiles, os.path.join(BASE, "data", "basemap_main.vrt"),
                   fixes=fixes)
    m2 = build_vrt(detail_tiles, os.path.join(BASE, "data", "basemap_detail.vrt"))
    print("missing total:", m1 + m2)


if __name__ == "__main__":
    main()
