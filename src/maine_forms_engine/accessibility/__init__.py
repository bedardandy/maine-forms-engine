"""PDF/UA remediation for filled AcroForms (donor: maine-court-forms
tools/accessibility/, whose accessibility_pipeline.py is byte-identical with
maine-probate-forms' and whose embed_widget_font.py carries the base-14
family generalization). remediate_form's /TU naming strategy is pluggable:
"caption" (court) / "schema-label" (probate) / any callable."""

from .remediate_form import remediate, schema_label_names  # noqa: F401
