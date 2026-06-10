"""Drift detection (ported from maine-court-forms tests/test_verify.py).

All offline — a synthetic manifest and a stubbed downloader. The donor's
RealManifest case (which iterated the repo's committed manifest) is replaced
by a structural check of the spec example, since the package ships no catalog.
"""
import hashlib
import json
import pathlib
import re
import tempfile
import unittest
from unittest import mock

from maine_forms_engine.fill import verify


def _sha(b):
    return hashlib.sha256(b).hexdigest()


def _manifest(blank: bytes):
    return {"forms": {"TEST_X": {
        "sha256": _sha(blank), "bytes": len(blank),
        "url": "https://example.test/TEST_X",
    }}}


class VerifyBlank(unittest.TestCase):
    def _write(self, root, blank):
        d = pathlib.Path(root) / "TEST_X"
        d.mkdir()
        (d / "TEST_X.pdf").write_bytes(blank)

    def test_match(self):
        blank = b"%PDF-1.7 the enriched revision\n"
        with tempfile.TemporaryDirectory() as td:
            self._write(td, blank)
            ok, detail = verify.verify_blank("TEST_X", forms_root=td, manifest=_manifest(blank))
            self.assertTrue(ok, detail)

    def test_detects_swap(self):
        blank = b"%PDF-1.7 the enriched revision\n"
        with tempfile.TemporaryDirectory() as td:
            self._write(td, b"%PDF-1.7 a DIFFERENT revision\n")
            ok, detail = verify.verify_blank("TEST_X", forms_root=td, manifest=_manifest(blank))
            self.assertFalse(ok)
            self.assertTrue("mismatch" in detail.lower() or "size" in detail.lower())

    def test_missing_file(self):
        with tempfile.TemporaryDirectory() as td:
            ok, detail = verify.verify_blank("TEST_X", forms_root=td, manifest=_manifest(b"x"))
            self.assertFalse(ok)
            self.assertIn("not present", detail)

    def test_strict_raises(self):
        with tempfile.TemporaryDirectory() as td:
            self._write(td, b"swapped")
            with self.assertRaises(verify.BlankRevisionError):
                verify.guard_blank("TEST_X", forms_root=td, mode="strict",
                                   manifest=_manifest(b"original"))

    def test_warn_does_not_raise(self):
        with tempfile.TemporaryDirectory() as td:
            self._write(td, b"swapped")
            with self.assertWarns(verify.BlankRevisionWarning):
                result = verify.guard_blank("TEST_X", forms_root=td, mode="warn",
                                            manifest=_manifest(b"original"))
            self.assertFalse(result)

    def test_off_skips(self):
        with tempfile.TemporaryDirectory() as td:
            self.assertTrue(verify.guard_blank("TEST_X", forms_root=td, mode="off",
                                               manifest=_manifest(b"original")))


class CheckUpstream(unittest.TestCase):
    def test_classifies(self):
        import maine_forms_engine.drift.check_upstream as cu
        upstream = b"%PDF-1.7 what the portal serves today\n"

        def fake_download(url, timeout, retries):
            if "gone" in url:
                raise OSError("404 Not Found")
            return upstream

        with mock.patch.object(cu, "_download", fake_download):
            self.assertEqual(cu.check_one(
                "A", {"sha256": _sha(upstream), "bytes": len(upstream), "url": "u"},
                1, 1)["status"], "ok")
            self.assertEqual(cu.check_one(
                "B", {"sha256": _sha(b"older"), "bytes": 5, "url": "u"},
                1, 1)["status"], "CHANGED")
            self.assertEqual(cu.check_one(
                "C", {"sha256": _sha(b"x"), "bytes": 1, "url": "gone"},
                1, 1)["status"], "GONE")

    def test_non_pdf_response_is_gone_not_changed(self):
        """The %PDF- guard: an HTML error page must NOT classify as CHANGED
        with adoptable hashes (the corp-repo bug this package eliminates)."""
        import maine_forms_engine.drift.check_upstream as cu
        html = b"<html><body>Service Unavailable</body></html>"
        with mock.patch.object(cu, "_download", lambda u, t, r: html):
            res = cu.check_one("D", {"sha256": _sha(b"x"), "bytes": 1, "url": "u"}, 1, 1)
        self.assertEqual(res["status"], "GONE")
        self.assertNotIn("got_sha256", res)


class FetchVerify(unittest.TestCase):
    def test_verify_guards(self):
        from maine_forms_engine.drift.fetch_pdfs import _verify
        pdf = b"%PDF-1.7 hello\n"
        entry = {"bytes": len(pdf), "sha256": _sha(pdf)}
        self.assertIsNone(_verify(pdf, entry))
        self.assertIn("not a PDF", _verify(b"<html>", entry))
        self.assertIn("size", _verify(pdf + b"x", entry))
        self.assertIn("sha256 mismatch",
                      _verify(pdf, {"bytes": len(pdf), "sha256": _sha(b"other")}))


class ManifestSpec(unittest.TestCase):
    def test_spec_example_matches_what_the_tools_read(self):
        spec = json.loads(
            (pathlib.Path(__file__).resolve().parent.parent / "specs" /
             "pdf_manifest.schema.json").read_text())
        example = spec["examples"][0]
        # the exact fields verify/check_upstream/fetch_pdfs consume
        for fid, e in example["forms"].items():
            self.assertTrue(e["url"].startswith("http"), fid)
            self.assertTrue(re.fullmatch(r"[0-9a-f]{64}", e["sha256"]), fid)
            self.assertGreater(e["bytes"], 0, fid)
        # the example must satisfy verify's own lookup path
        self.assertIsNotNone(verify.manifest_entry("AD-001", example))


if __name__ == "__main__":
    unittest.main()
