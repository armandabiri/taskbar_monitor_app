---
id: archive.project-memory-move-map
genre: archive
applies_to:
  - archive
load_mode: archived
status: archived
schema_version: 2
introduced_in: 2026.04.0
updated: 2026-04-29
owners:
  - Intelag Engineering
supersedes:
  - plans/project_memory_move_map_2026-04-29.md
doc_version: 0.1.0
---
# Project Memory Move Map

Move these consumer-local folders out of the instruction-kit submodule during Phase 5 and restore them under the recreated local `.intelag/.agent` directory:

| Path | Destination | Notes |
| --- | --- | --- |
| `plans/` | `.intelag/.agent/plans/` | Includes `agent_instructions_improve.md` and Phase 1 reports. |
| `reports/` | `.intelag/.agent/reports/` | Project-local reports. |
| `issues/` | `.intelag/.agent/issues/` | Project-local issue notes. |
| `requests/` | `.intelag/.agent/requests/` | Project-local request memory. |
| `summary/` | `.intelag/.agent/summary/` | Create if needed for local handoff summaries. |
| `.intelag/.agent_local_migration_preflight/` | audit only | Temporary preflight backup outside the submodule. |
