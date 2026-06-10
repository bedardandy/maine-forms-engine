"""Form filler utility: programmatically fill output PDFs with data."""

import json
import logging
from pathlib import Path

import fitz  # PyMuPDF


logger = logging.getLogger(__name__)


# Helvetica average char width factor (proportion of font size). 0.55 is a
# safe estimate that fits most lowercase + uppercase mixes without
# overflowing the rect.
_CHAR_WIDTH_FACTOR = 0.55
_RECT_PADDING_PT = 2.0  # margin we leave inside each widget rect
BODY_FONT = "helv"


def _widget_capacity_chars(widget) -> int:
    """Estimate how many characters fit on one line of this text widget."""
    font_size = float(getattr(widget, "text_fontsize", 0) or 0) or 11.0
    char_width = font_size * _CHAR_WIDTH_FACTOR
    usable_pt = max(0.0, widget.rect.width - 2 * _RECT_PADDING_PT)
    return max(1, int(usable_pt / char_width))


def _split_address_at_commas(value: str, n_widgets: int) -> list[str]:
    """Distribute a comma-separated address across N widgets.

    Why this exists: PP-412 attorney_address binds 2 widgets stacked
    on adjacent lines. A typical Maine address "150 Main Street,
    Auburn, ME 04210" fits entirely in widget 1, leaving widget 2's
    underline visibly empty — vision audit flags it as
    `blank_required`. Address widgets are *meant* to wrap line-1 =
    street, line-2 = city/state/zip. Splitting at commas matches that
    pattern.

    For N=2 (the common case): split on the FIRST comma — line 1 gets
    the street, line 2 gets everything after.
    For N≥3: distribute comma-segments round-robin so trailing widgets
    don't all collapse to the last segment.
    """
    parts = [p.strip() for p in value.split(",")]
    if len(parts) < 2:
        return [value] + [""] * (n_widgets - 1)
    if n_widgets == 2:
        return [parts[0], ", ".join(parts[1:])]
    # Distribute: if more parts than widgets, fold tail into last;
    # if fewer parts than widgets, leave the extras blank.
    out = [""] * n_widgets
    for i, part in enumerate(parts):
        slot = min(i, n_widgets - 1)
        out[slot] = (out[slot] + ", " + part).strip(", ") if out[slot] else part
    return out


def _multi_widget_mode(group: list[dict]) -> str:
    """Distinguish "replicate", "first_only", and "wrap" modes.

    Modes:
      - replicate: same value to every widget — e.g. a caption at top
        of page + the same name in a mid-page field.
      - first_only: multiple widgets at the SAME y (horizontal pair
        where wrap/split would visually collide). Only fill widget 0.
      - wrap: treat widgets as continuation lines of a single value.

    Heuristic on consecutive widget pairs:
      - different page OR vertical gap > 60pt → replicate
      - |Δy| < 5pt AND horizontally adjacent → first_only
        (heuristic detector usually found two boxes where the source
         PDF has one logical text field; rendering both fuses glyphs.
         See PP-410 W004 x=72 y=218 / W005 x=108 y=218 — drawing
         different content into each at the same y produced overlap.)
      - otherwise → wrap

    Why replicate exists: DE-201's decedent_name binds the "Estate of …"
    caption (W003 at y≈146) and the "Full legal name of Decedent" field
    (W014 at y≈398) — same value belongs in both, but the prior wrap
    logic treated W014 as a continuation line of W003 and left it
    blank when the value fit in W003.
    """
    if len(group) < 2:
        return "single"
    prev_page = None
    prev_y = None
    prev_rect = None
    mode = "wrap"
    for e in group:
        page = e.get("_page")
        rect = e["rect"]
        y = rect.y0
        if prev_page is not None:
            if page is not None and prev_page is not None and page != prev_page:
                return "replicate"
            if prev_y is not None and (y - prev_y) > 60.0:
                return "replicate"
            if (prev_y is not None and abs(y - prev_y) < 5.0
                    and prev_rect is not None
                    and rect.x0 < prev_rect.x1 + 5.0):
                mode = "first_only"
        prev_page, prev_y, prev_rect = page, y, rect
    return mode


