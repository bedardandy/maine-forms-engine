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
| `maine_forms_engine.accessibility` | `accessibility_pipeline` (remediate -> OpenDataLoader tag tree -> PDF/UA stamp -> veraPDF), `remediate_form` (title//Lang//Tabs + **pluggable /TU naming strategy**), `embed_widget_font` (base-14 -> Liberation embedding + ToUnicode), `make_zapf_ttf` | court `tools/accessibility/` |
| `maine_forms_engine.mcp` | standardized agent scaffold: `find_forms / get_form / plan_fill / fill_form` with one error shape; the repo supplies a backend adapter | new (consolidates 4 server dialects) |
| `specs/` | the canonical fact-object spec + a JSON Schema for the `{"forms": {...}}` `pdf_manifest.json` dialect | tax `docs/integrations/` |

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
python3 -m maine_forms_engine.fill.fill_via_mapping --form AD-001 --case case.json --out out/
python3 -m maine_forms_engine.accessibility.remediate_form filled.pdf out.pdf --schema forms/AD-001/schema.json --naming caption
```

All path defaults are the shared repo layout relative to the cwd
(`forms/`, `catalog/pdf_manifest.json`); pass `forms_root=` / `--forms-root`
/ `--manifest` explicitly from anywhere else.

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
   `specs/pdf_manifest.schema.json` to use `drift`), an **inverted** mapping
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
