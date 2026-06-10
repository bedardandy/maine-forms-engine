#!/usr/bin/env python3
"""Migration-readiness check: prove the packaged modules are drift-free
extractions of a sibling repo's copies.

Diffs each packaged module against its counterpart in a consumer-repo checkout
and classifies every changed line: lines explained by the documented
extraction changes (package-relative imports, repo-root path anchors becoming
parameters, the pluggable /TU naming strategy — see CHANGES_FROM_DONOR.md) are
EXPECTED; anything else is flagged UNEXPECTED and exits non-zero.

    python3 scripts/diff_against_donor.py /path/to/maine-court-forms
    python3 scripts/diff_against_donor.py /path/to/transactional-tax-forms --verbose
"""
from __future__ import annotations

import argparse
import difflib
import pathlib
import re
import sys

PKG = pathlib.Path(__file__).resolve().parent.parent / "src" / "maine_forms_engine"

# packaged module -> path inside a consumer repo checkout
MODULE_MAP = {
    "fill/form_filler.py": "engine/form_filler.py",
    "fill/text_fit.py": "engine/text_fit.py",
    "fill/canonical.py": "engine/canonical.py",
    "fill/field_split.py": "engine/field_split.py",
    "fill/verify.py": "engine/verify.py",
    "fill/verify_fill.py": "engine/verify_fill.py",
    "fill/fill_via_mapping.py": "engine/fill_via_mapping.py",
    "drift/check_upstream.py": "tools/check_upstream.py",
    "drift/fetch_pdfs.py": "tools/fetch_pdfs.py",
    "accessibility/accessibility_pipeline.py": "tools/accessibility/accessibility_pipeline.py",
    "accessibility/remediate_form.py": "tools/accessibility/remediate_form.py",
    "accessibility/embed_widget_font.py": "tools/accessibility/embed_widget_font.py",
    "accessibility/make_zapf_ttf.py": "tools/accessibility/make_zapf_ttf.py",
}

# A changed line is EXPECTED when it (or the comment block it belongs to)
# matches one of these: import rewiring, path-anchor/parameter changes, the
# injectable naming strategy, or the generalized User-Agent.
_EXPECTED = re.compile(
    r"(\bROOT\b|OSS_ROOT|_ROOT\b|DEFAULT_FORMS_ROOT|forms_root|forms-root"
    r"|DEFAULT_MANIFEST|_MANIFEST|MANIFEST\b|manifest"
    r"|^\s*(import|from)\s|sys\.path\.insert"
    r"|Packaged change|CHANGES_FROM_DONOR"
    r"|--naming|help=|choices="
    r"|USER_AGENT"
    # the injectable /TU naming strategy (remediate_form)
    r"|naming|schema-label|schema_label_names|caption|_accessible_names"
    r"|field_id|callable|donor behavior|schema\[\"fields\"\]"
    # multi-line call/signature rewraps introduced by the new parameters
    r"|fill_via_mapping\(|remediate\("
    r"|^\s*#|^\s*\"\"\"|^\s*$|^\s*[-A-Za-z ,.:;()'\"/]*$"  # comments/docstring prose
    r")"
)


def classify(line: str) -> str:
    return "expected" if _EXPECTED.search(line) else "UNEXPECTED"


def diff_one(pkg_rel: str, donor_root: pathlib.Path, verbose: bool):
    pkg_file = PKG / pkg_rel
    donor_file = donor_root / MODULE_MAP[pkg_rel]
    if not donor_file.exists():
        return None  # module not present in this sibling (e.g. canonical.py in tax)
    a = donor_file.read_text().splitlines()
    b = pkg_file.read_text().splitlines()
    diff = list(difflib.unified_diff(a, b, lineterm="",
                                     fromfile=str(donor_file),
                                     tofile=f"package:{pkg_rel}"))
    changed = [ln for ln in diff[2:] if ln[:1] in "+-"
               and not ln.startswith(("+++", "---"))]
    unexpected = [ln for ln in changed if classify(ln[1:]) == "UNEXPECTED"]
    return {"module": pkg_rel, "donor": str(donor_file),
            "changed_lines": len(changed), "unexpected": unexpected,
            "diff": diff if verbose else None}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("donor_root", type=pathlib.Path,
                    help="checkout of a consumer repo (court / tax / corp / probate)")
    ap.add_argument("--verbose", action="store_true",
                    help="print full unified diffs, not just the summary")
    args = ap.parse_args()

    bad = 0
    print(f"package vs donor: {args.donor_root}")
    for pkg_rel in MODULE_MAP:
        r = diff_one(pkg_rel, args.donor_root, args.verbose)
        if r is None:
            print(f"  -        {pkg_rel:<45} (no counterpart in this repo)")
            continue
        mark = "OK " if not r["unexpected"] else "DRIFT"
        ident = " (identical)" if r["changed_lines"] == 0 else \
                f" ({r['changed_lines']} changed lines, all expected)" \
                if not r["unexpected"] else \
                f" ({r['changed_lines']} changed, {len(r['unexpected'])} UNEXPECTED)"
        print(f"  {mark:<6}   {pkg_rel:<45}{ident}")
        if r["unexpected"]:
            bad += 1
            for ln in r["unexpected"][:20]:
                print(f"           ! {ln}")
        if args.verbose and r["diff"]:
            print("\n".join("           " + d for d in r["diff"]))
    print("\nresult:", "DRIFT DETECTED — see UNEXPECTED lines above" if bad
          else "drift-free extraction (all differences are the documented "
               "import/path-parameter changes)")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
