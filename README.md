> **AI/legal-use disclosure:** This repository is experimental, AI-assisted workflow software. It is not legal advice, not a primary legal source, and not lawyer-reviewed as a complete statement of law. Outputs are first drafts for human review, intended to reduce creation effort while increasing focused review effort. See [AI_DISCLOSURE.md](AI_DISCLOSURE.md).

# maine-forms-engine

The shared core extracted from the four Maine forms libraries — the ~1,800
lines of fill engine, upstream-drift guard, PDF/UA accessibility remediation,
and MCP scaffolding that were previously copy-ported (and drifting) across:

- [maine-court-forms](https://github.com/bedardandy/maine-court-forms) — Maine Judicial Branch court forms (the extraction donor)
- [transactional-tax-forms](https://github.com/bedardandy/transactional-tax-forms) — Maine Revenue / IRS transactional tax forms
- [maine-corporation-forms](https://github.com/bedardandy/maine-corporation-forms) — Maine SoS business-entity forms
- [maine-probate-forms](https://github.com/bedardandy/maine-probate-forms) — Maine probate court forms

This is an **extraction, not a rewrite**: module behavior is identical to the
donor copies except for documented import/path-parameter changes
([CHANGES_FROM_DONOR.md](CHANGES_FROM_DONOR.md));
`scripts/diff_against_donor.py` proves it against any sibling checkout.

**Experimental, AI-assisted software. Not legal advice.** See
[DISCLAIMER.md](DISCLAIMER.md). Licensed Apache-2.0 ([LICENSE](LICENSE)).

## Install

```bash
pip install "maine-forms-engine @ git+https://github.com/bedardandy/maine-forms-engine"
# with the MCP scaffold:
pip install "maine-forms-engine[mcp] @ git+https://github.com/bedardandy/maine-forms-engine"
```

Not published to PyPI; install from git. Python >= 3.12. Dependencies:
PyMuPDF, pikepdf, fontTools (and optionally `mcp` for the agent server).

## What's inside

| module | contents | donor |
|---|---|---|
| `maine_forms_engine.fill` | `form_filler` (AcroForm writer: multi-widget wrap/replicate, checkbox affirmative-token gate, font auto-fit), `fill_via_mapping` (mapping.json + canonical fact object -> filled PDF, with blank-revision guard, width-fit, shared-field split, zero-resolved failure), `field_split`, `text_fit`, `canonical` (fact object -> engine case adapter), `verify` (pinned-revision manifest checks), `verify_fill` (deterministic post-fill diff) | court `engine/` |
| `maine_forms_engine.drift` | `check_upstream` (re-probe official URLs vs manifest; `%PDF-` non-PDF guard; `--update-manifest`), `fetch_pdfs` (verified on-demand fetch — repos never redistribute blanks) | court `tools/` |
| `maine_forms_engine.verify_mapping` | mapping-staleness verifier: per form, the on-disk blank must match the manifest SHA-256 byte-for-byte AND every `mapping.map` entry must resolve to a live AcroForm field name (`field_splits.json` renames count as live). Report-only by default; `--stamp` writes `built_against_sha256` only for forms that fully verify; `--json`; non-zero exit on any failure. Format hooks (`manifest_entry` / `blank_path` / `resolve_widgets` / `split_names`) default to the tax/court dialect | tax `tools/verify_mapping_fields.py` |
| `maine_forms_engine.accessibility` | `accessibility_pipeline` (remediate -> OpenDataLoader tag tree -> PDF/UA stamp -> veraPDF), `remediate_form` (title//Lang//Tabs + **pluggable /TU naming strategy**), `embed_widget_font` (base-14 -> Liberation embedding + ToUnicode), `make_zapf_ttf` | court `tools/accessibility/` |
| `maine_forms_engine.mcp` | standardized agent scaffold: `find_forms / get_form / plan_fill / fill_form` with one error shape; the repo supplies a backend adapter | new (consolidates 4 server dialects) |
| `specs/` + `maine_forms_engine.specs` | the canonical fact-object spec (prose, repo `specs/`) + a JSON Schema for the `{"forms": {...}}` `pdf_manifest.json` dialect (shipped as package data; `specs.pdf_manifest_schema()`) | tax `docs/integrations/` |

Stays per-repo (deliberately not absorbed): `forms/` artifacts + catalogs,
court recipes + `fill_and_audit` + addendum renderer, corp rubric/when-gating
+ JSON-Schema validation, probate geometry fill path, repo routers, domain
docs.

## Quick use

```python
import pathlib
from maine_forms_engine.fill import fill_via_mapping

res = fill_via_mapping("AD-001", case, pathlib.Path("/tmp/out"),
                       forms_root=pathlib.Path("forms"))
# res: {ok, out_pdf, resolved, unresolved, fields_written, blank_verify, ...}
```

```bash
python3 -m maine_forms_engine.drift.fetch_pdfs --manifest catalog/pdf_manifest.json --forms-root forms
python3 -m maine_forms_engine.drift.check_upstream --json
python3 -m maine_forms_engine.verify_mapping --json          # re-verify mappings; add --stamp after re-mapping
python3 -m maine_forms_engine.fill.fill_via_mapping --form AD-001 --case case.json --out out/
python3 -m maine_forms_engine.accessibility.remediate_form filled.pdf out.pdf --schema forms/AD-001/schema.json --naming caption
```

All path defaults are the shared repo layout relative to the cwd
(`forms/`, `catalog/pdf_manifest.json`); pass `forms_root=` / `--forms-root`
/ `--manifest` explicitly from anywhere else.

## Radio groups — soft lock (yellow light, never written)

The fill engine **never writes radio groups**. Same-named BUTTON widgets with
distinct on-states (or any radio-typed widget) are detected by
`fill.detect_radio_groups` — distinguishing them from same-named text
continuation lines (the wrap path) and the court repos' legitimate same-named
checkbox fan-out (one shared on-state) — and any value routed at one is
skipped with a structured report entry instead of being wrapped as text:

```json
{"field_id": "residency_status", "kind": "radio_group",
 "options": ["Resident", "Nonresident"], "suggested": "Nonresident",
 "action": "manual selection required"}
```

A repo can declare the group in `mapping.json` so the suggestion resolves
from the case without ever writing:

```json
"manual": {
  "residency_status": {"fill": "manual", "kind": "radio_group",
                        "key": "facts.residency_status",
                        "options": ["Resident", "Nonresident"]}
}
```

`fill_via_mapping` results then carry `radio_groups: [...]` (both result
styles). This is deliberately a warning surface only — there is no
radio-group write support.

## Constraints — declarative checkbox paradoxes (warnings only)

`maine_forms_engine.constraints` reads an optional **`constraints.json` next
to a form's `mapping.json`** (that file, not a `mapping.json` block, is the
documented home) declaring selections the printed form cannot logically carry
at once:

```json
{"mutually_exclusive": [
    {"keys": ["facts.resident", "facts.nonresident"],
     "note": "Resident / Nonresident — single-status pair"}],
 "requires": {"facts.designee_pin": ["facts.designee_name"]}}
```

Groups may carry `"inferred": true` (harvested, not literally printed as
"check one") and a `"note"`. `constraints.evaluate(case_or_resolved_values)`
returns `[{code: "MUTUALLY_EXCLUSIVE"|"REQUIRES", keys, severity: "warning",
note?, inferred?}]`; `fill_via_mapping` surfaces it as
`constraint_warnings` in the fill result. **Warnings only, never blocking**:
no constraints file means zero behavior change, and a firing constraint never
alters the written PDF.

## Computations — declarative computed fields (the PDF stays dumb)

`maine_forms_engine.computations` reads an optional **`computations.json`
next to a form's `mapping.json`** declaring the arithmetic the form itself
prints ("Add lines 7a, 7b and 7c.", "Subtract line 2i from line 1j"):

```json
{"computed": {
    "facts.total_payments": {
      "op": "sum",
      "inputs": ["facts.maine_income_tax_withheld",
                  "facts.estimated_tax_payments",
                  "facts.refundable_tax_credits"],
      "formula_text": "d. Total payments. (Add lines 7a, 7b and 7c.)"}}}
```

Ops are deliberately tiny: `sum` (inputs may carry a `"-"` prefix for
"line 1 minus line 2 plus line 3"), `difference` (first minus the rest),
`product`; optional `round` (decimal places) and `floor` (only when the form
prints a clamp like "If zero or less, enter -0-"). `formula_text` must be the
**verbatim printed instruction** — formulas are anti-fabrication-gated to
text on the form (no statutory rates or fee schedules from memory); anything
not verbatim carries `"inferred": true`.

`fill_via_mapping` behavior (both result styles):

- target key **omitted** + all inputs present → the value is computed and
  filled, reported under `computed_fields: [{key, kind: "computed", value,
  formula_text}]`.
- target key **supplied** but contradicting → the supplied value is written
  **as-is** (never enforced/overridden) and `computation_warnings` carries
  `{code: "COMPUTATION_MISMATCH", key, supplied, computed, formula_text,
  severity: "warning"}`.
- an input missing → skipped silently; present but unparseable → skipped
  with a `computation_notes` entry, never guessed.

Evaluation is topological (computed values feed later computations; a cycle
is a load error), parsing is deterministic (`"$1,234.56"`, commas,
parentheses-negatives), comparison tolerates formatting ("1300" vs
"1,300.00"), and computed output mimics the input values' formatting style.
**Nothing is embedded in the PDF** — no AcroForm calculation JavaScript, no
field locking; no computations file means zero behavior change.

### Recipe-tier forms: the facts-only entry point

Some forms are pointer-only — `mapping.json` has an empty `map` and the fill
is driven by per-form recipe code reading facts off the case — yet still
print deterministic arithmetic ("for a total of $ ..."). For those,
`compute_facts(computations, facts)` is the **mapping-independent public
entry point**, running the exact same evaluator and semantics as the mapped
fill. The spec is the same `computations.json` schema; targets and inputs
are canonical fact keys that **no mapping needs to consume**:

```python
from maine_forms_engine.computations import compute_facts, load_computations

comp = load_computations(form_dir)            # None when absent (fine)
values, warnings = compute_facts(comp, case)  # ({key: value}, [warnings])
case.update(values)                           # omitted targets only
# ... recipes now read the computed facts off the case ...
```

- `values` is `{target_key: formatted_value}` only for targets the case
  **omits** (all inputs present), keys exactly as spelled in the spec
  (`"facts.judgment_total"`). A flat `case.update(values)` suffices — every
  engine lookup resolves flat keys before dotted paths. The call never
  mutates `case`.
- `warnings` is the `COMPUTATION_MISMATCH` list: a supplied target always
  wins (never in `values`, never overridden); a contradiction only warns.
  Surface them alongside the fill report like `computation_warnings`.
- Same cascade (computed or supplied values feed later computations,
  topologically), same skips (missing input → silent; unparseable input or
  supplied value → skipped, never guessed — those notes live in the full
  `evaluate(...)` report, not the tuple); `None`/empty spec → `({}, [])`.

## The MCP backend adapter

A consuming repo's `tools/agent_server.py` shrinks to a backend object plus
two lines:

```python
from maine_forms_engine.mcp import FormsBackend, UnknownFormError, main

class Backend:                      # implements maine_forms_engine.mcp.FormsBackend
    name = "maine-court-forms"      # unique per repo: all four can register at once

    def find_forms(self, query: str, top_k: int) -> list | dict:
        """list of candidates, or a dict (e.g. {'workflows': [...], 'forms': [...]})"""

    def get_form(self, form_id: str) -> dict:
        """metadata/trust/fields; raise UnknownFormError for unknown ids"""

    def fill_form(self, form_id: str, case: dict, out_dir: str) -> dict:
        """write the PDF, return the repo's result dict (carries 'ok')"""

    # OPTIONAL: def plan_fill(self, form_id: str, case: dict) -> dict

if __name__ == "__main__":
    raise SystemExit(main(Backend()))
```

The scaffold standardizes the tool surface (`query` / `case` / `out_dir`,
`top_k`) and the error shape — every failure is
`{"ok": False, "error": str, "error_type": str}`, never a raised exception —
while routing, trust vocabulary, and fill paths stay in the repo.

## Migrating the consumer repos

Order: **tax → court → corp → probate** (proof-of-concept on the
byte-identical subset first, then the donor, then the real divergence work).

1. **transactional-tax-forms** — its `engine/` was byte-identical to court's
   at review time; replace `engine/{form_filler,fill_via_mapping,field_split,
   text_fit,verify}.py` + `tools/{check_upstream,fetch_pdfs}.py` with package
   imports. NOTE: the 2026-06 fix round forked tax's `form_filler` (returns a
   result dict, rejects addendum policies) and `fill_via_mapping`
   (`FILLABLE_STATUSES` gate) from court's; this package ships **court's**
   behavior, so tax keeps thin wrappers for those two deltas or upstreams
   them as package options.
2. **maine-court-forms** — the donor; deletes its copies, keeps recipes,
   `fill_and_audit`, the addendum renderer, and its router behind a backend
   adapter.
3. **maine-corporation-forms** — consumes only `drift` + the `mcp` scaffold
   initially. Its divergences are real migration work, not drop-in: a forked
   `{"pdfs": [...]}` manifest (convert to the `{"forms": {...}}` dialect in
   the `{"forms": {...}}` dialect of `maine_forms_engine.specs/
   pdf_manifest.schema.json` to use `drift`), an **inverted** mapping
   direction (`{"fields": {canonical_key: {widget_id, confidence}}}`), a flat
   `entity.*`/`clerk.*`/`filing.*` case shape (vs the canonical fact object),
   and a pypdf fill engine (vs PyMuPDF) — so `fill` stays out of corp until
   those converge.
4. **maine-probate-forms** — consumes `drift` + `accessibility` (with
   `naming="schema-label"`) + the `mcp` scaffold; its geometry fill path
   stays its own.

## Development

```bash
pip install -e ".[test,mcp]"
python -m pytest tests/ -v               # fully offline; fixtures synthesized in-test
python3 scripts/diff_against_donor.py /path/to/maine-court-forms   # drift-free proof
```
