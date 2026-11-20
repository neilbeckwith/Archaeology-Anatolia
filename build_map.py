#!/usr/bin/env python3
"""Build the Türkiye archaeological sites map (PyQGIS, headless).

Reads data/sites.geojson and exports a vector PDF to
turkiye-archaeological-sites-map.pdf:
  p1  main map (A1 landscape, Lambert conformal conic, Sentinel-2 cloudless
      basemap, 3 insets, legend)
  p2  key to the 81 numbered (notable) sites, with period + culture
  p.. index of all sites, larger type than the original (10 pt, 4 columns)

Main map only — the Istanbul–Gömeç driving-route map is not built by
this script.

Re-runnable: `MAP_DPI=150 python3 build_map.py` (default 150 dpi for the
basemap raster — the map is viewed on screen, text and symbols stay
vector at any zoom; 300 dpi made the file 173 MB, 600 dpi needs >1 GB
free RAM for the A1 basemap raster).

QGIS 3.34.4, Ubuntu 24.04 (noble). Run with the system python3.
"""
import json
import math
import os
import sys
import textwrap

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
# NOTE: do NOT set XDG_CACHE_HOME here — redirecting Qt's cache breaks the
# WMS/WMTS provider (capabilities fetch fails and the layer is invalid).

BASE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(BASE, "data")
OUT = os.path.join(BASE, "turkiye-archaeological-sites-map.pdf")
DPI = int(os.environ.get("MAP_DPI", "150"))
FONT = "DejaVu Sans"


def qfont(size, bold=False):
    """QFont with a (possibly fractional) point size."""
    f = QFont(FONT)
    f.setPointSizeF(float(size))
    f.setBold(bold)
    return f

from qgis.core import (
    QgsApplication, QgsProject, QgsCoordinateReferenceSystem,
    QgsCoordinateTransform, QgsRectangle, QgsRasterLayer, QgsVectorLayer,
    QgsPrintLayout, QgsLayoutItemPage, QgsLayoutItemMap, QgsLayoutItemLabel,
    QgsLayoutItemScaleBar, QgsLayoutItemPicture, QgsLayoutItemShape,
    QgsLayoutPoint, QgsLayoutSize, QgsLayoutExporter, QgsUnitTypes,
    QgsLayoutItemMapGrid, QgsRuleBasedRenderer, QgsSimpleMarkerSymbolLayer,
    QgsSymbol, QgsProperty, QgsSymbolLayer, QgsWkbTypes,
    QgsRuleBasedLabeling, QgsPalLayerSettings, QgsSimpleLineCallout,
    QgsFillSymbol, QgsLineSymbol, QgsSimpleLineSymbolLayer,
    QgsSingleSymbolRenderer, QgsRenderContext,
    QgsVectorLayerSimpleLabeling, QgsTextFormat, QgsTextBufferSettings,
    QgsPalLayerSettings as Pal, QgsPointXY,
)
from qgis.PyQt.QtGui import QFont, QColor
from qgis.PyQt.QtCore import Qt

# ---------------------------------------------------------------- config
PERIODS = [
    ("PALAEOLITHIC / EPIPALAEOLITHIC", "Palaeolithic / Epipalaeolithic", "#8B5A2B"),
    ("NEOLITHIC", "Neolithic", "#2E8B57"),
    ("CHALCOLITHIC", "Chalcolithic", "#20B2AA"),
    ("BRONZE AGE", "Bronze Age", "#DAA520"),
    ("IRON AGE", "Iron Age", "#B22222"),
    ("ARCHAIC / CLASSICAL / HELLENISTIC / ROMAN", "Archaic / Classical / Hellenistic / Roman", "#6A5ACD"),
    ("BYZANTINE / LATE ANTIQUE / MEDIEVAL", "Byzantine / Late Antique / medieval", "#4682B4"),
    ("SELJUK / OTTOMAN", "Seljuk / Ottoman", "#228B22"),
    ("NO PERIOD PRINTED BY THE SOURCES", "No period printed by the sources", "#A9A9A9"),
]
PERIOD_COLOR = {p[0]: p[2] for p in PERIODS}
PERIOD_SHORT = {p[0]: p[1] for p in PERIODS}
PERIOD_ORDER = {p[0]: i + 1 for i, p in enumerate(PERIODS)}

COLOR_EXPR = ("CASE " + " ".join(
    f"WHEN \"period_group\" = '{g}' THEN '{c}'" for g, _, c in PERIODS) +
    " ELSE '#A9A9A9' END")

# marker shapes by source / listing (as in the original)
SHAPES = [
    ("WHC", "star", 3.6, "UNESCO World Heritage property"),
    ("TL", "triangle", 3.4, "UNESCO Tentative List site"),
    ("WD-notable", "square", 3.4, "Notable excavated site (Wikidata)"),
    ("WD", "circle", 1.7, "Other archaeological site (Wikidata)"),
    ("Pleiades", "diamond", 1.9, "Ancient place (Pleiades)"),
]
SHAPE_FILTER = {
    "WHC": "\"source\" = 'WHC'",
    "TL": "\"source\" = 'TL'",
    "WD-notable": "\"source\" = 'WD' AND \"notable\" = true",
    "WD": "\"source\" = 'WD' AND \"notable\" = false",
    "Pleiades": "\"source\" = 'Pleiades'",
}

