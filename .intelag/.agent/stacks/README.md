---
id: stacks.readme
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
doc_version: 1.0.0
---
# Stack Folder Taxonomy

Each stack folder uses the same buckets where applicable:

| Folder | Purpose |
| --- | --- |
| `conventions.md` | Baseline rules loaded for most tasks in that stack. |
| `guides/` | Topic guidance that is useful during implementation but is not a prompt or audit playbook. |
| `generators/` | Output-generation instructions such as README writers. |
| `prompts/` | Review or task prompt templates. |
| `playbooks/` | Deep audit workflows and long-form checklists. |
| `rules/` | Rule catalogues for stacks with many standalone rules, such as SQL. |
| `reviews/` | Review rubrics and assessment forms. |

Keep stack roots shallow. If a Markdown file is not `README.md` or `conventions.md`, put it in one of these buckets.
