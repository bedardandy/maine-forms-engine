# The canonical fact object

Every mapping-driven fill in the consumer repos goes through one boundary: a
form's `mapping.json` maps each fillable widget to a **canonical fact-key**,
and the engine (`maine_forms_engine.fill.fill_via_mapping`, or a repo's MCP
`fill_form` tool) resolves those keys against a **canonical fact object** — a
plain nested JSON dict your host system (intake form, docassemble interview,
agent, ...) builds per matter.

```bash
python3 -m maine_forms_engine.fill.fill_via_mapping --form <ID> \
    --case case.json --out out/ --forms-root forms/
```

## Resolution semantics

(Implemented by `fill.fill_via_mapping._resolve_key`; behavior identical to
the donor repos.)

- A dotted key like `parties.plaintiff.full_name` or `entity.legal_name`
  walks the object: `case["parties"]["plaintiff"]["full_name"]`.
- `today()` is computed at fill time (mm/dd/yyyy); never supply it.
- Values may be strings or numbers; ISO dates (`2025-01-15`) are rendered the
  way the forms expect (`01/15/2025`).
- `first_name` / `middle_name` / `last_name` are derived from a role's
  `full_name` when not supplied explicitly, so `full_name` alone covers
  split-name boxes.
- Checkbox-mapped keys are checked only on an affirmative token
  (`yes`/`true`/`x`/`1`); any other value leaves the box unchecked.
- Unresolved keys are reported in the result (`unresolved`), not fabricated.

## The court/probate shape (`{matter, parties, party, facts}`)

The shape `maine-court-forms` and `maine-probate-forms` map against, and what
`fill.canonical.is_canonical` recognizes:

| key | shape |
|---|---|
| `matter` | `docket_number`, `case_number`, `case_type`, `court_type`, `court_county`, `court_location`, `filing_date`, `event_date` |
| `parties.<role>` | `full_name`, `first_name`, `middle_name`, `last_name`, `address`, `city`, `state`, `zip`, `phone`, `email`, `date_of_birth` — roles such as `plaintiff`, `defendant`, `attorney`, `child_1`... (what each role means per form is in that form's per-form guide) |
| `party` | the single filing party (same attributes, plus `signature`) |
| `facts.<snake_case>` | the form's labeled line items (amounts, elections, narratives) |
| `today()` | computed at fill time |

`fill.canonical.to_engine_case` bridges this object to the court repo's
internal engine case shape (top-level `court`/`docket_no`/...), so a canonical
fact object can also drive that repo's recipe-tier fills.

## Domain-role shapes (tax-native roles)

`transactional-tax-forms` extends the same resolution semantics with
domain roles instead of litigation roles; the dotted-key walk is identical:

| role | attributes seen in shipped mappings |
|---|---|
| `entity` | `legal_name`, `trade_name`, `ein`, `mailing_address`, `mailing_city`, `mailing_state`, `mailing_zip`, `street_address`, `county`, `state_of_formation`, `formation_date`, `phone` |
| `responsible_party` | `name`, `ssn_itin_ein`, `title` |
| `decedent` | `name`, `ssn`, `date_of_death`, `domicile_county`, `domicile_state`, `address` |
| `executor` / `fiduciary` | `name`, `address`, `ssn_or_ein`, `phone` |
| `estate` / `trust` | `name`, `ein`, `date_created` |
| `transferor` / `transferee` | `name`, `address`, `mailing_*`, `ssn_or_ein` |
| `property` | `address`, `town`, `county`, `map_block_lot`, `purchase_price`, `transfer_date`, `type` |
| `facts.<snake_case>` | the form's labeled line items |
| `signature`, `today()` | signature line; computed date |

Read each form's `mapping.json` for the exact keys it consumes; every mapped
form also ships an `examples/sample_case.json` that exercises all of them.

## Divergence to converge: maine-corporation-forms

The corp repo currently uses a flat `entity.*` / `clerk.*` / `filing.*` case
shape **and** an inverted mapping direction
(`{"fields": {canonical_key: {widget_id, confidence}}}` instead of
`{"map": {widget_id: canonical_key}}`). It is the odd one out; converging it
onto this spec is part of its migration step (it consumes only the drift and
MCP-scaffold modules until then).

## Minimal example (court-shaped)

```json
{
  "matter": {"court_county": "Cumberland", "docket_number": "FM-2025-001"},
  "parties": {
    "plaintiff": {"full_name": "Jane Q. Doe", "address": "1 Main St",
                   "city": "Portland", "state": "ME", "zip": "04101"},
    "defendant": {"full_name": "John R. Roe"}
  },
  "facts": {"number_of_children": "2"}
}
```
