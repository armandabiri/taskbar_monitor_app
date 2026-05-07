---
id: conventions.frontmatter
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
# Frontmatter Convention

Every instruction-kit Markdown file starts with YAML frontmatter. Required fields:

| Field | Required | Notes |
| --- | --- | --- |
| `id` | yes | Stable dotted identifier derived from the path. |
| `genre` | yes | One of `convention`, `persona`, `playbook`, `prompt`, `routing`, `template`, `shared`, `archive`, `glossary`, `readme`. |
| `applies_to` | yes | List of stacks, roles, or `all`. |
| `load_mode` | yes | `always`, `task`, `reference`, or `archived`. |
| `status` | yes | `active`, `draft`, `deprecated`, or `archived`. |
| `schema_version` | yes | Current value is `2`. |
| `introduced_in` | yes | Kit CalVer release, such as `2026.04.0`. |
| `updated` | yes | ISO date of the last editorial change. |
| `owners` | yes | Owning team or role. |
| `supersedes` | yes | List of old paths or ids replaced by this file. |
| `doc_version` | yes | SemVer for the document content. |

Keep frontmatter minimal. Put operational rules in the body, not in metadata.
