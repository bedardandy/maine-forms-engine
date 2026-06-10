#!/usr/bin/env python3
"""Build a ZapfDingbats-compatible symbol TTF from DejaVu Sans (no fontforge).

The last PDF/UA-1 residual on court forms is the checkbox check glyph: the
"on" appearance draws ZapfDingbats byte 0x34 ('4' = heavy check), and that
base-14 symbol font is never embedded (7.21.4.1). There is no ZapfDingbats TTF
on the box and no fontforge to convert the Type1 clone, so we synthesize one:
take DejaVu Sans (which has ✓✔✗✘ at U+2713/14/17/18), subset to those glyphs,
and add a (1,0) byte cmap mapping the ZapfDingbats check/cross codes to them.
embed_widget_font.build_symbol_font then embeds it (symbolic TrueType, single
(1,0) cmap) so the checkmark renders from an embedded font.

    python3 tools/accessibility/make_zapf_ttf.py /tmp/ZapfDingbats.ttf
"""
from __future__ import annotations

import pathlib
import sys

from fontTools import subset
from fontTools.ttLib import TTFont, newTable

DEJAVU_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans.ttf",
]
# ZapfDingbats byte code -> Unicode glyph in DejaVu. 0x34 ('4') is THE court
# checkbox check (heavy check ✔ = U+2714); the neighbours give robustness for
# any form that uses a lighter check or a ballot-X cross.
ZAPF_TO_UNI = {
    0x33: 0x2713,  # '3' light check
    0x34: 0x2714,  # '4' heavy check  <- the one court forms use
    0x35: 0x2717,  # '5' ballot X
    0x36: 0x2718,  # '6' heavy ballot X
    0x37: 0x2717,  # '7'
    0x38: 0x2718,  # '8'
}


def build(out_path: str) -> str:
    src = next((p for p in DEJAVU_CANDIDATES if pathlib.Path(p).exists()), None)
    if not src:
        raise FileNotFoundError("DejaVuSans.ttf not found (install fonts-dejavu)")
    unis = sorted(set(ZAPF_TO_UNI.values()))
    # subset DejaVu to just the check/cross glyphs (keeps it small + its unicode
    # cmap, which build_symbol_font uses to derive ToUnicode).
    ss = subset.Subsetter(options=subset.Options(
        glyph_names=True, recalc_bounds=True, notdef_outline=True))
    font = TTFont(src)
    ss.populate(unicodes=unis)
    ss.subset(font)
    # name each kept glyph and add a (1,0) byte cmap: ZapfDingbats code -> glyph.
    uni_cmap = font.getBestCmap()
    byte_map = {code: uni_cmap[u] for code, u in ZAPF_TO_UNI.items()
                if u in uni_cmap}
    if not byte_map:
        raise RuntimeError("none of the check/cross glyphs survived subsetting")
    sub = newTable("cmap")
    sub.tableVersion = 0
    fmt0 = newTable("cmap").__class__  # not used; build subtable below
    from fontTools.ttLib.tables._c_m_a_p import CmapSubtable
    t0 = CmapSubtable.newSubtable(0)
    t0.platformID, t0.platEncID, t0.language = 1, 0, 0
    t0.cmap = dict(byte_map)
    # keep a unicode subtable too so build_symbol_font can reverse code->unicode
    t3 = CmapSubtable.newSubtable(4)
    t3.platformID, t3.platEncID, t3.language = 3, 1, 0
    t3.cmap = {u: uni_cmap[u] for u in unis if u in uni_cmap}
    sub.tables = [t0, t3]
    font["cmap"] = sub
    font["name"].setName("ZapfDingbats", 1, 3, 1, 0x409)
    font["name"].setName("ZapfDingbats", 6, 3, 1, 0x409)
    font.save(out_path)
    return out_path


def ensure(cache="/tmp/mcf_zapfdingbats.ttf") -> str | None:
    """Return a usable ZapfDingbats TTF path, building it once if needed."""
    p = pathlib.Path(cache)
    if p.exists() and p.stat().st_size > 0:
        return str(p)
    try:
        return build(cache)
    except Exception:  # noqa: BLE001 — no DejaVu / fontTools issue: caller degrades
        return None


if __name__ == "__main__":
    out = sys.argv[1] if len(sys.argv) > 1 else "/tmp/ZapfDingbats.ttf"
    print(build(out))
