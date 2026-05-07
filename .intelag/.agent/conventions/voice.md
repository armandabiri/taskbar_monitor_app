---
id: conventions.voice
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
# Voice Convention

Instruction files use direct, testable language.

Rules:

| ID | Severity | Rule |
| --- | --- | --- |
| `conventions.voice.atx-headings` | `medium` | Use ATX headings only. |
| `conventions.voice.no-decorative-markers` | `medium` | Avoid decorative emoji, horizontal-rule dividers, and chat transcript artifacts. |
| `conventions.voice.actionable-rules` | `high` | State the required behavior and verification path. |
| `conventions.voice.reference-by-id` | `high` | Use stable rule IDs for cross-references. |

Prefer short paragraphs and tables for rule sets. Keep examples close to the rule they demonstrate.