LCC_PROJ = "+proj=lcc +lat_1=36 +lat_2=42 +lat_0=39 +lon_0=35 +datum=WGS84 +units=m +no_defs"
WMTS_URI = ("contextualWMSLegend=0&crs=EPSG:3857&dpiMode=7&featureCount=10"
            "&format=image/jpeg&layers=s2cloudless-2023&styles="
            "&tileMatrixSet=GoogleMapsCompatible"
            "&url=https://tiles.maps.eox.at/wmts/1.0.0/WMTSCapabilities.xml")

INSETS = [
    ("A", "Şanlıurfa – Taş Tepeler", (37.9, 36.9, 39.7, 37.9)),
    ("B", "Ionia and Caria", (26.2, 36.6, 29.0, 38.7)),
    ("C", "Antalya and the Lake District", (29.3, 36.3, 32.3, 38.4)),
]


def log(*a):
    print(*a, flush=True)


# ---------------------------------------------------------------- data
def load_data():
    sites = json.load(open(os.path.join(DATA, "sites.geojson")))["features"]
    return sites


def culture_counts(sites):
    """Count sites per culture tag (split multi-tags)."""
    from collections import Counter
    c = Counter()
    for f in sites:
        cult = f["properties"].get("culture") or ""
        for part in cult.split(";"):
            part = part.strip()
            if part:
                c[part] += 1
    return c


# ------------------------------------------------------- text pagination
def wrap_entry(text, width=96, indent="    "):
    return textwrap.wrap(text, width=width, subsequent_indent=indent,
                         break_long_words=False, break_on_hyphens=False) or [""]


def index_lines(sites):
    """One (possibly wrapped) text block per site for the index pages."""
    lines = []
    for f in sorted(sites, key=lambda s: s["properties"]["number"]):
        p = f["properties"]
        src = {"WD": f"WD {p['source_id']}", "Pleiades": f"Pleiades {p['source_id']}",
               "WHC": f"WHC {p['source_id']}", "TL": f"TL {p['source_id']}"}[p["source"]]
        txt = f"{p['number']}  {p['name']} — {src} · {PERIOD_SHORT[p['period_group']]}"
        if p.get("culture"):
            txt += f" · {p['culture']}"
        lines.append(wrap_entry(txt))
    return lines


def paginate_index(lines, cols=4, lines_per_col=118):
    """Chunk wrapped index lines (each block = one site's lines, never split)
    into pages of exactly `cols` columns, each column at most `lines_per_col`
    lines."""
    pages = []
    cur_page = []  # list of columns, each a list of blocks
    col, col_lines = [], 0
    for block in lines:
        if col and col_lines + len(block) > lines_per_col:
            cur_page.append(col)
            col, col_lines = [], 0
            if len(cur_page) == cols:
                pages.append(cur_page)
                cur_page = []
        col.append(block)
        col_lines += len(block)
    if col:
        cur_page.append(col)
    if cur_page:
        pages.append(cur_page)
    return pages


# ---------------------------------------------------------------- QGIS setup
def init_qgis():
    # QGIS install prefix: /usr on Ubuntu/Debian. Other platforms put it
    # elsewhere; only set it when the Ubuntu layout is present.
    if os.path.isdir("/usr/share/qgis"):
        QgsApplication.setPrefixPath("/usr", True)
    app = QgsApplication([], False)
    app.initQgis()
    return app


def make_lcc():
    crs = QgsCoordinateReferenceSystem()
    assert crs.createFromProj(LCC_PROJ), "LCC CRS failed"
    return crs


def wgs84_to_lcc_rect(lonmin, latmin, lonmax, latmax, lcc, project):
    xform = QgsCoordinateTransform(
        QgsCoordinateReferenceSystem("EPSG:4326"), lcc, project)
    return xform.transformBoundingBox(QgsRectangle(lonmin, latmin, lonmax, latmax))


def aspect_rect(rect, aspect):
    """Expand a QgsRectangle (projected metres) to the given w/h aspect."""
    w, h = rect.width(), rect.height()
    cx, cy = rect.center().x(), rect.center().y()
    if w / h > aspect:  # too wide -> grow height
        nh = w / aspect
        return QgsRectangle(cx - w / 2, cy - nh / 2, cx + w / 2, cy + nh / 2)
    nw = h * aspect
    return QgsRectangle(cx - nw / 2, cy - h / 2, cx + nw / 2, cy + h / 2)


# ---------------------------------------------------------------- layers
def basemap_layer():
    layer = QgsRasterLayer(WMTS_URI, "Sentinel-2 cloudless 2023 (EOX)", "wms")
    if not layer.isValid():
        log("WARNING: EOX WMTS basemap invalid — falling back to plain background")
        return None
    log("basemap WMTS valid")
    return layer


def raster_layer(path, name):
    """Local raster (VRT/GeoTIFF) or None if missing/invalid."""
    if not os.path.exists(path):
        return None
    layer = QgsRasterLayer(path, name, "gdal")
    if not layer.isValid():
        log(f"WARNING: local raster {path} invalid")
        return None
    return layer


def style_sites(layer):
    """Rule-based renderer: shape by source/listing, colour by period group."""
    root = QgsRuleBasedRenderer.Rule(None)
    for key, shape, size, label in SHAPES:
        sym = QgsSymbol.defaultSymbol(QgsWkbTypes.PointGeometry)
        sym.deleteSymbolLayer(0)
        ml = QgsSimpleMarkerSymbolLayer.create({
            "name": shape, "size": str(size), "color": "#ffffff",
            "outline_color": "#000000", "outline_width": "0.25",
            "offset": "0,0", "angle": "0"})
        ml.setDataDefinedProperty(
            QgsSymbolLayer.PropertyFillColor,
            QgsProperty.fromExpression(COLOR_EXPR))
        sym.appendSymbolLayer(ml)
        rule = QgsRuleBasedRenderer.Rule(
            sym, filterExp=SHAPE_FILTER[key], label=label)
        root.appendChild(rule)
    layer.setRenderer(QgsRuleBasedRenderer(root))


