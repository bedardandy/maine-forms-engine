"""Shared-AcroForm-field runtime split (engine/field_split.py donor behavior):
a value mapped to one appearance must stop fanning out to the field's other,
semantically different appearance."""
import json

import fitz
import pikepdf
import pytest

from maine_forms_engine.fill.field_split import specs_for, split_to_copy


@pytest.fixture
def shared_field_tree(tmp_path):
    """forms/TEST-S/ with a blank whose ONE field `shared` has widget kids on
    page 1 and page 2 (the OTH-029 pattern), plus its field_splits.json."""
    fdir = tmp_path / "forms" / "TEST-S"
    fdir.mkdir(parents=True)
    blank = fdir / "TEST-S.pdf"

    doc = fitz.open()
    for pno in range(2):
        page = doc.new_page()
        w = fitz.Widget()
        w.field_name = f"tmp_{pno}"
        w.field_type = fitz.PDF_WIDGET_TYPE_TEXT
        w.rect = fitz.Rect(72, 100, 300, 120)
        page.add_widget(w)
    doc.save(str(blank))
    doc.close()

    # restructure with pikepdf: one parent field `shared` with two widget kids
    with pikepdf.open(str(blank), allow_overwriting_input=True) as pdf:
        kids = []
        for i, pg in enumerate(pdf.pages):
            annot = pg["/Annots"][0]
            for k in ("/T", "/FT"):
                if k in annot:
                    del annot[k]
            annot["/P"] = pg.obj
            kids.append(annot)
        parent = pdf.make_indirect(pikepdf.Dictionary({
            "/T": pikepdf.String("shared"),
            "/FT": pikepdf.Name("/Tx"),
            "/Kids": pikepdf.Array(kids),
        }))
        for k in kids:
            k["/Parent"] = parent
        pdf.Root.AcroForm.Fields = pikepdf.Array([parent])
        pdf.save()

    (fdir / "field_splits.json").write_text(json.dumps({"splits": [
        {"field": "shared", "page": 2, "new_name": "detached_box", "clear": True},
    ]}))
    return tmp_path / "forms"


def test_split_detaches_second_appearance(shared_field_tree, tmp_path):
    src = shared_field_tree / "TEST-S" / "TEST-S.pdf"
    dst = tmp_path / "TEST-S.split.pdf"
    n = split_to_copy(src, dst, "TEST-S", shared_field_tree)
    assert n == 1
    with pikepdf.open(str(dst)) as pdf:
        names = {str(f["/T"]) for f in pdf.Root.AcroForm.Fields if "/T" in f}
        assert names == {"shared", "detached_box"}
        # the original field keeps exactly one kid
        shared = next(f for f in pdf.Root.AcroForm.Fields
                      if str(f.get("/T", "")) == "shared")
        assert len(shared["/Kids"]) == 1
    # source blank untouched
    with pikepdf.open(str(src)) as pdf:
        assert len(pdf.Root.AcroForm.Fields) == 1


def test_no_specs_means_no_copy(tmp_path):
    root = tmp_path / "forms"
    (root / "TEST-N").mkdir(parents=True)
    assert specs_for("TEST-N", root) == []
    assert split_to_copy(tmp_path / "in.pdf", tmp_path / "out.pdf",
                         "TEST-N", root) == 0


def test_split_to_copy_closes_the_pdf_handle(shared_field_tree, tmp_path,
                                              monkeypatch):
    """Regression (audit 2026-07-06): split_to_copy used to open the source
    PDF without a context manager, leaking one handle per fill inside the
    long-lived MCP server. Assert the opened Pdf is closed before return."""
    import maine_forms_engine.fill.field_split as fs

    opened = []
    real_open = pikepdf.open

    def tracking_open(*a, **kw):
        pdf = real_open(*a, **kw)
        opened.append(pdf)
        return pdf

    monkeypatch.setattr(pikepdf, "open", tracking_open)
    # field_split imports pikepdf lazily inside the function, so the patched
    # module attribute is what it resolves.
    src = shared_field_tree / "TEST-S" / "TEST-S.pdf"
    dst = tmp_path / "TEST-S.split.pdf"
    n = fs.split_to_copy(src, dst, "TEST-S", shared_field_tree)
    assert n == 1
    assert opened, "split_to_copy did not open the source PDF"
    # every Pdf opened by split_to_copy must be closed on return. pikepdf
    # marks a closed handle by resetting .filename to "closed input source".
    for pdf in opened:
        assert pdf.filename == "closed input source", (
            "split_to_copy leaked an open pikepdf handle")
