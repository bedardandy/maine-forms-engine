#!/usr/bin/env python3
"""Full accessibility pipeline for a FILLED probate form -> Tagged PDF.

The validated OSS/offline path (no Adobe, no GPU, no network):

  1. schema-driven field names + doc title + /Lang + tab order   (remediate_form)
  2. logical content/structure tag tree                          (OpenDataLoader)
  3. PDF/UA identifier stamp                                      (pikepdf XMP)
  4. optional veraPDF UA-1 validation report

On DE-101 (filled) this took veraPDF UA-1 failures 234 -> 9; the content tag tree
(7.1/7.2) and form-field tagging (7.18) go to 0. The residual ~9 are
source-font-embedding (a defect inherited from the government source PDF) + a
couple of free-tier tagger nits. NOTE: do NOT try to fix the fonts with a
ghostscript re-distill — it flattens the form and strips toUnicode, making UA-1
far worse (tested: 826 failures). Full UA-1 needs OpenDataLoader's enterprise
PDF/UA export. Not legal advice.

Deps (optional, not core): opendataloader-pdf (Apache-2.0), veraPDF (validation).

    python3 accessibility_pipeline.py filled.pdf out.pdf \
        --schema repo/forms/<ID>/schema.json [--validate]
"""
from __future__ import annotations

import argparse
import glob
import os
import pathlib
import re
import subprocess
import sys
import tempfile

import pikepdf

# Packaged change (see CHANGES_FROM_DONOR.md): donor's sys.path hack becomes a
# package-relative import.
from . import remediate_form  # step 1 logic

ODL_PYTHON = os.environ.get("ODL_PYTHON", sys.executable)  # python with opendataloader-pdf installed
VERAPDF = os.environ.get("VERAPDF", "verapdf")  # veraPDF CLI on PATH


def tag_with_opendataloader(inp, out_dir):
    subprocess.run([ODL_PYTHON, "-c",
                    "import opendataloader_pdf,sys;"
                    "opendataloader_pdf.convert(input_path=[sys.argv[1]],"
                    "output_dir=sys.argv[2],format='tagged-pdf')",
                    str(inp), str(out_dir)], check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    hits = glob.glob(str(pathlib.Path(out_dir) / "*_tagged.pdf"))
    if not hits:
        raise RuntimeError("OpenDataLoader produced no *_tagged.pdf")
    return hits[0]


def _fix_form_kids(pdf):
    """PDF/UA 7.18.4: a Form structure element (no Role attr) must contain only the
    OBJR to its widget. Some taggers add extra marked-content (MCID) integers as
    siblings; strip them so /K is just the OBJR. Validated to clear 7.18.4/2 with
    no other-clause regression."""
    fixed = [0]

    def walk(node, n=[0]):
        if n[0] > 5000:
            return
        n[0] += 1
        if str(node.get("/S", "")) == "/Form":
            k = node.get("/K")
            if isinstance(k, pikepdf.Array):
                objrs = [x for x in k if isinstance(x, pikepdf.Dictionary)
                         and str(x.get("/Type", "")) == "/OBJR"]
                if objrs and len(k) > len(objrs):
                    node["/K"] = objrs[0] if len(objrs) == 1 else pikepdf.Array(objrs)
                    fixed[0] += 1
        kk = node.get("/K")
        if isinstance(kk, pikepdf.Array):
            for c in kk:
                if isinstance(c, pikepdf.Dictionary):
                    walk(c)
        elif isinstance(kk, pikepdf.Dictionary):
            walk(kk)
    sr = pdf.Root.get("/StructTreeRoot")
    if sr:
        walk(sr)
    return fixed[0]


def finalize(inp, out):
    """Fix Form-element child structure (7.18.4) + stamp the PDF/UA identifier."""
    with pikepdf.open(inp) as p:
        n = _fix_form_kids(p)
        # PDF/UA 7.21.4.2: a CIDSet, IF present, must list all CIDs. CIDSet is
        # optional (deprecated in PDF 2.0); subsetters (gs) often write an
        # incomplete one. Removing it satisfies the conditional rule.
        for obj in p.objects:
            if isinstance(obj, pikepdf.Dictionary) and \
                    str(obj.get("/Type", "")) == "/FontDescriptor" and "/CIDSet" in obj:
                del obj["/CIDSet"]
        # PDF/UA 6.2: MarkInfo/Marked must be true. Only assert it when a real tag
        # tree exists (it does after OpenDataLoader) — never fake it over no tree.
        if "/StructTreeRoot" in p.Root:
            mi = p.Root.get("/MarkInfo", pikepdf.Dictionary())
            mi["/Marked"] = True
            p.Root["/MarkInfo"] = mi
        with p.open_metadata(set_pikepdf_as_editor=False) as m:
            m["pdfuaid:part"] = "1"
        p.save(out)
    return n


def validate(pdf):
    try:
        xml = subprocess.run([VERAPDF, "--flavour", "ua1", pdf],
                             capture_output=True, text=True, timeout=180).stdout
    except Exception as e:
        return f"(veraPDF unavailable: {e})"
    m = re.search(r'failedRules="(\d+)" passedChecks="(\d+)" failedChecks="(\d+)"', xml)
    comp = re.search(r'isCompliant="(\w+)"', xml)
    fails = [(c, t, fc) for c, t, fc in re.findall(
        r'clause="([^"]+)"[^>]*testNumber="([^"]+)"[^>]*status="failed"[^>]*failedChecks="(\d+)"', xml)]
    out = [f"veraPDF UA-1: compliant={comp.group(1) if comp else '?'} "
           f"failedRules={m.group(1) if m else '?'} failedChecks={m.group(3) if m else '?'}"]
    for c, t, fc in fails:
        out.append(f"  clause {c} test {t} ({fc})")
    return "\n".join(out)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("pdf"); ap.add_argument("out")
    ap.add_argument("--schema", required=True)
    ap.add_argument("--lang", default="en-US"); ap.add_argument("--title", default=None)
    ap.add_argument("--validate", action="store_true")
    a = ap.parse_args()
    if pathlib.Path(a.out).resolve() == pathlib.Path(a.pdf).resolve():
        print("refusing to overwrite the original", file=sys.stderr); return 2

    tmp = pathlib.Path(tempfile.mkdtemp())
    step1 = tmp / "step1.pdf"
    done, title = remediate_form.remediate(a.pdf, str(step1), a.schema, a.lang, a.title)
    print(f"[1/3] field names {done['tu_set']}/{done['tu_total']}, title '{title}', "
          f"/Lang {a.lang}, tab order")
    # OpenDataLoader builds the content tag tree (7.1/7.2). It's an optional dep;
    # if it's not installed, degrade gracefully — keep the field names / title /
    # lang / tab order (the 234->216 win) and skip the tree. finalize() won't fake
    # /Marked because remediate() created no /StructTreeRoot, so the result stays
    # honest, just not fully tagged.
    try:
        tagged = tag_with_opendataloader(step1, tmp)
        print("[2/3] OpenDataLoader: logical tag tree built")
    except Exception as e:
        tagged = step1
        print(f"[2/3] OpenDataLoader unavailable ({type(e).__name__}) — skipping the "
              "content tag tree. Field names/title/lang/tab order still applied; "
              "install opendataloader-pdf or set ODL_PYTHON for the full PDF/UA tree.",
              file=sys.stderr)
    nfixed = finalize(tagged, a.out)
    print(f"[3/3] form-field tags fixed ({nfixed} Form elements) + PDF/UA id stamped -> {a.out}")
    if a.validate:
        print(validate(a.out))


if __name__ == "__main__":
    raise SystemExit(main())