def label_settings(field, size, bold=False, color=QColor(0, 0, 0),
                   halo=0.8, halo_color=QColor(255, 255, 255),
                   dist=1.2, callout=False):
    s = QgsPalLayerSettings()
    s.fieldName = field
    s.isExpression = False
    s.placement = QgsPalLayerSettings.AroundPoint
    s.dist = dist
    s.displayAll = True
    s.priority = 10
    fmt = QgsTextFormat()
    fmt.setFont(qfont(size, True if bold else False))
    fmt.setColor(color)
    buf = QgsTextBufferSettings()
    buf.setEnabled(True)
    buf.setSize(halo)
    buf.setColor(halo_color)
    fmt.setBuffer(buf)
    s.setFormat(fmt)
    if callout:
        co = QgsSimpleLineCallout()
        co.setEnabled(True)
        co.setLineSymbol(QgsLineSymbol.createSimple(
            {"color": "60,60,60,200", "width": "0.25"}))
        s.setCallout(co)
    return s


def label_notable(layer):
    """Two label rules: number next to the marker, name with callout."""
    root = QgsRuleBasedLabeling.Rule(QgsPalLayerSettings())
    num = QgsRuleBasedLabeling.Rule(
        label_settings("number", 7.5, bold=True, dist=1.0),
        filterExp='"notable" = true', description="number")
    name = QgsRuleBasedLabeling.Rule(
        label_settings("name", 9.5, dist=3.0, callout=True),
        filterExp='"notable" = true', description="name")
    root.appendChild(num)
    root.appendChild(name)
    layer.setLabeling(QgsRuleBasedLabeling(root))
    layer.setLabelsEnabled(True)


def label_cities(layer):
    s = QgsPalLayerSettings()
    s.fieldName = "name"
    s.placement = QgsPalLayerSettings.AroundPoint
    s.dist = 1.0
    s.displayAll = True
    fmt = QgsTextFormat()
    fmt.setFont(qfont(9, False))
    fmt.setColor(QColor(40, 40, 40))
    buf = QgsTextBufferSettings()
    buf.setEnabled(True)
    buf.setSize(1.0)
    buf.setColor(QColor(255, 255, 255))
    fmt.setBuffer(buf)
    s.setFormat(fmt)
    layer.setLabeling(QgsVectorLayerSimpleLabeling(s))
    layer.setLabelsEnabled(True)
    # no marker: transparent tiny square
    sym = QgsSymbol.defaultSymbol(QgsWkbTypes.PointGeometry)
    sym.deleteSymbolLayer(0)
    ml = QgsSimpleMarkerSymbolLayer.create({
        "name": "square", "size": "1.2", "color": "#666666",
        "outline_color": "#666666", "outline_width": "0"})
    sym.appendSymbolLayer(ml)
    layer.setRenderer(QgsSingleSymbolRenderer(sym))


# ---------------------------------------------------------------- hydrography
# Natural Earth 10m physical vectors (data/hydro_rivers, data/hydro_lakes).
# Blue hues, lakes at ~35% fill opacity, thin blue river lines — drawn
# between the basemap and the site symbols in every map item.
HYDRO_RIVERS = os.path.join(DATA, "hydro_rivers",
                            "ne_10m_rivers_lake_centerlines.shp")
HYDRO_LAKES = os.path.join(DATA, "hydro_lakes", "ne_10m_lakes.shp")

def ensure_hydro():
    """Unpack the Natural Earth 10m shapefiles from the shipped zips if
    the extracted files are missing. The extracted shapefiles are not in
    the repo (19 MB) — only the small zips are (4.4 MB). Pure stdlib."""
    import zipfile
    for shp, zipp, subdir in (
            (HYDRO_RIVERS, "ne_rivers.zip", "hydro_rivers"),
            (HYDRO_LAKES, "ne_lakes.zip", "hydro_lakes")):
        if os.path.exists(shp):
            continue
        zpath = os.path.join(DATA, zipp)
        if not os.path.exists(zpath):
            log(f"WARNING: {zipp} missing — building without "
                f"{os.path.basename(shp)}")
            continue
        target = os.path.join(DATA, subdir)
        os.makedirs(target, exist_ok=True)
        with zipfile.ZipFile(zpath) as zf:
            zf.extractall(target)
        log(f"unpacked {zipp} -> {target}")

RIVER_BLUE = "#1f6fbf"
LAKE_FILL = "127,179,213,90"   # light blue, ~35% opacity
LAKE_OUTLINE = "#1f6fbf"
# major water features to label (matched case-insensitively against the
# names actually present in the shapefiles — only those found are labelled)
WANTED_RIVERS = ["kızılırmak", "kizilirmak", "yeşilırmak", "sakarya",
                 "euphrates", "fırat", "firat", "furat", "murat",
                 "tigris", "dicle",
                 "menderes", "gediz", "seyhan", "ceyhan", "susurluk",
                 "göksu", "manavgat", "aksu", "çoruh", "aras"]
# NB: Natural Earth spells the Euphrates "Firat" (ASCII) and "Al Furat",
# and the Atatürk reservoir "Ataturk Barajt" — the ASCII forms are wanted
# too. "beyşehir" is deliberately NOT wanted: the lake label collides with
# the Eflatun Pinar site name ("The … Spring Sanctuary") at the lake's edge.
WANTED_LAKES = ["van", "tuz", "eğirdir", "iznik", "ulubat",
                "manyas", "bafa", "hazar", "çıldır", "burdur", "akşehir",
                "eber", "sapanca", "sultan sazlığı", "ataturk", "urmia"]


