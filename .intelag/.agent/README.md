---
id: readme
genre: readme
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
# Intelag Agent Instruction Kit

Reusable agent instructions for Intelag engineering workflows.

This repository is intended to be consumed as a Git submodule at `.intelag/.agent`. This kit is consumed at `.intelag/.agent`. Generated artifacts may be written under kit-local folders such as `requests/`, `reports/`, and `summary/` when the consumer workflow expects that layout.

Primary folders:

| Folder | Purpose |
| --- | --- |
| `conventions/` | Metadata, rule IDs, and writing conventions. |
| `shared/` | Cross-stack rules and reusable instructions. |
| `routing/` | Model and instruction selection guidance. |
| `personas/` | Role prompts and persona bundles. |
| `stacks/` | Stack-specific conventions, prompts, and playbooks. |
| `templates/` | Reusable request and planning templates. |
| `archive/` | Migration history and deprecated material. |
| `scripts/` | Validation and index-building utilities. |
