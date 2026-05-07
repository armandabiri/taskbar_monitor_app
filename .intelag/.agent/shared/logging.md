---
id: shared.logging
genre: shared
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
doc_version: 2.0.0
---
# Shared Logging Rules

| ID | Severity | Rule |
| --- | --- | --- |
| `shared.logging.no-console-output` | `high` | Do not use direct console output in production code. Use the project logger. |
| `shared.logging.lazy-formatting` | `medium` | Prefer structured or lazy formatting so log work is avoided when the level is disabled. |
| `shared.logging.log-before-failure` | `high` | Log operational failures before returning or throwing typed failures. |
| `shared.logging.no-sensitive-values` | `blocking` | Do not log tokens, passwords, secrets, or PII. Use anonymized identifiers. |