def _names_present(layer, field="name"):
    idx = layer.fields().indexOf(field)
    if idx < 0:
        return []
    return [str(v) for v in layer.uniqueValues(idx) if v not in (None, "")]


def _label_filter(names, wanted):
    """SQL filter matching the wanted features actually present.

    A wanted token must equal a whole word of the feature's name (after
    lower-casing and accent folding) — plain substring matching would
    wrongly catch e.g. "Malagarasi" for "Aras" or "Sevan" for "Van".
    """
    import unicodedata
    import re

    def norm(s):
        s = unicodedata.normalize("NFKD", s.lower())
        return "".join(c for c in s if not unicodedata.combining(c))

    wanted_words = {norm(w) for w in wanted}
    hits = []
    for n in names:
        # \w is Unicode-aware: Turkish letters (ı, ş, ğ, ü, ö, ç) stay
        # inside words. (An earlier [^a-z0-9] split broke "Kızılırmak"
        # into fragments, so it never matched.)
        if "blommestein" in norm(n):
            continue  # "Van Blommestein Meer" (Suriname) is not Lake Van
        words = set(re.findall(r"\w+", norm(n)))
        if words & wanted_words:
            hits.append(n.replace("'", "''"))
    if not hits:
        return None
    return "\"name\" IN ('" + "', '".join(hits) + "')"


def _apply_label_filter(layer, settings, filt):
    """Label only the features matching `filt` (an SQL expression).

    QgsVectorLayerSimpleLabeling has no filter — the expression must go on
    a rule. (The first revision computed the filter but attached simple
    labelling, so EVERY river and lake name was labelled.)"""
    root = QgsRuleBasedLabeling.Rule(QgsPalLayerSettings())
    root.appendChild(QgsRuleBasedLabeling.Rule(
        settings, filterExp=filt, description="major features"))
    layer.setLabeling(QgsRuleBasedLabeling(root))
    layer.setLabelsEnabled(True)


def style_hydro(rivers, lakes):
    """Rivers: thin blue lines. Lakes: light-blue fill, thin blue outline.
    Label only the major named features, italic blue with a white halo."""
    if rivers is not None:
        rsym = QgsLineSymbol.createSimple(
            {"color": RIVER_BLUE, "width": "0.5"})
        rivers.setRenderer(QgsSingleSymbolRenderer(rsym))
        filt = _label_filter(_names_present(rivers), WANTED_RIVERS)
        if filt:
            s = QgsPalLayerSettings()
            s.fieldName = "name"
            s.placement = QgsPalLayerSettings.Line
            s.displayAll = True
            fmt = QgsTextFormat()
            fnt = qfont(7.5)
            fnt.setItalic(True)
            fmt.setFont(fnt)
            fmt.setColor(QColor(RIVER_BLUE))
            buf = QgsTextBufferSettings()
            buf.setEnabled(True)
            buf.setSize(1.0)
            buf.setColor(QColor(255, 255, 255))
            fmt.setBuffer(buf)
            s.setFormat(fmt)
            _apply_label_filter(rivers, s, filt)
            log(f"  river labels: {filt}")
        else:
            log("  WARNING: no wanted river names found in shapefile")
    if lakes is not None:
        lsym = QgsFillSymbol.createSimple(
            {"color": LAKE_FILL, "outline_color": LAKE_OUTLINE,
             "outline_width": "0.4"})
        lakes.setRenderer(QgsSingleSymbolRenderer(lsym))
        filt = _label_filter(_names_present(lakes), WANTED_LAKES)
        if filt:
            s = QgsPalLayerSettings()
            s.fieldName = "name"
            s.placement = QgsPalLayerSettings.AroundPoint
            s.dist = 1.0
            s.displayAll = True
            fmt = QgsTextFormat()
            fnt = qfont(8)
            fnt.setItalic(True)
            fmt.setFont(fnt)
            fmt.setColor(QColor(RIVER_BLUE))
            buf = QgsTextBufferSettings()
            buf.setEnabled(True)
            buf.setSize(1.0)
            buf.setColor(QColor(255, 255, 255))
            fmt.setBuffer(buf)
            s.setFormat(fmt)
            _apply_label_filter(lakes, s, filt)
            log(f"  lake labels: {filt}")
        else:
            log("  WARNING: no wanted lake names found in shapefile")


def memory_layer(name, features, fields):
    """Build a memory point layer from a list of dicts."""
    from qgis.core import QgsVectorLayer as VL, QgsField, QgsFeature, QgsGeometry, QgsPointXY
    from qgis.PyQt.QtCore import QVariant
    type_map = {"int": QVariant.Int, "str": QVariant.String, "float": QVariant.Double}
    vl = VL("Point?crs=EPSG:4326", name, "memory")
    pr = vl.dataProvider()
    pr.addAttributes([QgsField(n, type_map[t]) for n, t in fields])
    vl.updateFields()
    feats = []
    for f in features:
        feat = QgsFeature(vl.fields())
        for n, _ in fields:
            feat[n] = f[n]
        feat.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(f["lon"], f["lat"])))
        feats.append(feat)
    pr.addFeatures(feats)
    vl.updateExtents()
    return vl


# ---------------------------------------------------------------- layout helpers
# Page offset: items are placed with page-relative y; the helpers add the
# current page's absolute top. QGIS stacks pages vertically with a gap
# between them, so without this every page's items pile onto page 1.
PAGE_Y0 = [0.0]


