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

## Mapping-staleness verifier promoted (v0.4.0, 2026-06-11)

`maine_forms_engine.verify_mapping` is an extraction of **transactional-tax-
forms `tools/verify_mapping_fields.py`** (the donor for this module; the
other repos had no counterpart). Semantics are donor-identical: per form,
the on-disk blank must match the manifest SHA-256 byte-for-byte before the
field check runs, every `mapping.map` entry must resolve to a live AcroForm
field name (`field_splits.json` renames count as live; `"fill": "manual"`
entries and documented `dropped_keys` never enter `map` and are exempt by
construction), report-only by default, `--stamp` writes
`built_against_sha256` only into mappings that fully verify, `--json`,
non-zero exit when any checked form fails. Documented changes:

- **Path anchors → parameters** (the global policy): the donor's repo-root
  `FORMS` / `MANIFEST` become cwd-relative `DEFAULT_FORMS_ROOT` /
  `DEFAULT_MANIFEST` plus `forms_root=` parameters and `--forms-root` /
  `--manifest` CLI arguments; `main()` gains `argv` / `default_forms_root` /
  `default_manifest` for consumer shims (the `check_upstream` /
  `fetch_pdfs` shim-hook style).
- **Repo format differences → hooks, not conditionals.** The donor inlined
  its repo dialect; the package factors each lookup into a keyword hook on
  `verify_form` / `main`, every default being the donor behavior:
  - `manifest_entry(manifest, form_id) -> dict` — default: the
    `{"forms": {<id>: {...}}}` dialect (all four repos today).
  - `blank_path(fdir, form_id, entry) -> Path` — default:
    `forms/<ID>/<ID>.pdf`.
  - `resolve_widgets(fdir, fmap) -> ({map_key: widget_name}, missing_keys)`
    — default `schema_widget_names` (donor: map keys are `schema.json`
    field_ids, `label` is the widget name). `direct_widget_names` ships for
    the corp dialect (map keys ARE widget names; values are spec dicts).
  - `split_names(fdir) -> set` — default: the donor's `field_splits.json`
    `new_name` reader (also exported under the donor's private name
    `_split_names` for shim re-export).
- **`stamp` anchor fallback.** The donor inserted the stamp after `model` /
  `status` and silently dropped it when a mapping had neither (a case the
  tax repo never hits). The package appends it instead — corp-dialect
  mappings (`{form_id, map}`) now get stamped rather than skipped.
  Indent-/trailing-newline preservation and idempotence are donor-identical.
- Donor prose naming specific tax forms (MRS-1041ME / MRS-700SOV stamp
  placement) was generalized in docstrings; behavior unchanged.

`scripts/diff_against_donor.py` now maps `verify_mapping.py` →
`tools/verify_mapping_fields.py` and proves the extraction against a tax
checkout (all changed lines classify as the documented hook/path changes).

## Probate-migration extensions (2026-06-10)

- `drift/check_upstream.py` — `main` gains `default_timeout` (probate probes
  maineprobate.net with 40s).
- `accessibility/accessibility_pipeline.py` — `main` gains `argv` /
  `default_naming` and a `--naming` CLI flag, so the probate shim's pipeline
  remediates with its `schema-label` /TU strategy. Default unchanged
  (`caption`, the court donor behavior).

## Recipe-tier computations entry point (v0.5.0, 2026-06-11)

`computations.compute_facts(computations, facts) -> (values, warnings)` —
a thin public wrapper over the existing `evaluate()` (no new evaluator, no
new ops, no new spec fields) for forms whose `mapping.json` is pointer-only
(empty `map`) and whose fill runs through per-form recipe code instead of
the mapped path (court MJ-009 / MJ-015 "for a total of $ ..."). It returns
`{target_key: formatted_value}` for omitted targets plus the
`COMPUTATION_MISMATCH` warning list, with the established semantics intact:
supplied always wins (and never appears in the values dict), topological
cascade, silent missing-input skip, never-guess unparseable skip. Targets
and inputs are canonical fact keys that no mapping needs to consume; the
calling harness merges the values into the case before its recipes run
(`case.update(values)` — flat keys resolve before dotted paths). The
mapped-fill wiring in `fill_via_mapping` is unchanged (it keeps calling
`evaluate()` directly for the full report). New in the package, no donor
counterpart.
