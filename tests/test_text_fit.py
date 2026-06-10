"""text_fit unit tests (ported from transactional-tax-forms
tests/test_engine_offline.py::TextFit — donor code is byte-identical)."""
import unittest

from maine_forms_engine.fill import text_fit


class TextFit(unittest.TestCase):
    def test_fit_name_collapses_middle_then_first(self):
        name = "Robert James Sterling"
        self.assertEqual(text_fit.fit_name(name, 18), "Robert J. Sterling")
        self.assertEqual(text_fit.fit_name(name, 14), "R. J. Sterling")

    def test_fit_name_never_truncates(self):
        # Two-part name: nothing safe to collapse -> returned unchanged.
        self.assertEqual(text_fit.fit_name("Jane Doe", 4), "Jane Doe")

    def test_fit_name_keeps_suffix(self):
        out = text_fit.fit_name("Robert James Sterling Jr.", 22)
        self.assertEqual(out, "Robert J. Sterling Jr.")

    def test_abbreviate_address(self):
        self.assertEqual(text_fit.abbreviate_address("100 Main Street, Suite 4"),
                         "100 Main St, Ste 4")

    def test_fit_generic_truncates_at_word_boundary(self):
        self.assertEqual(text_fit.fit("alpha beta gamma", 12), "alpha beta")

    def test_widget_char_budget(self):
        self.assertEqual(text_fit.widget_char_budget([0, 0, 100, 20]), 20)
        self.assertEqual(text_fit.widget_char_budget([0, 0, 0, 20]), 999)


if __name__ == "__main__":
    unittest.main()