def new_page(layout, width=841, height=594):
    page = QgsLayoutItemPage(layout)
    page.setPageSize(QgsLayoutSize(width, height, QgsUnitTypes.LayoutMillimeters))
    pc = layout.pageCollection()
    pc.addPage(page)
    PAGE_Y0[0] = pc.pagePositionToAbsolute(
        pc.pageCount() - 1,
        QgsLayoutPoint(0, 0, QgsUnitTypes.LayoutMillimeters)).y()
    log(f"  new page {pc.pageCount()}: y0 = {PAGE_Y0[0]:.1f} mm")
    return page


def _page_y(y, absolute):
    return y if absolute else y + PAGE_Y0[0]


def add_label(layout, x, y, w, h, text, size=10, bold=False, color=QColor(0, 0, 0),
              align=Qt.AlignLeft | Qt.AlignTop, absolute=False):
    y = _page_y(y, absolute)
    lab = QgsLayoutItemLabel(layout)
    lab.setText(text)
    lab.setFont(qfont(size, True if bold else False))
    lab.setFontColor(color)
    lab.setHAlign(Qt.AlignLeft)
    lab.setVAlign(Qt.AlignTop)
    lab.attemptMove(QgsLayoutPoint(x, y, QgsUnitTypes.LayoutMillimeters))
    lab.attemptResize(QgsLayoutSize(w, h, QgsUnitTypes.LayoutMillimeters))
    layout.addLayoutItem(lab)
    return lab


def add_rect(layout, x, y, w, h, fill="#ffffff", outline="#000000", outline_w=0.3,
             absolute=False):
    y = _page_y(y, absolute)
    sh = QgsLayoutItemShape(layout)
    sh.setShapeType(QgsLayoutItemShape.Shape.Rectangle)
    sh.setSymbol(QgsFillSymbol.createSimple({
        "color": fill, "outline_color": outline, "outline_width": str(outline_w)}))
    sh.attemptMove(QgsLayoutPoint(x, y, QgsUnitTypes.LayoutMillimeters))
    sh.attemptResize(QgsLayoutSize(w, h, QgsUnitTypes.LayoutMillimeters))
    layout.addLayoutItem(sh)
    return sh


def add_map(layout, x, y, w, h, extent, layers, crs):
    y = _page_y(y, False)
    m = QgsLayoutItemMap(layout)
    m.attemptMove(QgsLayoutPoint(x, y, QgsUnitTypes.LayoutMillimeters))
    m.attemptResize(QgsLayoutSize(w, h, QgsUnitTypes.LayoutMillimeters))
    m.setExtent(extent)
    m.setCrs(crs)
    m.setLayers(layers)
    m.setBackgroundColor(QColor(235, 238, 240))
    layout.addLayoutItem(m)
    return m


def add_grid(m, interval=5.0, font_size=8):
    G = QgsLayoutItemMapGrid
    g = m.grid()
    g.setEnabled(True)
    g.setCrs(QgsCoordinateReferenceSystem("EPSG:4326"))
    g.setIntervalX(interval)
    g.setIntervalY(interval)
    g.setStyle(G.GridStyle.Solid)
    g.setGridLineColor(QColor(255, 255, 255, 110))
    g.setGridLineWidth(0.3)
    g.setAnnotationEnabled(True)
    g.setAnnotationFormat(G.AnnotationFormat.DecimalWithSuffix)
    g.setAnnotationFont(qfont(font_size))
    g.setAnnotationFontColor(QColor(40, 40, 40))
    for side in (G.BorderSide.Bottom, G.BorderSide.Left):
        g.setAnnotationDisplay(G.DisplayMode.ShowAll, side)
        g.setAnnotationPosition(G.AnnotationPosition.OutsideMapFrame, side)
    for side in (G.BorderSide.Top, G.BorderSide.Right):
        g.setAnnotationDisplay(G.DisplayMode.HideAll, side)


def scalebar_width(m, units_per_seg, segments):
    """Natural width (mm on paper) of a scale bar for this map: the item
    box does NOT constrain the bar — it draws at its true size, so a box
    narrower than that lets the bar overflow its panel (the main bar's
    400 km end stuck out past the panel in the first revision)."""
    return segments * units_per_seg * 1e6 / m.scale()


def add_scalebar(layout, m, x, y, units_per_seg=100, segments=4, right=None):
    """Scale bar with a white background panel behind it, so it stays
    readable over dark satellite imagery. The panel is added first (so it
    renders underneath) and is sized to the bar's true width plus its
    labels, with a small margin. `right` aligns the panel's right edge to
    a page x (used for the insets whose bottom-left corner holds sites).
    Callers must keep the bar fully inside its map frame with a clear
    margin from the frame edge."""
    bar_w = scalebar_width(m, units_per_seg, segments)
    panel_w = bar_w + 8
    if right is not None:
        x = right - panel_w - 6
    y = _page_y(y, False)
    add_rect(layout, x - 4, y - 3, panel_w + 8, 18, fill="255,255,255,230",
             outline="#999999", outline_w=0.25, absolute=True)
    sb = QgsLayoutItemScaleBar(layout)
    sb.setLinkedMap(m)
    sb.setStyle("Single Box")
    sb.setUnits(QgsUnitTypes.DistanceKilometers)
    sb.setUnitsPerSegment(units_per_seg)
    sb.setNumberOfSegments(segments)
    sb.setNumberOfSegmentsLeft(0)
    sb.setUnitLabel("km")
    sb.setFont(qfont(9))
    sb.setHeight(4)
    sb.attemptMove(QgsLayoutPoint(x, y, QgsUnitTypes.LayoutMillimeters))
    sb.attemptResize(QgsLayoutSize(bar_w + 10, 12, QgsUnitTypes.LayoutMillimeters))
    layout.addLayoutItem(sb)
    return sb