def _wrap_across_widgets(
    value: str, capacities: list[int]
) -> tuple[list[str], str]:
    """Greedy word-wrap `value` across widgets of given char capacities
    (top-to-bottom). Returns (per_widget_lines, overflow_remainder).
    Overflow is the remaining text that didn't fit in any widget; the
    caller decides whether to truncate, route to addendum, or warn.

    A single word wider than the current widget skips ahead to the first
    widget that can hold it. Words wider than every widget overflow
    immediately.
    """
    words = value.split()
    if not words:
        return [""] * len(capacities), ""
    lines = [""] * len(capacities)
    idx = 0
    i = 0
    while i < len(words):
        if idx >= len(capacities):
            remainder = " ".join(words[i:])
            return lines, remainder
        word = words[i]
        cap = capacities[idx]
        candidate = (lines[idx] + " " + word).strip() if lines[idx] else word
        if len(candidate) <= cap:
            lines[idx] = candidate
            i += 1
        else:
            if not lines[idx]:
                # word alone won't fit current widget — try next
                idx += 1
                continue
            # current line full, advance
            idx += 1
    return lines, ""


def _group_widgets_by_name(doc) -> dict[str, list[dict]]:
    """Return {field_name: [{xref, rect, fontsize, capacity, pos}, ...]}
    sorted top-to-bottom across pages. We key on widget xref (not the
    Widget object) because PyMuPDF re-creates Widget objects on every
    page.widgets() call, so object identity isn't stable across loops.
    """
    groups: dict[str, list[tuple[int, float, float, dict]]] = {}
    for page_num, page in enumerate(doc):
        for w in page.widgets() or []:
            if w.field_type == fitz.PDF_WIDGET_TYPE_CHECKBOX:
                continue
            entry = {
                "xref": w.xref,
                "rect": fitz.Rect(w.rect),
                "fontsize": float(getattr(w, "text_fontsize", 0) or 0) or 11.0,
                "capacity": _widget_capacity_chars(w),
                "_page": page_num,
            }
            groups.setdefault(w.field_name, []).append(
                (page_num, w.rect.y0, w.rect.x0, entry)
            )
    out: dict[str, list[dict]] = {}
    for name, entries in groups.items():
        entries.sort(key=lambda t: (t[0], t[1], t[2]))
        out[name] = [t[3] for t in entries]
        for i, e in enumerate(out[name]):
            e["pos"] = i
    return out


