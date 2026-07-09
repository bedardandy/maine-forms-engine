"""The package's ``__version__`` must match the installed distribution
metadata (which is built from pyproject's ``version``), so a check keyed on
``maine_forms_engine.__version__`` can never read a stale, hand-edited value.
"""
from importlib.metadata import version

import maine_forms_engine


def test_version_matches_installed_metadata():
    assert maine_forms_engine.__version__ == version("maine-forms-engine")
