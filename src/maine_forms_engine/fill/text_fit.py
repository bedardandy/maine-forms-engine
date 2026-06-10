"""Width-fit helpers for narrow form widgets.

Surfaced by the Qwen edge-case probe: several forms (e.g. PA-033's
mailing/physical address widgets) truncate on real-world long addresses.
These helpers shrink values to fit a target character budget by applying
standard postal/legal abbreviations BEFORE falling back to hard truncation,
so the pre-fill stays readable instead of clipping mid-word.

Used by recipe-3 scripts at fill time when a value flows into a known-
narrow widget.
"""
from __future__ import annotations

import re

# USPS-style street-suffix + directional + unit abbreviations.
_ABBREV = {
    r"\bNorthwest\b": "NW", r"\bNortheast\b": "NE",
    r"\bSouthwest\b": "SW", r"\bSoutheast\b": "SE",
    r"\bNorth\b": "N", r"\bSouth\b": "S", r"\bEast\b": "E", r"\bWest\b": "W",
    r"\bParkway\b": "Pkwy", r"\bBoulevard\b": "Blvd", r"\bAvenue\b": "Ave",
    r"\bStreet\b": "St", r"\bDrive\b": "Dr", r"\bLane\b": "Ln",
    r"\bRoad\b": "Rd", r"\bCourt\b": "Ct", r"\bCircle\b": "Cir",
    r"\bPlace\b": "Pl", r"\bTerrace\b": "Ter", r"\bHighway\b": "Hwy",
    r"\bSuite\b": "Ste", r"\bApartment\b": "Apt", r"\bBuilding\b": "Bldg",
    r"\bFloor\b": "Fl", r"\bDepartment\b": "Dept", r"\bTownship\b": "Twp",
    r"\bIndustrial\b": "Ind",
}


def abbreviate_address(value: str) -> str:
    """Apply standard postal abbreviations (idempotent-ish)."""
    out = value
    for pat, rep in _ABBREV.items():
        out = re.sub(pat, rep, out)
    # Collapse any double spaces introduced.
    return re.sub(r"\s{2,}", " ", out).strip()


_SUFFIXES = {"jr", "jr.", "sr", "sr.", "ii", "iii", "iv", "v", "esq", "esq."}


def fit_name(name: str, max_chars: int) -> str:
    """Shrink a PERSON name to <= max_chars without producing a wrong
    legal name. Strategy, in order, stopping as soon as it fits:
      1. collapse middle name(s) to initials  (Robert James Sterling
         -> Robert J. Sterling)
      2. collapse the first name to an initial too  (R. J. Sterling)
    NEVER truncates a name part — a clipped legal name is worse than an
    overflowing one. If it still doesn't fit after initialing, returns
    the full name unchanged so the edge-probe keeps flagging the widget
    as a genuine too-narrow-for-real-names limitation.
    """
    if not name or len(name) <= max_chars:
        return name
    parts = name.split()
    if len(parts) < 3:
        return name  # nothing safe to collapse (first + last only)
    # Separate a trailing suffix (Jr./III/Esq.) so it survives intact.
    suffix = ""
    if parts[-1].lower().strip(".,") in _SUFFIXES:
        suffix = parts[-1]
        core = parts[:-1]
    else:
        core = parts
    if len(core) >= 3:
        first, *middles, last = core
        # 1) middle(s) -> initials
        mid_i = " ".join(f"{m[0]}." for m in middles if m)
        cand = " ".join(x for x in (first, mid_i, last, suffix) if x)
        if len(cand) <= max_chars:
            return cand
        # 2) first -> initial too
        cand = " ".join(x for x in (f"{first[0]}.", mid_i, last, suffix)
                        if x)
        if len(cand) <= max_chars:
            return cand
    return name  # genuine limitation — leave the real name, don't clip


def fit(value: str, max_chars: int, *, address: bool = False,
        name: bool = False) -> str:
    """Return `value` shrunk to <= max_chars.
      - name=True   : initial-collapse a person name, never truncate.
      - address=True: postal-abbreviate, then word-boundary truncate.
      - otherwise   : word-boundary truncate (generic narrative).
    Returns the value unchanged when it already fits."""
    if not value or len(value) <= max_chars:
        return value
    if name:
        return fit_name(value, max_chars)
    if address:
        value = abbreviate_address(value)
        if len(value) <= max_chars:
            return value
    # Word-boundary truncation as a last resort (narrative only).
    cut = value[:max_chars].rsplit(" ", 1)[0]
    return cut if cut else value[:max_chars]


def widget_char_budget(rect, *, font_pt: float = 10.0) -> int:
    """Rough chars-that-fit estimate for a widget rect [x0,y0,x1,y1].
    Assumes ~0.5*font_pt average glyph advance (Helvetica-ish)."""
    width_pt = float(rect[2]) - float(rect[0])
    if width_pt <= 0:
        return 999
    return max(1, int(width_pt / (font_pt * 0.5)))