def fill_form(
    pdf_path: str | Path,
    field_data: dict[str, str],
    output_path: str | Path | None = None,
    *,
    tree: dict | None = None,
    addendum_policy: str = "none",
    form_id: str | None = None,
) -> str:
    """Fill an AcroForm PDF with field data.

    Args:
        pdf_path: Path to a fillable PDF (from output/).
        field_data: Dict mapping field_name → value.
        output_path: Where to save. If None, appends '_filled' to filename.
        tree: Optional parsed tree YAML (dict). Used for two things:
            (a) prompt labels for the addendum question headers
            (b) per-tree `addendum_policy` override
        addendum_policy: One of "none" (default — truncate overflow
            inline, no addendum), "court_form" (skip addendum, assume
            the user will attach a court-published continuation form),
            or "auto" (append addendum pages for overflowed answers —
            requires the `engine.addendum` module, which is NOT shipped
            in this repo; requesting it raises ValueError on overflow).
        form_id: Form identifier used in the addendum header. Defaults
            to the PDF filename stem.

    Multi-widget handling: when N>1 widgets share a field name (the
    tree-builder's "continuation line" pattern), the value is greedy
    word-wrapped across all N widgets using each widget's actual rect
    width — so a narrow line-1 followed by full-width lines 2..N gets
    the right split. Overflow past the last widget is routed to an
    appended addendum page (unless addendum_policy disables it), and
    the last inline widget is rewritten to read "See Addendum QN".
    """
    pdf_path = Path(pdf_path)
    if output_path is None:
        output_path = pdf_path.parent / f"{pdf_path.stem}_filled.pdf"
    output_path = Path(output_path)

    # Tree-level overrides (e.g. some forms use a court-published
    # continuation form and shouldn't have an auto-generated addendum).
    if tree:
        addendum_policy = tree.get("addendum_policy") or addendum_policy
        if not form_id:
            form_id = tree.get("form_id")
    if not form_id:
        form_id = pdf_path.stem.split(" ")[0]

    prompts: dict[str, str] = {}
    if tree:
        for n in tree.get("nodes", []):
            if isinstance(n, dict) and n.get("id"):
                prompts[n["id"]] = (
                    n.get("prompt") or n.get("label") or n["id"]
                )

    doc = fitz.open(str(pdf_path))
    filled_count = 0
    overflowed: list[tuple[str, str]] = []  # (field_name, remainder)

    groups = _group_widgets_by_name(doc)

    # Pass 1: write single-widget fields and checkboxes the easy way.
    # For multi-widget text groups we have to bypass AcroForm /V because
    # consolidated widgets share one /V — setting any one would propagate
    # the same value to all kids. Instead we draw the wrapped text as
    # overlay via page.insert_text() in pass 2.
    # NOTE: we store page INDEX (not page object) because doc.new_page()
    # later (addendum) can invalidate earlier page references.
    multi_groups: dict[str, list[tuple[int, int]]] = {}
    for page_idx, page in enumerate(doc):
        for widget in page.widgets():
            name = widget.field_name
            if name not in field_data:
                continue
            value = field_data[name]
            if widget.field_type == fitz.PDF_WIDGET_TYPE_CHECKBOX:
                # Check ONLY on an explicit affirmative token, never on an
                # incidental string. Many forms collapse a caption text widget
                # and a role/option checkbox onto one field_id (a case-folded
                # field-name collision, e.g. "Plaintiff" text + "plaintiff"
                # checkbox), and enum-radios point several boxes at one value
                # (court_type → superior/district/unified). Treating any
                # non-empty value as "checked" then stamps a name onto the role
                # box or checks every enum option. A checkbox is boolean: it is
                # driven by a check token, not by a name/address/enum string.
                # Recipes write "X"/"1"/"Yes" by convention; boolean facts
                # resolve to "true"/"yes"/"X".
                v = (value or "").strip().lower()
                affirmative = {"x", "✓", "yes", "y", "1", "true", "on",
                               "checked", "[x]", "selected"}
                widget.field_value = "Yes" if v in affirmative else "Off"
                widget.update()
                filled_count += 1
                continue
            group = groups.get(name)
            if not group or len(group) == 1:
                widget.field_value = value
                # Auto-size font when the value would overflow the widget
                # at its fixed font size, so long real-world names/
                # addresses shrink to fit instead of clipping at the box
                # edge. (Edge-probe finding: widget_width was the #1
                # automation limitation; this is the systemic safety net
                # downstream of text_fit's abbreviation.) Single-line
                # text widgets only — multi-widget groups wrap in pass 2.
                try:
                    if (widget.field_type == fitz.PDF_WIDGET_TYPE_TEXT
                            and len(str(value)) > _widget_capacity_chars(widget)
                            and (widget.field_flags or 0) & 4096 == 0):
                        widget.text_fontsize = 0  # 0 = auto-fit
                except Exception:
                    pass
                widget.update()
                filled_count += 1
                continue
            # multi-widget text group: queue for pass 2
            multi_groups.setdefault(name, []).append((page_idx, widget.xref))
            # Clear /V so AcroForm doesn't render the shared value on
            # every widget appearance (the original bug).
            widget.field_value = ""
            widget.update()

    # Pass 2: for each multi-widget text group, wrap and draw lines as
    # overlay text. Because PDF widget appearances render on top of page
    # content, we delete the kid widgets first — the consolidated
    # AcroForm field is replaced with stamped text. Track overflows.
    rect_overlays: dict[str, tuple] = {}  # name -> (rect, fs, page_idx)
    for name in multi_groups:
        value = field_data[name]
        group = groups[name]
        caps = [e["capacity"] for e in group]
        mode = _multi_widget_mode(group)
        if mode == "replicate":
            # Every widget gets the full value (e.g. a "Estate of … "
            # caption + a mid-page "Full legal name of Decedent" field
            # both bound to the same node). No wrap, no overflow.
            lines = [value] * len(group)
            remainder = ""
        elif mode == "first_only":
            # Horizontally-overlapping pair detected — render value in
            # the first widget only to avoid glyph collisions.
            lines = [value] + [""] * (len(group) - 1)
            remainder = ""
        elif ("address" in name.lower() and len(group) >= 2
              and "," in value and len(value) <= caps[0]):
            # Multi-widget address whose value fits the first widget —
            # wrap algorithm would leave widget 2+ blank. Split at
            # commas instead so each widget shows a logical line of
            # the address (line-1 street, line-2 city/state/zip).
            lines = _split_address_at_commas(value, len(group))
            remainder = ""
        else:
            lines, remainder = _wrap_across_widgets(value, caps)
        field_data[f"__wrap_cache_{name}"] = lines  # type: ignore[assignment]
        if remainder:
            overflowed.append((name, remainder))
            logger.info(
                "field %r overflowed: %d chars past widget %d",
                name, len(remainder), len(group),
            )
        page_idx_by_xref = {xr: pi for pi, xr in multi_groups[name]}
        for entry in group:
            page_idx = page_idx_by_xref.get(entry["xref"])
            if page_idx is None:
                continue
            page = doc[page_idx]
            line = lines[entry["pos"]]
            rect = entry["rect"]
            fs = entry["fontsize"]
            # Delete the widget so its appearance doesn't cover our text.
            try:
                for w in list(page.widgets() or []):
                    if w.xref == entry["xref"]:
                        page.delete_widget(w)
                        break
            except Exception as e:
                logger.warning("could not delete widget xref=%s: %s",
                               entry["xref"], e)
            if line:
                baseline_y = rect.y1 - max(2.0, (rect.height - fs) / 2.0)
                page.insert_text(
                    (rect.x0 + 1.5, baseline_y), line,
                    fontname=BODY_FONT, fontsize=fs, color=(0, 0, 0),
                )
                filled_count += 1
            if entry["pos"] == len(group) - 1:
                rect_overlays[name] = (rect, fs, page_idx)

    # Check for fields in data that weren't found in the PDF.
    # Multi-widget groups were intentionally deleted in pass 2; exclude
    # them from the missing-fields warning. Checkbox widgets are also
    # excluded from groups (they're filled directly in pass 1), so
    # collect their names separately to avoid false-positive warnings.
    checkbox_names: set[str] = set()
    for page in doc:
        for w in page.widgets() or []:
            if w.field_type == fitz.PDF_WIDGET_TYPE_CHECKBOX:
                checkbox_names.add(w.field_name)
    available = set(groups.keys()) | checkbox_names
    missing_fields = [
        k for k in field_data
        if k not in available and not k.startswith("__wrap_cache_")
    ]

    # Addendum: render overflow pages and rewrite the last inline widget
    # of each overflowed field to point to the addendum entry.
    if overflowed and addendum_policy == "auto":
        # The addendum renderer is not part of this repo's engine; fail
        # with a clear message instead of an ImportError mid-fill.
        try:
            from .addendum import render_addendum_pages  # noqa: PLC0415
        except ImportError as e:
            doc.close()
            raise ValueError(
                f"addendum_policy='auto' requires the engine.addendum "
                f"module (addendum-page rendering), which is not shipped "
                f"in this repo — {len(overflowed)} field(s) overflowed. "
                f"Use addendum_policy='none' (truncate inline) or "
                f"'court_form' (attach the court continuation form)."
            ) from e
        # For each overflowed field, addendum body = wrapped-last-line +
        # remainder. We replace the last widget's text with "See Addendum
        # QN" so the inline form is unambiguous and the addendum carries
        # the tail from where line N-1 left off.
        addendum_bodies: list[tuple[str, str]] = []
        for name, remainder in overflowed:
            cache_key = f"__wrap_cache_{name}"
            lines = field_data.get(cache_key) or []
            tail_inline = lines[-1] if lines else ""
            body = (tail_inline + " " + remainder).strip()
            addendum_bodies.append((name, body))
        refs = render_addendum_pages(doc, form_id, addendum_bodies, prompts)
        ref_map = dict(refs)
        # Multi-widget groups have already been deleted in pass 2 and
        # their text drawn on the page. Cover the last rect with white
        # and stamp "See Addendum QN" in its place.
        for name, ref_text in ref_map.items():
            overlay = rect_overlays.get(name)
            if overlay is not None:
                rect, fs, page_idx = overlay
                page = doc[page_idx]
                page.draw_rect(rect, color=None, fill=(1, 1, 1))
                baseline_y = rect.y1 - max(2.0, (rect.height - fs) / 2.0)
                page.insert_text(
                    (rect.x0 + 1.5, baseline_y), ref_text,
                    fontname=BODY_FONT, fontsize=fs, color=(0, 0, 0),
                )
            else:
                # Single-widget field overflow (rare): write via /V.
                for pg in doc:
                    for w in pg.widgets() or []:
                        if w.field_name == name:
                            w.field_value = ref_text
                            w.update()
                            break
    elif overflowed and addendum_policy == "none":
        logger.info("addendum_policy=none: %d fields truncated inline",
                    len(overflowed))
    elif overflowed and addendum_policy == "court_form":
        logger.info("addendum_policy=court_form: %d fields overflowed; "
                    "user must attach court continuation form",
                    len(overflowed))

    doc.save(str(output_path))
    doc.close()

    logger.info("Filled %d fields in %s", filled_count, output_path.name)
    if missing_fields:
        logger.warning("Fields not found in PDF: %s", missing_fields)
    if overflowed:
        logger.warning(
            "%d fields overflowed (need addendum): %s",
            len(overflowed),
            ", ".join(name for name, _ in overflowed[:5]),
        )

    return str(output_path)


