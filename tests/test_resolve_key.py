"""Canonical fact-key resolution (ported from transactional-tax-forms
tests/test_engine_offline.py::ResolveKey), plus the canonical-shape adapter."""
import unittest

from maine_forms_engine.fill.canonical import is_canonical, to_engine_case
from maine_forms_engine.fill.fill_via_mapping import _resolve_key


class ResolveKey(unittest.TestCase):
    FACTS = {
        "matter": {"docket_number": "RE-2025-1"},
        "parties": {"plaintiff": {"full_name": "Jane Q. Doe"}},
        "entity": {"ein": "00-0000000"},
        "facts": {"date_of_death": "2025-01-15", "amount": 1000},
    }

    def test_dotted_walk(self):
        self.assertEqual(_resolve_key("matter.docket_number", self.FACTS),
                         "RE-2025-1")
        self.assertEqual(_resolve_key("entity.ein", self.FACTS), "00-0000000")

    def test_missing_key_is_none(self):
        self.assertIsNone(_resolve_key("entity.phone", self.FACTS))
        self.assertIsNone(_resolve_key("nobody.name", self.FACTS))

    def test_name_parts_derived_from_full_name(self):
        self.assertEqual(
            _resolve_key("parties.plaintiff.first_name", self.FACTS), "Jane")
        self.assertEqual(
            _resolve_key("parties.plaintiff.middle_name", self.FACTS), "Q.")
        self.assertEqual(
            _resolve_key("parties.plaintiff.last_name", self.FACTS), "Doe")

    def test_iso_dates_render_us_style(self):
        self.assertEqual(_resolve_key("facts.date_of_death", self.FACTS),
                         "01/15/2025")

    def test_numbers_stringified(self):
        self.assertEqual(_resolve_key("facts.amount", self.FACTS), "1000")

    def test_today_is_computed(self):
        import datetime
        self.assertEqual(_resolve_key("today()", {}),
                         datetime.date.today().strftime("%m/%d/%Y"))


class CanonicalAdapter(unittest.TestCase):
    def test_is_canonical(self):
        self.assertTrue(is_canonical({"matter": {}, "parties": {}}))
        self.assertFalse(is_canonical({"court": {}, "docket_no": "X"}))

    def test_to_engine_case_passthrough_for_engine_shape(self):
        case = {"court": {"name": "District"}, "docket_no": "X"}
        self.assertIs(to_engine_case(case), case)

    def test_to_engine_case_translates(self):
        out = to_engine_case({
            "matter": {"docket_number": "FM-1", "court_county": "Cumberland",
                       "filing_date": "2026-01-02"},
            "parties": {"plaintiff": {"full_name": "Jane Q. Doe",
                                      "date_of_birth": "1990-05-01"}},
        })
        self.assertEqual(out["docket_no"], "FM-1")
        self.assertEqual(out["court"]["county"], "Cumberland")
        self.assertEqual(out["parties"]["plaintiff"]["dob"], "1990-05-01")
        self.assertEqual(out["event_date"], "2026-01-02")


if __name__ == "__main__":
    unittest.main()
