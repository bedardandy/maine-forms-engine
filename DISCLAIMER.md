# Disclaimer

**Not legal advice.** This project is software — a shared engine that prepares
*draft* filled-in forms from structured data for the Maine forms libraries
that consume it. It does not provide legal or tax advice, it does not create
an attorney–client or any other professional relationship, and its output is
not a substitute for the advice of a licensed attorney or qualified
professional.

**Intended use — a professionally managed system only.** This library is
designed to be one automated component within a broader workflow that is
designed, implemented, supervised, and reviewed by a qualified, licensed
professional. It is **not** intended for unsupervised use, and it is not a
do-it-yourself substitute for professional representation. The professional
who operates it is solely and fully responsible for every form prepared,
filed, or relied upon.

**Drafts only — verify everything.** All output is a draft that may be
incomplete or incorrect. The field mappings this engine fills from are
produced in the consumer repos by automated and AI/LLM-assisted methods and
carry trust tiers (see each consumer form's `mapping.json` `status`) —
machine-reviewed is not attorney-reviewed. Official forms and the law change
over time; the drift guard in this package detects revised blanks but cannot
re-verify a mapping. Before any form is signed, filed, or relied upon, a
qualified professional must verify it against the **current official form**
and the **applicable law**.

**AI-assisted, experimental software.** This package was extracted and
assembled with substantial AI/LLM assistance and is experimental. Review it
as you would any third-party code before relying on it.

**No warranty.** This software and its output are provided "AS IS", without
warranty of any kind and without any liability, to the fullest extent
permitted by law (see the Apache-2.0 [`LICENSE`](LICENSE), sections 7–8). You
assume all risk and responsibility for any use of this software or its
output.

**Not affiliated** with, endorsed by, or sponsored by the Maine Judicial
Branch, the Maine Secretary of State, Maine Revenue Services, or the Internal
Revenue Service. Form names and numbers are used only to identify the public
forms they describe.