def add_north(layout, x, y, w=18, h=32):
    """North arrow that is instantly recognisable: a two-tone arrowhead
    with a bold letter N above it, on a white panel so it reads against
    the satellite imagery. (The stock QGIS NorthArrow_01.svg is a thin
    circle with a short tick and no N or arrowhead — that is why the old
    one was not recognised as a north arrow.)"""
    y = _page_y(y, False)
    # white panel behind the whole item
    add_rect(layout, x - 5, y - 3, w + 10, h + 17, fill="255,255,255,230",
             outline="#999999", outline_w=0.25, absolute=True)
    # bold N, centred, above the arrow
    nlab = QgsLayoutItemLabel(layout)
    nlab.setText("N")
    nlab.setFont(qfont(16, True))
    nlab.setFontColor(QColor(0, 0, 0))
    nlab.setHAlign(Qt.AlignHCenter)
    nlab.setVAlign(Qt.AlignTop)
    nlab.attemptMove(QgsLayoutPoint(x, y, QgsUnitTypes.LayoutMillimeters))
    nlab.attemptResize(QgsLayoutSize(w, 12, QgsUnitTypes.LayoutMillimeters))
    layout.addLayoutItem(nlab)
    # arrow (custom SVG in the project data dir)
    pic = QgsLayoutItemPicture(layout)
    pic.setPicturePath(os.path.join(DATA, "north_arrow.svg"))
    pic.attemptMove(QgsLayoutPoint(x, y + 12, QgsUnitTypes.LayoutMillimeters))
    pic.attemptResize(QgsLayoutSize(w, h, QgsUnitTypes.LayoutMillimeters))
    layout.addLayoutItem(pic)
    return pic


def map_to_layout(m, lon, lat, project, lcc):
    """Layout (mm) position of a WGS84 point inside map item m."""
    xform = QgsCoordinateTransform(
        QgsCoordinateReferenceSystem("EPSG:4326"), lcc, project)
    p = xform.transform(lon, lat)
    e = m.extent()
    r = m.rect()
    fx = (p.x() - e.xMinimum()) / e.width()
    fy = 1.0 - (p.y() - e.yMinimum()) / e.height()
    pos = m.positionWithUnits()
    return (pos.x() + fx * r.width(), pos.y() + fy * r.height())


# ---------------------------------------------------------------- page 1: main map
def page_main(layout, project, lcc, layers_main, layers_detail, sites):
    layers = layers_main
    new_page(layout)
    add_label(layout, 10, 8, 821, 14,
              "Archaeological sites of Türkiye, Palaeolithic to Ottoman",
              size=22, bold=True)
    add_label(layout, 10, 23, 821, 7,
              "Every archaeological site Wikidata holds for Türkiye with a coordinate, ancient places from "
              "Pleiades, and UNESCO World Heritage and Tentative List sites. Colour = period group printed "
              "by the sources; numbered sites are listed in the key (page 2), all sites in the index. "
              "Updated 2026-11-20.",
              size=9, color=QColor(60, 60, 60))

    extent = aspect_rect(
        wgs84_to_lcc_rect(25.5, 35.2, 45.5, 43.4, lcc, project), 825 / 415)
    main = add_map(layout, 8, 32, 825, 415, extent, layers, lcc)
    add_grid(main, interval=5.0, font_size=8)
    add_scalebar(layout, main, 18, 428, units_per_seg=100, segments=4)
    add_north(layout, 806, 40)

    # inset frames on the main map + A/B/C tags (transparent fill, dark outline)
    for letter, title, (lo0, la0, lo1, la1) in INSETS:
        x0, y0 = map_to_layout(main, lo0, la1, project, lcc)
        x1, y1 = map_to_layout(main, lo1, la0, project, lcc)
        add_rect(layout, x0, y0, x1 - x0, y1 - y0,
                 fill="255,255,255,0", outline="#333333", outline_w=0.5,
                 absolute=True)
        add_label(layout, x0 + 1.5, y0 + 1.0, 10, 8, letter, size=11, bold=True,
                  absolute=True)

    # bottom strip: insets + legend
    y_in, h_in = 463, 115
    for i, (letter, title, (lo0, la0, lo1, la1)) in enumerate(INSETS):
        x = 8 + i * 204
        add_label(layout, x, 455, 198, 7, f"{letter}  {title}", size=11, bold=True)
        ext = aspect_rect(
            wgs84_to_lcc_rect(lo0, la0, lo1, la1, lcc, project), 198 / h_in)
        im = add_map(layout, x, y_in, 198, h_in, ext, layers_detail, lcc)
        add_grid(im, interval=1.0, font_size=7)
        # scale bar well inside the frame (not at the very bottom edge),
        # with a white panel behind it (see add_scalebar). A goes
        # top-right: both bottom corners hold notable site labels there
        # (Zeugma bottom-left, Harbetsuvan Tepe bottom-right) that the
        # panel covered. C goes bottom-right: its bottom-left holds
        # Belbaşı / Xanthos-Letoon, and the bottom-right is open sea.
        # B stays bottom-left — its bottom-right holds Kaunos, and the
        # bottom-left is open sea.
        if letter == "A":
            add_scalebar(layout, im, x + 6, y_in + 6,
                         units_per_seg=25, segments=2, right=x + 198)
        elif letter == "C":
            add_scalebar(layout, im, x + 6, y_in + h_in - 22,
                         units_per_seg=25, segments=2, right=x + 198)
        else:
            add_scalebar(layout, im, x + 6, y_in + h_in - 22,
                         units_per_seg=25, segments=2)

    # legend panel (numbered-site note dropped: the subtitle already says
    # the numbered sites are listed in the key on page 2)
    lx, ly, lw = 620, 452, 213
    add_label(layout, lx, ly, lw, 8, "Legend", size=12, bold=True)
    y = ly + 11
    add_label(layout, lx, y, lw, 6, "Period group (earliest printed; colour)",
              size=9, bold=True)
    y += 7
    for _g, label, color in PERIODS:
        add_rect(layout, lx, y + 0.5, 6, 4, fill=color, outline="#000000", outline_w=0.25)
        add_label(layout, lx + 9, y, lw - 9, 6, label, size=9)
        y += 6.0
    y += 3
    add_label(layout, lx, y, lw, 6, "Source / listing (marker)", size=9, bold=True)
    y += 7
    for key, shape, size, label in SHAPES:
        add_label(layout, lx, y, lw, 6,
                  f"  {'★' if shape == 'star' else '▲' if shape == 'triangle' else '■' if shape == 'square' else '◆' if shape == 'diamond' else '●'}  {label}",
                  size=9)
        y += 6.0
    y += 3
    add_label(layout, lx, y, lw, 6, "Hydrography (Natural Earth)", size=9, bold=True)
    y += 7
    add_rect(layout, lx, y + 2.5, 12, 1.2, fill=RIVER_BLUE,
             outline=RIVER_BLUE, outline_w=0)
    add_label(layout, lx + 15, y, lw - 15, 6, "Rivers", size=9)
    y += 6.0
    add_rect(layout, lx, y + 0.5, 6, 4, fill="#7fb3d5",
             outline=RIVER_BLUE, outline_w=0.4)
    add_label(layout, lx + 9, y, lw - 9, 6, "Lakes", size=9)

    add_label(layout, 10, 588, 821, 5,
              "Sources: UNESCO World Heritage Centre; Wikidata (CC0); Pleiades (CC BY 3.0); English Wikipedia "
              "(CC BY-SA 4.0, period words only). Basemap: Sentinel-2 cloudless by EOX IT Services GmbH "
              "(CC BY-NC-SA 4.0), contains modified Copernicus Sentinel data. Hydrography: Natural Earth. "
              f"Made with QGIS. Basemap rendered at {DPI} dpi; text and symbols are vector and stay sharp at any zoom.",
              size=7, color=QColor(80, 80, 80))
    return main


