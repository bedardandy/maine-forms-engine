# Changes from the donor copies

This package is an **extraction, not a rewrite**. Donor = `maine-court-forms`
(the most-fixed fork at extraction time, 2026-06). Module behavior is
identical except for the entries below; `scripts/diff_against_donor.py`
proves it against a sibling checkout.

## Global

- **Repo-root path anchors → cwd-relative defaults + explicit parameters.**
  The donors resolved `forms/` and `catalog/pdf_manifest.json` from their repo
  root via `__file__`. A pip-installed package has no repo root, so every such
  default is now relative to the current working directory (identical
  effective behavior when run from a consumer repo root, since all consumers
  share that layout) and every entry point takes the path explicitly
  (`forms_root=` / `manifest=` parameters; `--forms-root` / `--manifest` CLI
  arguments).
- **`sys.path` hacks → package-relative imports.** `from engine import ...`,
  `from tools.fetch_pdfs import ...`, and the accessibility directory's
  `sys.path.insert` become `from ..fill import ...` / `from . import ...`.
- Environment variables are unchanged (`MCF_VERIFY_BLANK`, `WIDGET_TTF`,
  `ZAPF_TTF`, `LIBERATION_DIR`, `ODL_PYTHON`, `VERAPDF`), so existing consumer
  configurations keep working. `MCF_VERIFY_BLANK` governing all consumers is a
  known (inherited) wart.

## fill/

- `form_filler.py`, `text_fit.py`, `canonical.py` — **byte-identical** to the
  donor.
- `field_split.py` — `OSS_ROOT`-derived default replaced by
  `DEFAULT_FORMS_ROOT = Path("forms")` in `specs_for`.
- `verify.py` — `_MANIFEST` / forms-root defaults are now cwd-relative
  (`catalog/pdf_manifest.json`, `forms`); the `manifest_path` / `forms_root` /
  `manifest` parameters are unchanged donor API.
- `verify_fill.py` — library function unchanged; the CLI's schema lookup uses
  a new `--forms-root` argument instead of the repo root.
- `fill_via_mapping.py` — `forms_root` defaults change as above; the CLI gains
  `--forms-root`. **One behavioral assumption changed:** the donor's fill-time
  blank-revision guard loaded the manifest from the repo root; the package
  resolves it **next to `forms_root`** (`<forms_root>/../catalog/
  pdf_manifest.json`) and passes it to `verify.guard_blank`, falling back to
  verify's cwd-relative default when that file does not exist. Run from a
  consumer repo root this is the same file the donor read.

## drift/

- `check_upstream.py` — donor's `sys.path` + `engine`/`tools` imports become
  package-relative; the hardcoded repo-root `MANIFEST` becomes `--manifest`
  (default `catalog/pdf_manifest.json`). Classification logic (`check_one`,
  including the `%PDF-` non-PDF guard) and `--update-manifest` are unchanged.
- `fetch_pdfs.py` — destination root becomes `--forms-root` (default
  `forms`); `USER_AGENT` renamed from the court-specific string to
  `maine-forms-engine/fetch_pdfs (...)` so non-court consumers don't announce
  themselves as the court repo. Download/verify logic unchanged.

## accessibility/

- `accessibility_pipeline.py` — byte-identical court↔probate at extraction;
  only the `sys.path` hack → `from . import remediate_form` changed.
- `embed_widget_font.py` — court's base-14-family-generalized version; only
  `import make_zapf_ttf` → `from . import make_zapf_ttf` changed.
  `make_zapf_ttf.py` ships verbatim (it is embed_widget_font's optional
  synthesized-ZapfDingbats fallback).
- `remediate_form.py` — court's caption-derived /TU core, with the **/TU
  naming strategy made injectable** (`remediate(..., naming=...)`):
  `"caption"` (default — exact donor behavior), `"schema-label"` (the probate
  fork's behavior, reimplemented verbatim as `schema_label_names`), or any
  callable `(pdf_path, schema, mapping_map) -> {field_name: name}`. The corp
  fork's third variant can be passed as a callable. New `--naming` CLI flag;
  default output is unchanged.

## mcp/

New code (the "standardized MCP scaffold" the review called for), not an
extraction: `adapter.FormsBackend` + `server.build_server`. Parameter names
standardize on `query` / `case` / `out_dir`; failures always return
`{"ok": False, "error": ..., "error_type": ...}`.

## Not absorbed (stays per-repo)

`forms/` artifacts and catalogs; court recipes + `fill_and_audit` (and
therefore `engine/fill.py`, `build_kv_map`, the addendum renderer —
`form_filler`'s `addendum_policy="auto"` still raises the donor's ValueError
explaining the renderer is not shipped); corp rubric / when-gating /
JSON-Schema validation; probate geometry fill path; repo routers
(`find_forms` lexical/LLM routers stay behind the MCP adapter); domain docs.
