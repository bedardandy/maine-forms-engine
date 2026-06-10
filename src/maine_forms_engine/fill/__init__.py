"""Deterministic AcroForm fill core (donor: maine-court-forms engine/)."""

from .canonical import is_canonical, to_engine_case  # noqa: F401
from .fill_via_mapping import fill_via_mapping, resolve_mapping  # noqa: F401
from .form_filler import (  # noqa: F401
    detect_radio_groups,
    fill_form,
    fill_form_from_json,
    generate_template,
    list_form_fields,
    match_radio_option,
)
from .verify_fill import verify_fill  # noqa: F401
