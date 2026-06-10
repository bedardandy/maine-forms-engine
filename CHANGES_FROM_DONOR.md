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

## Post-extraction reconciliation (2026-06-10)

The package shipped court's engine behavior; the tax fork had since gained
improvements the package lacked. Reconciled as compatible features — every
default still matches the court donor, the tax consumer opts in:

- `fill/form_filler.py` —
  - `fill_form(..., return_report=True)` returns the tax fork's result dict
    `{output_path, filled_count, missing_fields, overflowed}` instead of the
    donor's output-path string (default `False` = donor contract).
    `fill_form_from_json` gains the same flag.
  - `fill_form(..., supported_policies=frozenset({...}))` refuses an
    unsupported effective `addendum_policy` (after any tree override) up
    front — tax's hard-raise on anything but `"none"`. Default `None` =
    donor behavior (every policy accepted; `"auto"` fails at overflow time).
- `fill/fill_via_mapping.py` —
  - `resolve_mapping` / `fill_via_mapping` gain `fillable_statuses`
    (allowlist mode; default `None` = donor blocklist of
    recipe/no-mappable-fields), `skip_reasons` (per-status refusal text),
    and `require_built_against` + `manifest_path` (tax commit 9524439's
    staleness gate: a mapping whose `built_against_sha256` disagrees with
    the manifest pin is refused — the MRS-1041ME incident class).
  - `fill_via_mapping` gains `blank_verify_env` (which env vars set the
    blank-guard mode; tax reads `TTF_VERIFY_BLANK` then `MCF_VERIFY_BLANK`)
    and `result_style="court"|"tax"` (the two forks' result dialects:
    court = coverage ratio + `blank_verify` dict + zero-resolved loud
    failure; tax = `missing_widgets`/`overflowed` diagnostics,
    `fields_written` = widgets actually written, `blank_verified` bool).
  - the mapping-refused return now carries `skipped: true` + `status`
    alongside the donor's `{form_id, ok, error}` (superset; tax parity).
- `drift/check_upstream.py` — adopted the tax fork's revised-blank
  inspection: CHANGED results carry `got_num_pages`/`got_has_acroform` and
  `--update-manifest` adopts them along with sha256/bytes. `check_one` and
  `main()` gain a `downloader` hook (so consumer-shim tests can keep stubbing
  their `tools.check_upstream._download`), and `main()` gains `argv` /
  `default_manifest` / `update_hint` (string or callable taking the changed
  list) for consumer shims.
- `drift/fetch_pdfs.py` — `main()` gains `argv` / `default_manifest` /
  `default_forms_root` for consumer shims.
- `mcp/server.py` — `build_server` / `main` gain `extra_tools=[...]`:
  repo-specific callables (e.g. corp's filing `preflight`) registered beyond
  the standard four, wrapped in the scaffold's one error shape.

Tests: tax's BuiltAgainstSha pair ported (drifted refused / matching
resolves), plus allowlist-gate, tax result-style, report/policy-gate, and
extra_tools coverage. Version stays 0.1.0 (pre-release).

## Corp-migration extensions (2026-06-10)

Added for the maine-corporation-forms shim (defaults unchanged — donor
behavior unless a consumer opts in):

- `drift/check_upstream.py` — `check_one`/`main` gain `on_download_error`
  (refine failed-download classification; corp maps transient timeout/DNS/
  HTTP-5xx to `ERROR`, which is reported but never gates the exit code,
  reserving `GONE` for a definitive 404/410 or a non-PDF response);
  `main` gains `entry_filter` (restrict the default probe set, e.g. corp's
  `"fetch": true` flag) and `default_retries`.
- `drift/fetch_pdfs.py` — `main` gains the same `entry_filter` /
  `default_retries` hooks plus a `--list` flag (show what would be fetched,
  do not download — the corp fork's feature, now available everywhere).
- `maine_forms_engine.specs` — `pdf_manifest.schema.json` now ships inside
  the package (package data + `specs.pdf_manifest_schema()` loader) so a
  consumer repo can validate its converted manifest in CI. The prose
  canonical-fact-object spec stays in the repo `specs/` directory.