def fill_form_from_json(
    pdf_path: str | Path,
    json_path: str | Path,
    output_path: str | Path | None = None,
) -> str:
    """Fill a form using data from a JSON file.

    JSON format: {"field_name": "value", ...}
    """
    data = json.loads(Path(json_path).read_text())
    return fill_form(pdf_path, data, output_path)


def list_form_fields(pdf_path: str | Path) -> list[dict]:
    """List all fillable fields in a PDF.

    Returns list of dicts with field_name, field_type, page, current_value.
    """
    doc = fitz.open(str(pdf_path))
    fields = []

    type_names = {
        fitz.PDF_WIDGET_TYPE_TEXT: "text",
        fitz.PDF_WIDGET_TYPE_CHECKBOX: "checkbox",
        fitz.PDF_WIDGET_TYPE_RADIOBUTTON: "radio",
        fitz.PDF_WIDGET_TYPE_COMBOBOX: "combobox",
        fitz.PDF_WIDGET_TYPE_LISTBOX: "listbox",
        fitz.PDF_WIDGET_TYPE_SIGNATURE: "signature",
    }

    for page_num, page in enumerate(doc):
        for widget in page.widgets():
            fields.append(
                {
                    "field_name": widget.field_name,
                    "field_type": type_names.get(
                        widget.field_type, f"unknown({widget.field_type})"
                    ),
                    "page": page_num,
                    "current_value": widget.field_value or "",
                    "rect": [
                        widget.rect.x0,
                        widget.rect.y0,
                        widget.rect.x1,
                        widget.rect.y1,
                    ],
                }
            )

    doc.close()
    return fields


def generate_template(
    pdf_path: str | Path, output_path: str | Path | None = None
) -> str:
    """Generate a JSON template with all field names and empty values.

    Useful for creating fill-data files.
    """
    fields = list_form_fields(pdf_path)
    template = {f["field_name"]: "" for f in fields}

    if output_path is None:
        output_path = Path(pdf_path).with_suffix(".template.json")
    Path(output_path).write_text(json.dumps(template, indent=2))

    logger.info("Template with %d fields written to %s", len(template), output_path)
    return str(output_path)