# ---------------------------------------------------------------- key + index pages
def page_key(layout, sites):
    new_page(layout)
    add_label(layout, 10, 8, 821, 12,
              "Key to the numbered (notable) sites, by period group",
              size=18, bold=True)
    add_label(layout, 10, 22, 821, 7,
              "Each numbered site is marked on the main map (page 1) and the insets with its number and name. "
              "Culture is the associated civilisation or culture where the sources state one.",
              size=10, color=QColor(60, 60, 60))
    notable = sorted([f for f in sites if f["properties"]["notable"]],
                     key=lambda s: s["properties"]["number"])
    # 3 columns, grouped by period group
    col_w, col_gap = 265, 8
    xs = [10, 10 + col_w + col_gap, 10 + 2 * (col_w + col_gap)]
    y_top, line_h = 36, 8.0
    # distribute groups across columns by line count
    groups = []
    for g, _label, _c in PERIODS:
        members = [f for f in notable if f["properties"]["period_group"] == g]
        if members:
            groups.append((g, members))
    col = 0
    y = y_top
    for g, members in groups:
        need = 8 + len(members) * line_h
        if y + need > 560 and col < 2:
            col += 1
            y = y_top
        x = xs[col]
        add_label(layout, x, y, col_w, 7, PERIOD_SHORT[g].upper(), size=13, bold=True)
        y += 8
        for f in members:
            p = f["properties"]
            txt = f"{p['number']:>3}  {p['name']}"
            if p.get("culture"):
                txt += f"  —  {p['culture']}"
            add_label(layout, x, y, col_w, 7, txt, size=13)
            y += line_h
        y += 4
    # culture summary
    y = 545
    from collections import Counter
    cc = Counter()
    for f in notable:
        for part in (f["properties"].get("culture") or "").split(","):
            part = part.strip()
            if part:
                cc[part] += 1
    summary = "Cultures in the key: " + "; ".join(
        f"{k} ({v})" for k, v in cc.most_common())
    add_label(layout, 10, y, 821, 14, summary, size=10, color=QColor(60, 60, 60))


def page_index(layout, sites, page_no, total_pages, columns):
    new_page(layout)
    add_label(layout, 10, 8, 821, 10,
              f"Archaeological sites of Türkiye — site index (page {page_no} of {total_pages})",
              size=14, bold=True)
    add_label(layout, 10, 20, 821, 6,
              "Number, name as printed by the source, source record (WHC/TL = UNESCO World Heritage / "
              "Tentative List id, WD = Wikidata item, Pleiades = Pleiades place id), period group, culture "
              "where known.",
              size=9, color=QColor(60, 60, 60))
    col_w, col_gap = 199, 8
    xs = [10 + i * (col_w + col_gap) for i in range(4)]
    for ci, col_blocks in enumerate(columns):
        lines = []
        for block in col_blocks:
            lines.extend(block)
        add_label(layout, xs[ci], 30, col_w, 550, "\n".join(lines), size=10)
    add_label(layout, 10, 586, 821, 5,
              "Made with QGIS. Sources: UNESCO World Heritage Centre; Wikidata (CC0); Pleiades (CC BY 3.0).",
              size=7, color=QColor(80, 80, 80))


