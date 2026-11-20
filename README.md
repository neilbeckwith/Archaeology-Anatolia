# Archaeological sites of Türkiye — map builder (PyQGIS)

A QGIS Python script that rebuilds the map **"Archaeological sites of
Türkiye, Palaeolithic to Ottoman"** — every archaeological site Wikidata
holds for Türkiye with a coordinate, ancient places from Pleiades, and
UNESCO World Heritage and Tentative List sites — as a 14-page A1 vector
PDF: the main map with three insets, a key to the 81 numbered sites, and
a full index of all 5,421 sites.

Main map only. The Istanbul–Gömeç driving-route map is a separate build
and is not in this repo.

## What it produces

`turkiye-archaeological-sites-map.pdf` (about 10 MB at the default 150 dpi):

- p1 — main map, Lambert conformal conic, Sentinel-2 cloudless 2023
  basemap, 3 insets (A Şanlıurfa – Taş Tepeler, B Ionia and Caria,
  C Antalya and the Lake District), period colours, numbered markers
  for the 81 notable sites with name labels, legend, scale bar, north
  arrow, rivers and lakes.
- p2 — key to the 81 numbered sites: number, name, period group and
  civilisation/culture tag.
- p3–p14 — full index of all 5,421 sites (10 pt, 4 columns per page):
  number, name, source record, period group, culture where known.

Text and symbols are vector, so the map stays sharp at any zoom; the
satellite basemap is raster at 150 dpi (see `MAP_DPI` below).

## Pre-built PDF

The built map is also in this repo as `turkiye-sites-map-v2.pdf` (16 pages,
19.9 MB) — the full version, including the Istanbul–Gömeç driving-route
map on pages 15–16. Note the script in this repo builds the 14-page
main-map version only; the route map is a separate build.

## Requirements

- Ubuntu 24.04, QGIS 3.34 with PyQGIS: `sudo apt install qgis python3-qgis`
- Python 3 (system python3), `curl`
- DejaVu Sans (ships with Ubuntu; the map's font)
- About 100 MB free disk for the tile cache, plus the output PDF

Tested on Ubuntu 24.04 / QGIS 3.34.4. The script sets the QGIS prefix to
`/usr` only when `/usr/share/qgis` exists; on other platforms that line
may need adjusting.

## Build

```sh
python3 download_tiles.py   # EOX Sentinel-2 tiles, 4,204 tiles, ~70 MB
python3 make_vrt.py          # mosaic the tiles into two VRTs
python3 build_map.py         # write turkiye-archaeological-sites-map.pdf
```

`MAP_DPI=300 python3 build_map.py` raises the basemap resolution (larger
file). 600 dpi needs >1 GB free RAM for the A1 basemap raster.

## Files

- `build_map.py` — the map builder (PyQGIS, headless, no GUI).
- `download_tiles.py` — fetches the Sentinel-2 cloudless 2023 tiles
  (EOX WMTS, `s2cloudless-2023_3857`) and fills the 39 placeholder
  Black Sea tiles with flat sea-colour tiles.
- `make_vrt.py` — builds `data/basemap_main.vrt` (z10) and
  `data/basemap_detail.vrt` (z11, insets) from the tile cache.
- `data/sites.geojson` / `data/sites.csv` — the rebuilt dataset: 5,421
  sites, all with coordinates, 0 unmatched. Fields: number, name, lon,
  lat, source (WHC/TL/WD/Pleiades), source_id, period_group, culture,
  notable. Culture tags are only given where a source states one (171
  sites); nothing was guessed from geography.
- `data/cities.geojson` — city labels for the main map.
- `data/north_arrow.svg` — the north arrow used on the map.
- `data/ne_rivers.zip`, `data/ne_lakes.zip` — Natural Earth 10m rivers
  and lakes; `build_map.py` unpacks them automatically if the
  shapefiles are missing.
- `data/sea-fill.jpg` — flat sea-colour tile used for the 39 placeholder
  Black Sea tiles.

## Fallbacks

- If the tile cache is missing, `build_map.py` falls back to the live
  EOX WMTS basemap, and if that fails too, to a plain background. The
  sites, labels, rivers and lakes still render.
- If the Natural Earth zips are missing, the map builds without the
  rivers and lakes.

## Data provenance

No dataset file existed for the original map, so the dataset was rebuilt
from its printed index: site names, numbers and source records were
parsed from the original PDF's index, coordinates were fetched from
Wikidata (SPARQL, P625) and the Pleiades JSON dump, UNESCO sites from
their list entries. 5,421 sites, all with coordinates, 0 unmatched.
Culture tags for the 81 notable sites were curated from each site's own
record; a further 90 come from Wikidata P2596 (culture).

Sources: UNESCO World Heritage Centre; Wikidata (CC0); Pleiades
(CC BY 3.0); Natural Earth; EOX Sentinel-2 cloudless 2023.

## Known quirks

- Natural Earth spells the reservoir "Ataturk Barajt" — that is the
  source data's spelling, not a typo in this repo.
- A few label collisions remain in the densest areas (e.g. "Antalya"
  under the Aspendos label in inset C).
