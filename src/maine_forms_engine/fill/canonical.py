#!/usr/bin/env python3
"""Adapter: canonical fact object -> engine case shape.

The library's portable boundary is the **canonical fact object**
(``{matter, parties, party, facts}``, defined in docs/integrations/README.md),
which is what each form's ``mapping.json`` targets and what external adapters
translate their host data into. The reference engine, however, fills from its
own **engine case shape** (top-level ``court``/``docket_no``/``parties``/...).

``to_engine_case`` bridges the two so a canonical fact object — e.g. a form's
``examples/sample_case.json`` — can drive the full engine (generic field map
*and* the form-specific recipes). It is the one missing translator noted in
engine/README.md ("Two case shapes").
"""
from __future__ import annotations

# canonical party attr -> engine party attr (only where the names differ;
# full_name/address/city/state/zip/phone/email pass through unchanged).
_PARTY_KEY_REMAP = {"date_of_birth": "dob"}


def _remap_party(p: dict) -> dict:
    if not isinstance(p, dict):
        return p
    out = dict(p)
    for canon, eng in _PARTY_KEY_REMAP.items():
        if canon in out and eng not in out:
            out[eng] = out[canon]
    return out


def is_canonical(case: dict) -> bool:
    """A canonical fact object has a top-level ``matter`` and no engine-shape
    top-level ``court``/``docket_no``."""
    return ("matter" in case) and not ({"court", "docket_no"} & set(case))


def to_engine_case(c: dict) -> dict:
    """Translate a canonical fact object into the engine case shape.

    Idempotent-ish: if ``c`` already looks like an engine case, it is returned
    unchanged, so callers can pass either shape.
    """
    if not is_canonical(c):
        return c

    matter = c.get("matter") or {}
    parties = {role: _remap_party(p)
               for role, p in (c.get("parties") or {}).items()}

    # The canonical flat ``party`` (the single filing party) has no engine
    # equivalent; if no roles were given, seed it as the plaintiff so the
    # generic map still has a party to work with.
    party = c.get("party") or {}
    if party and not parties:
        parties["plaintiff"] = _remap_party(party)

    return {
        "case_id": c.get("case_id") or matter.get("case_id"),
        "case_no": matter.get("case_number") or matter.get("docket_number"),
        "docket_no": matter.get("docket_number"),
        "case_type": matter.get("case_type"),
        # The engine stamps "Dated:"/signature-date lines from these; without
        # them every such line renders blank. The canonical filing date lives
        # in matter.filing_date (with a top-level fallback); event_date falls
        # back to the filing date.
        "filing_date": matter.get("filing_date") or c.get("filing_date"),
        "event_date": (matter.get("event_date") or c.get("event_date")
                       or matter.get("filing_date") or c.get("filing_date")),
        "court": {
            "name": matter.get("court_type"),
            "county": matter.get("court_county"),
            "location": matter.get("court_location"),
        },
        # The canonical object doesn't carry a separate notary county; the
        # court county is the right default for the jurat block.
        "notary_county": matter.get("court_county"),
        "parties": parties,
        "facts": c.get("facts") or {},
    }