# ---------------------------------------------------------------- main
def main():
    log("loading data…")
    sites = load_data()
    log(f"sites: {len(sites)}")
    cc = culture_counts(sites)
    log("top cultures:", cc.most_common(12))

    app = init_qgis()
    project = QgsProject.instance()
    lcc = make_lcc()
    project.setCrs(lcc)

    # layers: local VRT basemaps (pre-downloaded EOX tiles) if present,
    # otherwise the live EOX WMTS, otherwise no basemap at all.
    bm_main = raster_layer(os.path.join(DATA, "basemap_main.vrt"),
                           "Sentinel-2 cloudless 2023 (z10)")
    bm_detail = raster_layer(os.path.join(DATA, "basemap_detail.vrt"),
                             "Sentinel-2 cloudless 2023 (z11)")
    if bm_main is not None and bm_detail is not None:
        log("basemap: local VRTs (z10 main, z11 detail)")
        project.addMapLayer(bm_main)
        project.addMapLayer(bm_detail)
    else:
        log("basemap: local VRTs missing — trying live WMTS")
        bm_main = bm_detail = basemap_layer()
        if bm_main is not None:
            project.addMapLayer(bm_main)
    # hydrography (Natural Earth 10m) — between the basemap and the sites
    # in every map item: basemap -> lakes/rivers -> sites -> cities/labels.
    ensure_hydro()
    rivers_layer = None
    lakes_layer = None
    if os.path.exists(HYDRO_RIVERS):
        rivers_layer = QgsVectorLayer(HYDRO_RIVERS, "rivers", "ogr")
        if rivers_layer.isValid():
            project.addMapLayer(rivers_layer)
        else:
            log("WARNING: rivers shapefile invalid")
            rivers_layer = None
    if os.path.exists(HYDRO_LAKES):
        lakes_layer = QgsVectorLayer(HYDRO_LAKES, "lakes", "ogr")
        if lakes_layer.isValid():
            project.addMapLayer(lakes_layer)
        else:
            log("WARNING: lakes shapefile invalid")
            lakes_layer = None
    style_hydro(rivers_layer, lakes_layer)
    sites_layer = QgsVectorLayer(os.path.join(DATA, "sites.geojson"), "sites", "ogr")
    assert sites_layer.isValid(), "sites layer failed"
    style_sites(sites_layer)
    label_notable(sites_layer)
    project.addMapLayer(sites_layer)
    cities_layer = QgsVectorLayer(os.path.join(DATA, "cities.geojson"), "cities", "ogr")
    label_cities(cities_layer)
    project.addMapLayer(cities_layer)
    # Layer order in setLayers(): the list is TOP-FIRST — the first item
    # is drawn on top. (Verified by rendering: with the basemap first the
    # river lines and site markers were all hidden under it;
    # only labels showed, because labels render in a separate top pass.)
    # Stack, top to bottom: cities/labels, sites, rivers, lakes, basemap.
    layers_main = [l for l in (cities_layer, sites_layer, rivers_layer,
                               lakes_layer, bm_main) if l is not None]
    layers_detail = [l for l in (cities_layer, sites_layer, rivers_layer,
                                 lakes_layer, bm_detail) if l is not None]

    layout = QgsPrintLayout(project)
    layout.initializeDefaults()
    # initializeDefaults creates one default page; drop it — new_page() adds
    # exactly the pages we need.
    pc = layout.pageCollection()
    while pc.pageCount() > 0:
        pc.deletePage(pc.page(0))

    log("building page 1 (main map)…")
    page_main(layout, project, lcc, layers_main, layers_detail, sites)
    log("building key page…")
    page_key(layout, sites)

    log("building index pages…")
    idx_lines = index_lines(sites)
    idx_pages = paginate_index(idx_lines, cols=4, lines_per_col=118)
    log(f"index pages: {len(idx_pages)}")
    for i, cols in enumerate(idx_pages):
        page_index(layout, sites, i + 1, len(idx_pages), cols)

    total = layout.pageCollection().pageCount()
    log(f"total pages: {total}; exporting at {DPI} dpi…")
    exporter = QgsLayoutExporter(layout)
    settings = QgsLayoutExporter.PdfExportSettings()
    settings.dpi = DPI
    settings.forceVectorOutput = True
    settings.appendGeoreference = False
    settings.exportMetadata = False
    # Text as embedded-font text objects, NOT glyph outlines: the outlines
    # were ~101 MB of the 173 MB file (the 12 index pages). If this build
    # of QGIS does not expose the setting, the export falls back to
    # outlines and the file stays large — the log will say so.
    if hasattr(settings, "textRenderFormat"):
        settings.textRenderFormat = QgsRenderContext.TextRenderFormat.AlwaysText
        log("text export: AlwaysText (embedded fonts)")
    else:
        log("WARNING: PdfExportSettings has no textRenderFormat — "
            "text will export as outlines")
    result = exporter.exportToPdf(OUT, settings)
    log("export result:", result, "(0 = Success)")
    if result != QgsLayoutExporter.Success:
        log("EXPORT FAILED")
        sys.exit(1)
    log(f"wrote {OUT} ({os.path.getsize(OUT) / 1e6:.1f} MB)")
    app.exitQgis()


if __name__ == "__main__":
    main()
