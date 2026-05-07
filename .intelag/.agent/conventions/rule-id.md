---
id: conventions.rule-id
genre: convention
applies_to:
  - all
load_mode: reference
status: active
schema_version: 2
introduced_in: 2026.04.0
updated: 2026-04-29
owners:
  - Intelag Engineering
supersedes: []
doc_version: 1.0.1
---
# Rule ID Convention

Use stable dotted IDs for rules and references.

Format: `<scope>.<stack-or-domain>.<topic>.<rule>`.

Examples:

| Rule ID | Severity | Meaning |
| --- | --- | --- |
| `stacks.flutter.logging.no-print` | `high` | Avoid direct `print` calls in production Flutter code. |
| `stacks.python.config.env-driven` | `high` | Read environment-specific values from configuration. |
| `shared.secrets.no-hardcoded-secret` | `blocking` | Never commit credentials or tokens. |

Severity vocabulary:

| Severity | Gate behavior |
| --- | --- |
| `blocking` | Must be fixed before merge or release. |
| `high` | Should be fixed in the same change unless explicitly waived. |
| `medium` | Fix when touching the area or before milestone closure. |
| `low` | Advisory cleanup or readability improvement. |

Never reuse an ID for a changed semantic rule. Deprecate the old ID in `archive/legacy-rule-aliases.md` and create a new one.
