---
id: shared.secrets
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
# Shared Secrets and PII Rules

| ID | Severity | Rule |
| --- | --- | --- |
| `shared.secrets.no-hardcoded-secret` | `blocking` | Never commit credentials, tokens, passwords, or private keys. |
| `shared.secrets.no-pii-in-logs` | `blocking` | Logs must not include PII or credential material. |
| `shared.secrets.secure-token-storage` | `blocking` | Store tokens only in an approved secure storage mechanism. |
| `shared.secrets.https-only` | `blocking` | Send application traffic over HTTPS or stronger transport only. |
| `shared.secrets.encrypt-local-data` | `high` | Encrypt local data stores that contain user, tenant, or operationally sensitive data. |
