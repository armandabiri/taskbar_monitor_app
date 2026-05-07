---
id: stacks.sql.playbooks.performance
genre: playbook
applies_to:
  - sql
load_mode: reference
status: active
schema_version: 2
introduced_in: 2026.04.0
updated: 2026-04-29
owners:
  - Intelag Engineering
supersedes: []
size_exception: true
size_exception_reason: merged source exceeds 1200 lines and no clean topic split was available during 2026-04-29 migration
doc_version: 2.0.0
---
# SQL Performance Audit Playbook

## Canonical Source: sql-performance-audit-playbook.md

## Section Break
doc_version: 1.0.0
## Section Break
# SQL Table Design Audit Playbook and Report Template

## Executive summary

This playbook is a **performance-first** auditing system for SQL table design that enforces a strict hierarchy:

- **Performance**: query latency/throughput/IO cost first (indexes, query shape, partitioning, storage, maintenance, plan stability).
- **Modularity/readability**: schema clarity second (naming, constraints, integrity contracts, domain boundaries).
- **README/documentation**: updated last (only once behavior and performance are stable and regression-protected).

The “performance-first” posture is implemented as **gates**: you do not progress to broad schema refactors (renames, normalization work, large migration re-org) until you can demonstrate performance baselines and a rollback-safe path. This is consistent with the fact that planners depend on **up-to-date statistics** and that design choices like indexes and partitioning have direct measurable runtime outcomes.

This report includes:

- A **prioritized checklist** (P0/P1/P2) where each item contains: rationale, detection (SQL / EXPLAIN patterns / stats views / schema inspection), remediation (before/after SQL), risk/impact, and tests/CI checks.
- A **table/index audit template** to record findings per table/per index.
- A **report template** and a **scoring rubric** (performance/correctness/maintainability/risk).
- A **refactor/migration workflow** with effort levels (XS–XL), plus example CI YAML gates.
- Coverage for PostgreSQL (primary), with parallel notes for MySQL and SQLite where applicable.
- Mermaid diagrams: schema relationship diagram + Gantt timeline.

Two internal rubrics you provided already capture a tiered review discipline and strong defaults around keys, constraints, indexing and migration safety; this playbook builds on those tiered concepts and extends them with operational performance tooling and CI “gates.”

## Performance diagnostics, metrics, and profiling workflow

### Define measurable budgets before touching schema

**Why**: a schema “improvement” that increases p95 latency, lock time, or write amplification is a regression even if it looks cleaner.

Recommended minimum targets (adapt per workload):

- **Latency**: p50/p95/p99 for critical queries (top N by time and by frequency).
- **Planner accuracy**: estimate vs actual row counts should be directionally sane (avoid wildly wrong cardinality).
- **IO profile**: buffer hits vs reads; temp file spills; WAL generated (for write paths).
- **Concurrency**: lock waits and deadlocks; long “idle in transaction.”

PostgreSQL provides **EXPLAIN (ANALYZE)** to execute the statement and report actual runtime and actual rows per plan node, which is the fastest path to validate whether your changes actually improve execution.

### PostgreSQL profiling steps you can standardize

1. **Capture production-like query mix**
   - Enable **pg_stat_statements** to track planning/execution statistics for all SQL statements and identify the most expensive queries by total time, mean time, or call count.
2. **Enable targeted slow-query logging**
   - Use `log_min_duration_statement` to log statements exceeding a threshold; optionally sample with `log_min_duration_sample` and `log_statement_sample_rate` when traffic is too high to log everything.
3. **Profile individual hotspots with EXPLAIN options**
   - Use `EXPLAIN (ANALYZE, BUFFERS, WAL, SETTINGS, SUMMARY)` as needed:
     - `BUFFERS` reports cache hits/reads/dirties/writes and can include IO timing if enabled.
     - `WAL` reports WAL records/bytes; useful for write amplification analysis.
     - `SERIALIZE` measures output serialization cost (datatype output functions, TOAST fetch), which can dominate “fast” plans that return huge results.
     - `TIMING OFF` reduces overhead when you only need row counts and node structure.
4. **Check statistics freshness**
   - EXPLAIN depends on accurate stats; PostgreSQL explicitly notes that up-to-date `pg_statistic` data is required, usually maintained by autovacuum, but manual `ANALYZE` may be needed after big changes.
5. **Investigate lock/contention**
   - Use `pg_stat_activity` for current backend activity and `pg_locks` for held/waiting locks.
   - Enable `log_lock_waits` to log sessions waiting longer than `deadlock_timeout`, helping tie lock waits to specific statements.
6. **Prevent runaway queries and lock pileups**
   - Apply `statement_timeout`, `lock_timeout`, and `idle_in_transaction_session_timeout` (carefully) to bound worst-case behavior; PostgreSQL notes idle-in-transaction sessions can hold locks and can prevent vacuum from reclaiming dead tuples, contributing to bloat.
7. **Maintenance posture**
   - Routine vacuuming is central in PostgreSQL; autovacuum can also issue `ANALYZE` when tables change sufficiently.

### MySQL profiling equivalents to include in audits

- MySQL’s **slow query log** records statements exceeding `long_query_time` (and meeting other criteria). It is explicitly designed to find optimization candidates.
- MySQL provides **EXPLAIN ANALYZE** as an execution profiling tool that instruments and runs the query, reporting where time is spent.
- MySQL Performance Schema includes methodologies for diagnosing repeatable performance problems via instrumentation and post-filtering.

### SQLite profiling equivalents to include in audits

- SQLite’s **EXPLAIN QUERY PLAN** provides a high-level description of the strategy and, critically, how indexes are used.
- SQLite’s optimizer guidance explains the role of **ANALYZE** for gathering index selectivity statistics into `sqlite_stat*` tables.

## Performance-first prioritized checklist

**How to read this checklist**

- **P0** items are “stop-the-line” performance gates. Address these before schema “cleanups.”
- **P1** items are high-value performance improvements that may require more invasive changes.
- **P2** items are optimizations and long-term scaling work that are workload-dependent.

Each item includes: **Rationale**, **Detection**, **Remediation (before/after)**, **Risk/impact**, **Tests/CI checks**.

### P0: Instrumentation and baseline gates

**P0-1 Enable query visibility (pg_stat_statements or equivalent)**

- Rationale: You cannot optimize what you cannot measure; pg_stat_statements exists specifically to track planning/execution stats for all SQL statements.
- Detection: In PostgreSQL, check extension presence and query top statements by total time / mean time / calls from `pg_stat_statements`.
- Remediation:
  - Before: ad-hoc guessing based on anecdotes.
  - After:

    ```sql
    CREATE EXTENSION IF NOT EXISTS pg_stat_statements;
    ```

    (Then standard “top queries” dashboards/queries.)
- Risk/impact: high impact, low code risk; modest overhead and requires config enabling in many environments.
- Tests/CI checks: In staging CI, require pg_stat_statements enabled in performance test runs (skip for ephemeral unit tests).

**P0-2 Enable slow query logging with thresholds**

- Rationale: Logging queries over a threshold is a direct way to surface regressions and missing indexes. PostgreSQL logs statement durations above `log_min_duration_statement`.
- Detection: Confirm DB config; verify log lines include durations and statement text correlation via `log_line_prefix`.
- Remediation:
  - Before: no slow query visibility.
  - After:

    ```conf
    log_min_duration_statement = '250ms'
    log_line_prefix = '%m [%p] %q%u@%d/%a '
    ```

- Risk/impact: medium overhead (logging volume), high diagnostic value; must handle sensitive data risk in logs.
- Tests/CI checks: CI gate can parse logs during load tests and fail if new slow queries exceed SLO (environment dependent).

**P0-3 Require EXPLAIN ANALYZE evidence for changes impacting hot queries**

- Rationale: PostgreSQL EXPLAIN with `ANALYZE` executes and reports actual runtimes and actual rows, letting you validate estimate accuracy and real costs.
- Detection: PR requirement—include EXPLAIN output (prefer JSON output format for machine parsing). EXPLAIN supports JSON/YAML/XML output formats.
- Remediation:
  - Require commands like:

    ```sql
    EXPLAIN (ANALYZE, BUFFERS, WAL, FORMAT JSON)
    SELECT ...
    ```

- Risk/impact: low risk, high payoff; costs time to run on representative data.
- Tests/CI checks: Plan-shape tests (see CI section) on synthetic/staging data.

### P0: Indexing fundamentals

**P0-4 Index every frequent WHERE/JOIN/ORDER BY path; delete/update constraints need child indexes**

- Rationale: PostgreSQL explicitly notes foreign keys require indexes on referenced columns and it’s often a good idea to index referencing columns too, because deletes/updates on the referenced table require scanning the referencing table.
- Detection:
  - Query catalog for missing indexes on FK columns.
  - Identify top queries from pg_stat_statements and validate their predicates match index leading columns.
- Remediation:
  - Before:

    ```sql
    ALTER TABLE order_items
      ADD CONSTRAINT fk_order_items_order
      FOREIGN KEY (order_id) REFERENCES orders(order_id);
    -- No index on order_items(order_id)
    ```

  - After:

    ```sql
    CREATE INDEX CONCURRENTLY idx_order_items_order_id
      ON order_items(order_id);
    ```

    `CREATE INDEX CONCURRENTLY` is supported to build without locking out writes, but takes longer and requires two scans and transaction waits.
- Risk/impact: high impact; low risk if built concurrently (but cannot be used inside a transaction block). For very large tables, still operationally heavy.
- Tests/CI checks: Integration tests for FK behavior + query performance tests for join path.

**P0-5 Choose index type by operator semantics, not habit (PostgreSQL)**

- Rationale: PostgreSQL provides multiple index types (B-tree, GiST, GIN, BRIN, etc.), each suited to different clauses and data types.
- Detection:
  - For JSONB containment/search: prefer GIN operator classes; confirm operators are indexable.
  - For full text search: GIN or GiST recommended.
  - For huge append-like tables with correlated columns: consider BRIN.
- Remediation examples:
  - JSONB containment:

    ```sql
    CREATE INDEX idx_events_payload_gin
      ON events USING gin (payload jsonb_path_ops);
    ```

    PostgreSQL docs note `jsonb_path_ops` can offer better performance for supported operators.
  - BRIN for correlated time:

    ```sql
    CREATE INDEX idx_events_created_at_brin
      ON events USING brin (created_at);
    ```

    BRIN is designed for very large tables with correlation to physical location.
- Risk/impact: high performance upside; risk is “wrong index type” wasting write cost without plan adoption.
- Tests/CI checks: Query performance tests that validate plan uses the intended index (EXPLAIN checks).

**P0-6 Composite indexes: enforce “equality-first, then range/sort” and avoid redundant prefixes**

- Rationale: PostgreSQL supports multi-column indexes; index usability depends on predicate patterns and column order.
- Detection:
  - From pg_stat_statements, list top queries, extract common predicates.
  - EXPLAIN confirms whether index conditions are applied or you still see Seq Scan / Filter without Index Cond.
- Remediation:
  - Before (two indexes, still slow sort):

    ```sql
    CREATE INDEX idx_orders_org ON orders(org_id);
    CREATE INDEX idx_orders_created ON orders(created_at);
    ```

  - After (one composite matches query):

    ```sql
    CREATE INDEX CONCURRENTLY idx_orders_org_status_created
      ON orders(org_id, status, created_at DESC);
    ```

- Risk/impact: high; but can increase write amplification and bloat if overused.
- Tests/CI checks: EXPLAIN plan assertions; regression benchmarks for list endpoints.

**P0-7 Covering indexes and index-only scans (PostgreSQL INCLUDE)**

- Rationale: PostgreSQL index-only scans can avoid heap access; INCLUDE lets you store payload columns for covering indexes. Index-only scans depend on visibility map bits and are best when heap pages are “all-visible.”
- Detection:
  - EXPLAIN shows `Index Only Scan`.
  - If you expected index-only but see heap fetches, check whether table is frequently updated (visibility bits not set) and vacuum/analyze posture.
- Remediation:
  - Before:

    ```sql
    SELECT display_name FROM users WHERE email = $1;
    -- Index only on email, heap fetch required to get display_name.
    ```

  - After:

    ```sql
    CREATE INDEX CONCURRENTLY idx_users_email_cover
      ON users(email) INCLUDE (display_name);
    ```

    INCLUDE is the intended mechanism for covering indexes; docs caution against bloating indexes with wide payload columns.
- Risk/impact: high read-path improvement; risk is index bloat, slower writes, and reduced benefit if rows change often.
- Tests/CI checks: perf tests for the hot query; verify plan remains stable with representative stats.

**P0-8 Partial indexes and expression indexes for “non-sargable” predicates**

- Rationale:
  - Partial indexes index only rows matching a predicate, reducing size and maintenance work.
  - Expression indexes let you index computed expressions such as `lower(email)`.
- Detection:
  - Queries with low-selectivity flags (`is_active`, `deleted_at`) that still scan large sets.
  - Predicates applying functions to columns (e.g., `WHERE lower(email)=...`) without matching expression index.
- Remediation:
  - Soft-delete partial index (PostgreSQL):

    ```sql
    CREATE INDEX CONCURRENTLY idx_items_live_org_created
      ON items(org_id, created_at DESC)
      WHERE deleted_at IS NULL;
    ```

  - Case-insensitive email:

    ```sql
    CREATE INDEX CONCURRENTLY idx_users_lower_email
      ON users (lower(email));
    ```

- Risk/impact: Often huge improvements; risk is predicate mismatch (query must match the expression/predicate).
- Tests/CI checks: Query plan tests ensure predicates use index; regression for soft-delete correctness.

### P0: Query plan stability, parameterization, prepared statements

**P0-9 Validate generic vs custom plans for prepared statements (PostgreSQL)**

- Rationale: PostgreSQL prepared statements can execute with a generic plan (reused) or custom plan (parameter-specific). Generic plans save planning overhead but can be inefficient if the best plan depends heavily on parameter values.
- Detection:
  - Compare `EXPLAIN (GENERIC_PLAN)` vs `EXPLAIN ANALYZE EXECUTE ...` for representative parameter values. EXPLAIN supports `GENERIC_PLAN`.
- Remediation:
  - Before: parameterized query performs inconsistently across values due to plan mismatch.
  - After: restructure query or indexes to make plan robust across parameter ranges; only consider hinting tools in exceptional cases (see P2).
- Risk/impact: high in multi-tenant or skewed distributions; risk is overfitting plan to one parameter set.
- Tests/CI checks: parameter-sweep benchmarks (small and large tenants) + plan shape snapshots.

**P0-10 Use EXPLAIN SERIALIZE to catch “serialization dominates” problems (PostgreSQL)**

- Rationale: PostgreSQL EXPLAIN’s `SERIALIZE` option measures output conversion cost; it can be significant when output functions are expensive or TOAST values must be fetched.
- Detection:
  - EXPLAIN shows big serialization time relative to execution.
  - Symptoms: “fast plan but slow API response,” especially with huge result sets.
- Remediation:
  - Before: `SELECT *` returning wide TOAST columns.
  - After: project only needed columns, paginate, avoid pulling TOAST blobs by default.
- Risk/impact: high; low schema risk; often large application impact.
- Tests/CI checks: response-size tests; query performance tests with realistic projections.

### P0: Concurrency, locking, and timeouts

**P0-11 Lock monitoring and lock-wait logging**

- Rationale: Lock contention can dominate p95/p99 even when individual queries are fast. PostgreSQL provides `pg_locks` and `pg_stat_activity`.
- Detection:
  - Audit runbook includes standard “who is blocking whom” query joining pg_locks + pg_stat_activity.
  - Enable `log_lock_waits` to log lock waits longer than `deadlock_timeout`.
- Remediation:
  - Reduce lock scope (avoid long transactions).
  - Add indexes that reduce scanned rows for locking reads (critical in InnoDB too; InnoDB locks index ranges scanned).
- Risk/impact: high; missteps can cause outages if schema changes increase lock times.
- Tests/CI checks: concurrency tests; deadlock retry logic tests at app layer.

**P0-12 Apply statement/lock/idle-in-tx timeouts safely**

- Rationale: PostgreSQL provides `statement_timeout`, `lock_timeout`, and `idle_in_transaction_session_timeout` to bound runaway behavior; idle-in-transaction can hold locks and block vacuum, contributing to bloat.
- Detection:
  - Identify “idle in transaction” sessions and long-running statements.
  - Review timeouts per role/session; avoid global defaults that break poolers.
- Remediation (per-transaction safety):

  ```sql
  BEGIN;
  SET LOCAL statement_timeout = '2s';
  SET LOCAL lock_timeout = '200ms';
  -- critical statements
  COMMIT;
  ```

- Risk/impact: high safety benefit; risk is false timeouts without idempotent retry strategy.
- Tests/CI checks: integration tests that ensure proper retry/rollback on timeout errors.

### P0: Bulk insert/upsert and write amplification

**P0-13 Bulk load strategy: load first, index later (PostgreSQL)**

- Rationale: PostgreSQL docs state the fastest method for loading a fresh table is: create table → bulk load data using COPY → create indexes after; creating an index on existing data is quicker than updating it incrementally per row load.
- Detection:
  - ETL migrations that do row-by-row inserts and maintain indexes/triggers during load.
- Remediation:
  - Before: repeated INSERTs into indexed table.
  - After:

    ```sql
    CREATE TABLE stage_events (...);
    COPY stage_events FROM STDIN (FORMAT csv);
    CREATE INDEX CONCURRENTLY idx_stage_events_created ON stage_events(created_at);
    ```

- Risk/impact: high performance improvement; operational risk in production pipelines if COPY sources/permissions are mishandled.
- Tests/CI checks: load-time benchmarks; post-load constraint validation.

**P0-14 Correct upsert semantics (PostgreSQL, MySQL, SQLite)**

- Rationale:
  - PostgreSQL `INSERT ... ON CONFLICT DO UPDATE` guarantees atomic insert-or-update outcome, even under high concurrency.
  - MySQL `INSERT ... ON DUPLICATE KEY UPDATE` updates existing rows on unique/PK conflict.
  - SQLite UPSERT uses an `ON CONFLICT` clause and is modeled after PostgreSQL’s syntax.
- Detection:
  - Anti-pattern: read-then-insert pattern (`SELECT` then `INSERT`) under concurrency.
- Remediation:
  - PostgreSQL:

    ```sql
    INSERT INTO accounts(account_id, balance)
    VALUES ($1, $2)
    ON CONFLICT (account_id)
    DO UPDATE SET balance = EXCLUDED.balance;
    ```

  - MySQL:

    ```sql
    INSERT INTO accounts(account_id, balance)
    VALUES (?, ?)
    ON DUPLICATE KEY UPDATE balance = VALUES(balance);
    ```

  - SQLite:

    ```sql
    INSERT INTO accounts(account_id, balance)
    VALUES (?, ?)
    ON CONFLICT(account_id) DO UPDATE SET balance=excluded.balance;
    ```

- Risk/impact: Very high correctness and performance benefit; risk is accidental overwrite and lost-update if you don’t encode business logic in the update clause.
- Tests/CI checks: concurrency tests for idempotency; invariant tests for conflict behavior.

### P1: Partitioning and large-table posture

**P1-1 Partition only when pruning is real and retention operations matter**

- Rationale: PostgreSQL docs note partitioning benefits usually pay off when table would otherwise be very large; pruning must be enabled (e.g., `enable_partition_pruning`).
- Detection:
  - Table size exceeds memory; queries filter by time or key.
  - EXPLAIN shows partition pruning and reduced scanned partitions; if not, partitioning might be harming performance.
- Remediation (range partition example):

  ```sql
  CREATE TABLE events (
    event_id bigserial PRIMARY KEY,
    created_at timestamptz NOT NULL,
    payload jsonb NOT NULL
  ) PARTITION BY RANGE (created_at);
  ```

  PostgreSQL supports range/list/hash partitioning.
- Risk/impact: Potentially huge; risk is operational complexity and constraints limitations.

**P1-2 Unique and primary key constraints on partitioned tables**

- Rationale: PostgreSQL documents a key limitation: for unique or primary key constraints on partitioned tables, the constraint’s columns must include all partition key columns (and partition keys must not be expressions) so uniqueness cannot be violated across partitions.
- Detection:
  - Attempted `PRIMARY KEY (id)` on range-partitioned-by-date table without including date.
- Remediation:
  - Before:

    ```sql
    -- Partition by created_at, but PK lacks created_at
    CREATE TABLE invoice (
      invoice_id uuid PRIMARY KEY,
      created_at timestamptz NOT NULL
    ) PARTITION BY RANGE (created_at);
    ```

  - After (example pattern):

    ```sql
    CREATE TABLE invoice (
      invoice_id uuid NOT NULL,
      created_at timestamptz NOT NULL,
      PRIMARY KEY (created_at, invoice_id)
    ) PARTITION BY RANGE (created_at);
    ```

- Risk/impact: high correctness impact; may require application-level key handling changes.
- Tests/CI checks: uniqueness tests across partitions; partition routing tests.

**P1-3 Online partition maintenance (detach/attach and lock costs)**

- Rationale: PostgreSQL partitioning docs describe `DETACH PARTITION` and `DETACH PARTITION CONCURRENTLY`, and note index creation limitations and lock behaviors.
- Detection:
  - Backfills or retention deletes taking `ACCESS EXCLUSIVE` locks on parent.
- Remediation: prefer detach/drop for retention, attach staged tables with pre-created matching CHECK constraints to avoid scans where possible.
- Risk/impact: high operational impact; mistakes can block writes.
- Tests/CI checks: migration rehearsal on staging with lock monitoring.

### P1: Storage, TOAST, and table parameters (PostgreSQL)

**P1-4 Tune fillfactor for update-heavy tables**

- Rationale: PostgreSQL `fillfactor` reserves free space on pages to make UPDATEs more likely to stay on the same page (more efficient, more HOT updates).
- Detection:
  - Update-heavy table with page splits and high bloat; index maintenance overhead.
- Remediation:
  - Before: default fillfactor=100 on hot-update table.
  - After:

    ```sql
    ALTER TABLE orders SET (fillfactor = 80);
    ```

- Risk/impact: Can reduce bloat and improve write latency; risk is increased initial storage footprint.
- Tests/CI checks: write-path benchmarks; bloat metrics monitoring.

**P1-5 Understand TOAST storage and compression choices**

- Rationale:
  - PostgreSQL TOAST strategies determine whether large values are compressed and/or stored out-of-line.
  - PostgreSQL supports column `COMPRESSION` methods `pglz` and `lz4` (when built with lz4).
  - Storage mode choices (EXTERNAL vs EXTENDED) can trade storage for faster substring operations.
- Detection:
  - EXPLAIN `SERIALIZE` shows time dominated by TOAST fetch/serialization.
- Remediation (example patterns):
  - Prefer `jsonb` over `json` for repeated processing: `json` stores text and reparses each time, while `jsonb` stores a decomposed binary format and is faster to process.
  - For large text columns used in substring operations, consider `STORAGE EXTERNAL` (posture depends on workload).
  - Set compression:

    ```sql
    ALTER TABLE docs
      ALTER COLUMN body
      SET COMPRESSION lz4;
    ```

- Risk/impact: medium-to-high; risk of unexpected storage/CPU tradeoffs and migration time.
- Tests/CI checks: query perf tests on text/json operations; storage usage monitoring.

### P2: Index maintenance, bloat, and hinting tools

**P2-1 Manage bloat and rebuild safely**

- Rationale: PostgreSQL provides REINDEX to rebuild indexes, and VACUUM behavior includes index cleanup decisions.
- Detection:
  - Rising index size; VACUUM not keeping up; performance degradation.
- Remediation:
  - `REINDEX` for targeted rebuilds.
  - Use pg_repack to remove bloat online with minimal locking; it is positioned as an alternative to VACUUM FULL in some environments.
- Risk/impact: Operational risk; schedule carefully; require runbooks.
- Tests/CI checks: maintenance rehearsal in staging; post-maintenance benchmarks.

**P2-2 Use planner hints only with explicit governance**

- Rationale: PostgreSQL core planner is typically improved through statistics/schema changes; hinting extensions like pg_hint_plan exist to influence plan decisions via comments.
- Detection: repeated plan instability after addressing stats/indexes/query structure.
- Remediation:
  - Document which hints are used and why; ensure monitoring for plan regressions.
- Risk/impact: high maintenance burden; hints can become wrong as data distributions change.
- Tests/CI checks: plan snapshot tests on representative datasets; alerts on plan drift.

## Modularity and readability checklist

This checklist is **explicitly subordinate** to performance gates above. It aims to make schemas self-explanatory and safe—without introducing performance regressions.

**M0-1 Naming consistency and discoverability**

- Rationale: Consistent naming reduces ambiguity and supports automation and tooling. This is a core Tier A rule in your internal rubric.
- Detection: lint schema for mixed singular/plural, mixed casing, unclear abbreviations.
- Remediation:
  - Before: `OrgDeptRole`, `dept_role`, `org_deptRoles`
  - After: consistent `snake_case` and stable naming convention.
- Risk/impact: Renames can be high-risk migrations; only do with explicit compatibility strategies.
- Tests: migration tests; backward compatibility tests (views, dual-write).

**M0-2 Primary keys, timestamps, and NULL policy**

- Rationale: Your rubric requires strong defaults: a single primary key, `created_at/updated_at`, and NOT NULL by default unless NULL has explicit meaning.
- Detection: schema scan for missing PKs, nullable columns without documented meaning, missing audit fields.
- Remediation: add PK and audit columns; add NOT NULL + defaults (handled with online migration safety; see workflow).
- Risk/impact: medium-to-high in existing tables; changes can rewrite tables depending on engine/version.
- Tests: integrity tests; application contract tests.

**M0-3 Foreign keys and deliberate ON DELETE/ON UPDATE**

- Rationale: PostgreSQL constraints docs emphasize FK semantics and indexing considerations; your rubric prohibits “soft” FKs.
- Detection: orphan checks, missing FK constraints, ambiguous cascade behavior.
- Remediation:
  - Before:

    ```sql
    user_id uuid -- “FK” but no constraint
    ```

  - After:

    ```sql
    ALTER TABLE orders
      ADD CONSTRAINT fk_orders_user
      FOREIGN KEY (user_id) REFERENCES users(user_id)
      ON DELETE RESTRICT;
    ```

- Risk/impact: high correctness improvement; risk is unexpected cascade restrictions in legacy data.
- Tests: FK enforcement tests; cascade behavior tests; query-perf tests for deletes/joins (and add FK indexes).

**M0-4 CHECK constraints and domain validation**

- Rationale: Your internal rules recommend CHECK constraints for domain validation and status columns.
- Detection: invalid values in production; status stored as free-form text.
- Remediation:
  - Before: `status text`
  - After:

    ```sql
    ALTER TABLE jobs
      ADD CONSTRAINT chk_jobs_status
      CHECK (status IN ('queued','running','done','failed'));
    ```

- Risk/impact: medium; backfilling invalid values required.
- Tests: constraint tests; migration pre-checks.

**M0-5 ENUM vs lookup table decision**

- Rationale:
  - PostgreSQL ENUM types are ordered sets of values.
  - MySQL ENUM is a string chosen from explicit permitted values.
  - SQLite supports structural enforcement via foreign keys and STRICT tables, but does not have a native enum type in the same sense.
- Detection: statuses that change frequently; multi-tenant domains with evolving state machines.
- Remediation guidance:
  - Prefer lookup tables when you need:
    - additional metadata per value,
    - frequent value lifecycle changes,
    - cross-engine portability.
  - Prefer ENUM for stable, truly static sets (days of week, small closed sets).
- Risk/impact: ENUM migrations can be engine/version tricky; lookup tables increase join cost (but can be optimized).
- Tests: migration tests; referential integrity tests; query perf tests on filters.

**M0-6 SQL linting and formatting**

- Rationale: SQLFluff is an established linter/formatter supporting multiple dialects and intended to catch errors and bad SQL before it hits your database.
- Detection: inconsistent formatting, ambiguous joins, use of `SELECT *`, missing schema qualification conventions.
- Remediation: enforce SQLFluff in CI (see CI section).
- Risk/impact: low runtime risk; medium merge churn in large repos.
- Tests: CI lint gate.

## Audit templates, report template, and scoring rubric

### Table-level audit template

Use this as a spreadsheet or markdown table; record **one row per table**, updated per audit cycle.

| Table | Domain/schema | Purpose (1 line) | Size (rows/pages/GB) | Hot queries (top 3–5) | blocking predicates & joins | Current indexes summary | EXPLAIN evidence links | Autovacuum / stats posture | Partitioned? key & pruning | Lock/concurrency risks | P0 findings | P1 findings | Risk (change cost) | Effort (XS–XL) | Owner | Status |
| ----- | ------------- | ---------------- | -------------------: | --------------------- | --------------------------- | ----------------------- | ---------------------- | -------------------------- | -------------------------- | ---------------------- | ----------- | ----------- | ------------------ | -------------- | ----- | ------ |

**Minimum required evidence per hot table**:

- `pg_stat_statements` excerpt or slow-log excerpt.
- `EXPLAIN (ANALYZE, BUFFERS)` (and `WAL` if write-heavy).
- Stats freshness: last analyze/vacuum, and whether autovacuum is enabled at table level.

### Index-level audit template

Record **one row per index** (including PK/unique indexes).

| Index name | Table | Type (btree/gin/gist/brin/…) | Columns / expression | Predicate (partial?) | INCLUDE columns | Supports index-only? | Intended query pattern | Actual plan usage | Write cost risk | Maintenance/bloat notes | Action (keep/drop/modify) | Migration safety plan |
| ---------- | ----- | ---------------------------- | -------------------- | -------------------- | --------------- | -------------------- | ---------------------- | ----------------- | --------------- | ----------------------- | ------------------------- | --------------------- |

Key reminders:

- PostgreSQL index-only scan requirements, visibility map dependence, and INCLUDE design tradeoffs are explicitly documented.
- GIN does not support index-only scans (by design).
- BRIN is designed for very large tables where columns correlate with physical location.

### Report template for an audit cycle

Use this structure to publish an audit report (per domain/schema, quarterly, or per major release):

**Scope and environment**

- DB engine/version, instance sizes, dataset size, sampling window for stats.
- Extensions enabled (e.g., pg_stat_statements).

**Workload summary**

- Top queries by total time and by calls.
- Slow query log summary (thresholds used).

**Findings summary**

- P0 blockers (must-fix)
- P1 improvements
- P2 long-term work

**Per-table findings**

- Table audit table excerpt
- EXPLAIN evidence
- Index proposals and tradeoffs

**Migration plan**

- Online steps, lock strategy, rollback plan
- Verification steps and CI gates

**Decision log**

- Any risk acceptance (explicit owner + reason + date)

Your internal “Tier A–D” domain rubric already provides a structured sign-off block and decision matrix; incorporate it here as the governance layer around approvals and risk acceptance.

### Scoring rubric and grading

A practical scoring rubric must reflect the hierarchy (performance dominates), while still capturing correctness/integrity and maintainability risk.

**Per-table scoring dimensions (0–5 each)**

- **Performance (weight 45%)**: plan quality, index alignment, pruning, IO/WAL footprint, tail latency risk.
- **Correctness/integrity (weight 30%)**: PK/FK/unique/check constraints, null policy, data type correctness.
- **Maintainability (weight 15%)**: naming clarity, domain boundaries, constraint naming, migration readability.
- **Change risk (weight 10%)**: migration downtime risk, backfill complexity, lock risk.

```mermaid
pie title Table Score Weights
  "Performance" : 45
  "Correctness/Integrity" : 30
  "Maintainability" : 15
  "Change Risk" : 10
```

**Grade bands**

- **A (4.5–5.0)**: performance + integrity solid; safe to scale.
- **B (3.5–4.4)**: acceptable; schedule P1 work.
- **C (2.5–3.4)**: performance or integrity issues likely; remediation required before scale.
- **D (<2.5)**: high outage or corruption risk; block major releases until fixed.

This scoring coexists with your tiered pass/fail rubric: Tier A integrity fundamentals should be non-negotiable, and Tier C performance should be required for hot-path tables.

## Refactor and migration workflow, CI gates, and sample SQL fixes

### Step-by-step workflow with effort estimates (XS–XL)

Effort definitions:

- **XS**: < 0.5 day
- **S**: 0.5–1 day
- **M**: 1–3 days
- **L**: 3–5 days
- **XL**: 1–2+ weeks

Workflow:

1. **Baseline capture (S)**
   - Enable pg_stat_statements, collect top queries; enable slow query logging threshold.

2. **Hot query EXPLAIN pack (S–M)**
   - Produce EXPLAIN (ANALYZE, BUFFERS, WAL, FORMAT JSON) for top queries and attach to audit.

3. **P0 index alignment fixes (M–L)**
   - Add missing FK indexes; correct composite index ordering; add partial/expression indexes. Use concurrency-safe creation where possible.

4. **P0 operational guardrails (S)**
   - Apply statement/lock/idle timeouts in a safe, role- or transaction-scoped way.

5. **P1 partitioning or materialized views (L–XL)**
   - Only after evidence shows pruning/retention wins; follow documented partition constraints and index limitations. Consider materialized views for computed summaries.

6. **P2 bloat remediation (S–M)**
   - For heavy bloat, consider pg_repack or REINDEX; validate operational constraints.

7. **Modularity/readability refactors (M)**
   - Naming, constraint naming, documentation comments, domain schema alignment.

8. **README/documentation update (S)**
   - Document only once schema and performance are stable and CI-gated.

### Sample SQL fixes for common issues

**Fix: make an un-indexable predicate indexable (expression index)**

- Before:

  ```sql
  SELECT * FROM users WHERE lower(email) = lower($1);
  ```

- After:

  ```sql
  CREATE INDEX CONCURRENTLY idx_users_lower_email
    ON users (lower(email));

  SELECT user_id, display_name
  FROM users
  WHERE lower(email) = lower($1);
  ```

Expression indexes are explicitly supported.

**Fix: enable index-only scans via INCLUDE**

- Before:

  ```sql
  SELECT display_name FROM users WHERE email = $1;
  ```

- After:

  ```sql
  CREATE INDEX CONCURRENTLY idx_users_email_cover
    ON users(email) INCLUDE (display_name);
  ```

Index-only scans and INCLUDE tradeoffs are explicitly documented.

**Fix: boolean/low-cardinality indexing via partial index**

- Before:

  ```sql
  CREATE INDEX idx_items_is_active ON items(is_active);
  ```

- After:

  ```sql
  CREATE INDEX CONCURRENTLY idx_items_active_org_created
    ON items(org_id, created_at DESC)
    WHERE is_active = true;
  ```

Partial indexes are built over subsets defined by predicates.

**Fix: bulk load properly in PostgreSQL**

- Before:

  ```sql
  INSERT INTO events(...) VALUES (...); -- repeated millions of times
  ```

- After:

  ```sql
  COPY events FROM STDIN (FORMAT csv);
  -- then build indexes after load
  ```

PostgreSQL docs explicitly recommend COPY then indexes for fastest fresh load.

**Fix: safe upsert**

- PostgreSQL atomic UPSERT:

  ```sql
  INSERT INTO inventory(sku, qty)
  VALUES ($1, $2)
  ON CONFLICT (sku) DO UPDATE SET qty = EXCLUDED.qty;
  ```

PostgreSQL guarantees atomic insert-or-update for ON CONFLICT DO UPDATE.

**Fix: partitioning with retention**

- After (range partitions by month):

  ```sql
  CREATE TABLE measurement (
    city_id int NOT NULL,
    logdate date NOT NULL,
    peaktemp int,
    unitsales int
  ) PARTITION BY RANGE (logdate);

  CREATE TABLE measurement_2026_03 PARTITION OF measurement
    FOR VALUES FROM ('2026-03-01') TO ('2026-04-01');
  ```

PostgreSQL partitioning methods and examples are documented, including retention via dropping/detaching partitions.

### CI gates and sample YAML snippets

**Gate categories**

- **SQL lint/format**: SQLFluff with dialect configuration for Postgres/MySQL/SQLite.
- **Schema correctness tests**: pgTAP for PostgreSQL schema/unit assertions.
- **Migration rehearsal**: apply migrations to ephemeral DB; run smoke queries; run EXPLAIN checks.
- **Performance budgets**: pgbench scripts for representative OLTP transactions; pgbench is included as a PostgreSQL benchmark tool.

Example GitHub Actions job (PostgreSQL-focused, adaptable):

```yaml
name: db-ci

on:
  pull_request:
  push:
    branches: [ main ]

jobs:
  postgres-audit:
    runs-on: ubuntu-latest
    services:
      postgres:
        image: postgres:17
        env:
          POSTGRES_PASSWORD: postgres
        ports:
          - 5432:5432
        options: >-
          --health-cmd "pg_isready -U postgres"
          --health-interval 10s
          --health-timeout 5s
          --health-retries 10

    steps:
      - uses: actions/checkout@v4

      - name: Install tools
        run: |
          pip install sqlfluff
          sudo apt-get update
          sudo apt-get install -y postgresql-client

      - name: SQLFluff lint
        run: |
          sqlfluff lint --dialect postgres sql/

      - name: Apply migrations
        env:
          DATABASE_URL: postgresql://postgres:postgres@localhost:5432/postgres
        run: |
          # Replace with Flyway/Liquibase/Alembic/etc
          ./scripts/apply_migrations.sh

      - name: Run schema assertions (pgTAP)
        run: |
          # Example: psql -f test/sql/pgtap_tests.sql
          psql postgresql://postgres:postgres@localhost:5432/postgres -f test/sql/pgtap_tests.sql

      - name: EXPLAIN plan gate (no Seq Scan on hot query)
        run: |
          psql postgresql://postgres:postgres@localhost:5432/postgres <<'SQL'
          EXPLAIN (FORMAT JSON)
          SELECT /* hot query */ 1;
          SQL

      - name: pgbench performance smoke
        run: |
          pgbench -i postgresql://postgres:postgres@localhost:5432/postgres
          pgbench -T 30 -c 10 postgresql://postgres:postgres@localhost:5432/postgres
```

Notes:

- pgbench runs a repeated SQL sequence and reports average transaction rate; you can replace the default script with your own transaction scripts for domain-specific workloads.
- For MySQL, equivalent gates can parse the slow query log and use Performance Schema methodologies; MySQL explicitly states the slow query log surfaces candidate queries and that Performance Schema can be used for repeatable bottleneck analysis.
- For SQLite, CI can run `EXPLAIN QUERY PLAN` checks to ensure index usage in critical queries.

## Component relationships and migration timeline diagrams

### Mermaid schema relationship diagram

```mermaid
erDiagram
  USERS ||--o{ ORDERS : places
  ORDERS ||--o{ ORDER_ITEMS : contains
  PRODUCTS ||--o{ ORDER_ITEMS : referenced_by
  ORGS ||--o{ USERS : owns

  USERS {
    uuid user_id PK
    uuid org_id FK
    text email
    timestamptz created_at
  }
  ORDERS {
    uuid order_id PK
    uuid user_id FK
    uuid org_id FK
    text status
    timestamptz created_at
  }
```

### Mermaid Gantt chart for a performance-first audit/refactor

```mermaid
gantt
  title SQL Table Design Performance-First Audit and Migration
  dateFormat  YYYY-MM-DD
  axisFormat  %b %d

  section Baseline
  Enable stats + slow logging                 :a1, 2026-03-31, 2d
  Capture top queries (pg_stat_statements)    :a2, after a1, 2d
  EXPLAIN pack for hotspots                   :a3, after a2, 3d

  section P0 Fixes
  Missing FK + composite indexes              :b1, after a3, 5d
  Partial/expression/covering indexes         :b2, after b1, 5d
  Timeout + lock guardrails                   :b3, after b1, 2d

  section P1 Scaling
  Partitioning or materialized views          :c1, after b2, 10d
  Bulk-load + upsert hardening                :c2, after b2, 4d

  section Quality Gates
  SQL lint + pgTAP + EXPLAIN gates            :d1, after b2, 4d
  pgbench budget baseline                     :d2, after d1, 3d

  section Documentation
  README + data catalog updates               :e1, after d2, 2d
```

This staged approach aligns with PostgreSQL’s explicit tooling expectations: you profile with EXPLAIN options and use maintenance/statistics pathways (autovacuum/ANALYZE) before making large structural moves like partitioning.


## Merged Source: sql-performance-audit-playbook_v1.md

## Section Break
doc_version: 1.0.0
## Section Break
# SQL Table Design Audit Playbook and Report Template

## Executive summary

This playbook is a **performance-first** auditing system for SQL table design that enforces a strict hierarchy:

- **Performance**: query latency/throughput/IO cost first (indexes, query shape, partitioning, storage, maintenance, plan stability).
- **Modularity/readability**: schema clarity second (naming, constraints, integrity contracts, domain boundaries).
- **README/documentation**: updated last (only once behavior and performance are stable and regression-protected).

The “performance-first” posture is implemented as **gates**: you do not progress to broad schema refactors (renames, normalization work, large migration re-org) until you can demonstrate performance baselines and a rollback-safe path. This is consistent with the fact that planners depend on **up-to-date statistics** and that design choices like indexes and partitioning have direct measurable runtime outcomes.

This report includes:

- A **prioritized checklist** (P0/P1/P2) where each item contains: rationale, detection (SQL / EXPLAIN patterns / stats views / schema inspection), remediation (before/after SQL), risk/impact, and tests/CI checks.
- A **table/index audit template** to record findings per table/per index.
- A **report template** and a **scoring rubric** (performance/correctness/maintainability/risk).
- A **refactor/migration workflow** with effort levels (XS–XL), plus example CI YAML gates.
- Coverage for PostgreSQL (primary), with parallel notes for MySQL and SQLite where applicable.
- Mermaid diagrams: schema relationship diagram + Gantt timeline.

Two internal rubrics you provided already capture a tiered review discipline and strong defaults around keys, constraints, indexing and migration safety; this playbook builds on those tiered concepts and extends them with operational performance tooling and CI “gates.”

## Performance diagnostics, metrics, and profiling workflow

### Define measurable budgets before touching schema

**Why**: a schema “improvement” that increases p95 latency, lock time, or write amplification is a regression even if it looks cleaner.

Recommended minimum targets (adapt per workload):

- **Latency**: p50/p95/p99 for critical queries (top N by time and by frequency).
- **Planner accuracy**: estimate vs actual row counts should be directionally sane (avoid wildly wrong cardinality).
- **IO profile**: buffer hits vs reads; temp file spills; WAL generated (for write paths).
- **Concurrency**: lock waits and deadlocks; long “idle in transaction.”

PostgreSQL provides **EXPLAIN (ANALYZE)** to execute the statement and report actual runtime and actual rows per plan node, which is the fastest path to validate whether your changes actually improve execution.

### PostgreSQL profiling steps you can standardize

1. **Capture production-like query mix**
   - Enable **pg_stat_statements** to track planning/execution statistics for all SQL statements and identify the most expensive queries by total time, mean time, or call count.
2. **Enable targeted slow-query logging**
   - Use `log_min_duration_statement` to log statements exceeding a threshold; optionally sample with `log_min_duration_sample` and `log_statement_sample_rate` when traffic is too high to log everything.
3. **Profile individual hotspots with EXPLAIN options**
   - Use `EXPLAIN (ANALYZE, BUFFERS, WAL, SETTINGS, SUMMARY)` as needed:
     - `BUFFERS` reports cache hits/reads/dirties/writes and can include IO timing if enabled.
     - `WAL` reports WAL records/bytes; useful for write amplification analysis.
     - `SERIALIZE` measures output serialization cost (datatype output functions, TOAST fetch), which can dominate “fast” plans that return huge results.
     - `TIMING OFF` reduces overhead when you only need row counts and node structure.
4. **Check statistics freshness**
   - EXPLAIN depends on accurate stats; PostgreSQL explicitly notes that up-to-date `pg_statistic` data is required, usually maintained by autovacuum, but manual `ANALYZE` may be needed after big changes.
5. **Investigate lock/contention**
   - Use `pg_stat_activity` for current backend activity and `pg_locks` for held/waiting locks.
   - Enable `log_lock_waits` to log sessions waiting longer than `deadlock_timeout`, helping tie lock waits to specific statements.
6. **Prevent runaway queries and lock pileups**
   - Apply `statement_timeout`, `lock_timeout`, and `idle_in_transaction_session_timeout` (carefully) to bound worst-case behavior; PostgreSQL notes idle-in-transaction sessions can hold locks and can prevent vacuum from reclaiming dead tuples, contributing to bloat.
7. **Maintenance posture**
   - Routine vacuuming is central in PostgreSQL; autovacuum can also issue `ANALYZE` when tables change sufficiently.
8. **Capture slow-query plans automatically with `auto_explain`**
   - `pg_stat_statements` summarizes; `auto_explain` captures the actual plan for any query exceeding a threshold. Enable in `postgresql.conf`:

     ```conf
     shared_preload_libraries = 'pg_stat_statements,auto_explain'
     auto_explain.log_min_duration = '500ms'
     auto_explain.log_analyze = on        # adds runtime; enable selectively
     auto_explain.log_buffers = on
     auto_explain.log_wal = on
     auto_explain.log_format = json
     ```

   - Use sampling (`auto_explain.sample_rate`) on high-traffic systems.
9. **Use `pg_stat_io` (PG16+) for authoritative I/O attribution**
   - `pg_stat_io` reports reads/writes/extends per backend type and per I/O context (normal, vacuum, bulkread, bulkwrite). It supersedes the older `pg_statio_*` views for identifying which workload is generating I/O pressure.

     ```sql
     SELECT backend_type, context, reads, writes, extends, hits
     FROM pg_stat_io
     WHERE reads + writes > 0
     ORDER BY reads + writes DESC;
     ```

10. **Use wait-event analysis for contention diagnosis**
    - `pg_stat_activity.wait_event_type` and `wait_event` categorize what each backend is waiting on (`Lock`, `LWLock`, `IO`, `Client`, etc.). Sample once per second for a minute to get a flame-graph-style view of where time is going.

### MySQL profiling equivalents to include in audits

- MySQL’s **slow query log** records statements exceeding `long_query_time` (and meeting other criteria). It is explicitly designed to find optimization candidates.
- MySQL provides **EXPLAIN ANALYZE** as an execution profiling tool that instruments and runs the query, reporting where time is spent.
- MySQL Performance Schema includes methodologies for diagnosing repeatable performance problems via instrumentation and post-filtering.

### SQLite profiling equivalents to include in audits

- SQLite’s **EXPLAIN QUERY PLAN** provides a high-level description of the strategy and, critically, how indexes are used.
- SQLite’s optimizer guidance explains the role of **ANALYZE** for gathering index selectivity statistics into `sqlite_stat*` tables.

## Performance-first prioritized checklist

**How to read this checklist**

- **P0** items are “stop-the-line” performance gates. Address these before schema “cleanups.”
- **P1** items are high-value performance improvements that may require more invasive changes.
- **P2** items are optimizations and long-term scaling work that are workload-dependent.

Each item includes: **Rationale**, **Detection**, **Remediation (before/after)**, **Risk/impact**, **Tests/CI checks**.

### P0: Instrumentation and baseline gates

**P0-1 Enable query visibility (pg_stat_statements or equivalent)**

- Rationale: You cannot optimize what you cannot measure; `pg_stat_statements` tracks planning/execution stats for all SQL statements.
- Detection: Check extension presence and query top statements by `total_exec_time` / `mean_exec_time` / `calls` from `pg_stat_statements`.
- Remediation:
  - Before: ad-hoc guessing based on anecdotes.
  - After (load library, install extension, configure):

    ```conf
    # postgresql.conf
    shared_preload_libraries = 'pg_stat_statements'
    pg_stat_statements.track = all          # capture nested statements (functions, triggers)
    pg_stat_statements.max = 10000          # raise from default 5000 for diverse workloads
    pg_stat_statements.save = on            # persist across restarts
    ```

    ```sql
    CREATE EXTENSION IF NOT EXISTS pg_stat_statements;
    ```

  - Define a reset cadence (e.g., weekly via `SELECT pg_stat_statements_reset()`) so trend windows are bounded.
- Risk/impact: high impact, low code risk; modest CPU overhead.
- Tests/CI checks: Require `pg_stat_statements` enabled in performance test runs; assert presence at deploy time.

**P0-2 Enable slow query logging with thresholds**

- Rationale: Logging queries over a threshold is a direct way to surface regressions and missing indexes. PostgreSQL logs statement durations above `log_min_duration_statement`.
- Detection: Confirm DB config; verify log lines include durations and statement text correlation via `log_line_prefix`.
- Remediation:
  - Before: no slow query visibility.
  - After:

    ```conf
    log_min_duration_statement = '250ms'
    log_line_prefix = '%m [%p] %q%u@%d/%a '
    ```

- Risk/impact: medium overhead (logging volume), high diagnostic value; must handle sensitive data risk in logs.
- Tests/CI checks: CI gate can parse logs during load tests and fail if new slow queries exceed SLO (environment dependent).

**P0-3 Require EXPLAIN ANALYZE evidence for changes impacting hot queries**

- Rationale: PostgreSQL EXPLAIN with `ANALYZE` executes the statement and reports actual runtimes and rows, letting you validate estimate accuracy and real costs.
- Detection: PR requirement—include EXPLAIN output (prefer JSON output format for machine parsing). EXPLAIN supports JSON/YAML/XML output formats. PG17 adds the `MEMORY` option to report planner memory usage.
- Remediation:
  - For SELECTs:

    ```sql
    EXPLAIN (ANALYZE, BUFFERS, WAL, FORMAT JSON)
    SELECT ...
    ```

  - **For DML (UPDATE/DELETE/INSERT) — wrap in a transaction to avoid mutating data:**

    ```sql
    BEGIN;
    EXPLAIN (ANALYZE, BUFFERS, WAL)
    UPDATE orders SET status = 'shipped' WHERE id = $1;
    ROLLBACK;
    ```

- Risk/impact: low risk if DML is wrapped; high payoff. `ANALYZE` adds non-trivial timing overhead — use `TIMING OFF` if only row counts/structure are needed.
- Tests/CI checks: Plan-shape tests (see CI section) on synthetic/staging data.

### P0: Indexing fundamentals

**P0-4 Index every frequent WHERE/JOIN/ORDER BY path; delete/update constraints need child indexes**

- Rationale: PostgreSQL explicitly notes foreign keys require indexes on referenced columns and it’s often a good idea to index referencing columns too, because deletes/updates on the referenced table require scanning the referencing table.
- Detection:
  - Query catalog for missing indexes on FK columns.
  - Identify top queries from pg_stat_statements and validate their predicates match index leading columns.
- Remediation:
  - Before:

    ```sql
    ALTER TABLE order_items
      ADD CONSTRAINT fk_order_items_order
      FOREIGN KEY (order_id) REFERENCES orders(order_id);
    -- No index on order_items(order_id)
    ```

  - After:

    ```sql
    CREATE INDEX CONCURRENTLY idx_order_items_order_id
      ON order_items(order_id);
    ```

    `CREATE INDEX CONCURRENTLY` is supported to build without locking out writes, but takes longer and requires two scans and transaction waits.
- Risk/impact: high impact; low risk if built concurrently (but cannot be used inside a transaction block). For very large tables, still operationally heavy.
- Tests/CI checks: Integration tests for FK behavior + query performance tests for join path.

**P0-5 Choose index type by operator semantics, not habit (PostgreSQL)**

- Rationale: PostgreSQL provides multiple index types (B-tree, GiST, GIN, BRIN, etc.), each suited to different clauses and data types.
- Detection:
  - For JSONB containment/search: prefer GIN operator classes; confirm operators are indexable.
  - For full text search: GIN or GiST recommended.
  - For huge append-like tables with correlated columns: consider BRIN.
- Remediation examples:
  - JSONB containment:

    ```sql
    CREATE INDEX idx_events_payload_gin
      ON events USING gin (payload jsonb_path_ops);
    ```

    PostgreSQL docs note `jsonb_path_ops` can offer better performance for supported operators.
  - BRIN for correlated time:

    ```sql
    CREATE INDEX idx_events_created_at_brin
      ON events USING brin (created_at);
    ```

    BRIN is designed for very large tables with correlation to physical location.
- Risk/impact: high performance upside; risk is “wrong index type” wasting write cost without plan adoption.
- Tests/CI checks: Query performance tests that validate plan uses the intended index (EXPLAIN checks).

**P0-6 Composite indexes: enforce “equality-first, then range/sort” and avoid redundant prefixes**

- Rationale: PostgreSQL supports multi-column indexes; index usability depends on predicate patterns and column order.
- Detection:
  - From pg_stat_statements, list top queries, extract common predicates.
  - EXPLAIN confirms whether index conditions are applied or you still see Seq Scan / Filter without Index Cond.
- Remediation:
  - Before (two indexes, still slow sort):

    ```sql
    CREATE INDEX idx_orders_org ON orders(org_id);
    CREATE INDEX idx_orders_created ON orders(created_at);
    ```

  - After (one composite matches query):

    ```sql
    CREATE INDEX CONCURRENTLY idx_orders_org_status_created
      ON orders(org_id, status, created_at DESC);
    ```

- Risk/impact: high; but can increase write amplification and bloat if overused.
- Tests/CI checks: EXPLAIN plan assertions; regression benchmarks for list endpoints.

**P0-7 Covering indexes and index-only scans (PostgreSQL INCLUDE)**

- Rationale: PostgreSQL index-only scans can avoid heap access; INCLUDE lets you store payload columns for covering indexes. Index-only scans depend on visibility map bits and are best when heap pages are “all-visible.”
- Detection:
  - EXPLAIN shows `Index Only Scan`.
  - If you expected index-only but see heap fetches, check whether table is frequently updated (visibility bits not set) and vacuum/analyze posture.
- Remediation:
  - Before:

    ```sql
    SELECT display_name FROM users WHERE email = $1;
    -- Index only on email, heap fetch required to get display_name.
    ```

  - After:

    ```sql
    CREATE INDEX CONCURRENTLY idx_users_email_cover
      ON users(email) INCLUDE (display_name);
    ```

    INCLUDE is the intended mechanism for covering indexes; docs caution against bloating indexes with wide payload columns.
- Risk/impact: high read-path improvement; risk is index bloat, slower writes, and reduced benefit if rows change often.
- Tests/CI checks: perf tests for the hot query; verify plan remains stable with representative stats.

**P0-8 Partial indexes and expression indexes for “non-sargable” predicates**

- Rationale:
  - Partial indexes index only rows matching a predicate, reducing size and maintenance work.
  - Expression indexes let you index computed expressions such as `lower(email)`.
- Detection:
  - Queries with low-selectivity flags (`is_active`, `deleted_at`) that still scan large sets.
  - Predicates applying functions to columns (e.g., `WHERE lower(email)=...`) without matching expression index.
- Remediation:
  - Soft-delete partial index (PostgreSQL):

    ```sql
    CREATE INDEX CONCURRENTLY idx_items_live_org_created
      ON items(org_id, created_at DESC)
      WHERE deleted_at IS NULL;
    ```

  - Case-insensitive email:

    ```sql
    CREATE INDEX CONCURRENTLY idx_users_lower_email
      ON users (lower(email));
    ```

- Risk/impact: Often huge improvements; risk is predicate mismatch (query must match the expression/predicate).
- Tests/CI checks: Query plan tests ensure predicates use index; regression for soft-delete correctness.

### P0: Query plan stability, parameterization, prepared statements

**P0-9 Validate generic vs custom plans for prepared statements (PostgreSQL)**

- Rationale: PostgreSQL prepared statements can execute with a generic plan (reused) or custom plan (parameter-specific). The planner uses a custom plan for the **first 5 executions**, then switches to a generic plan if its estimated cost is competitive — this is the cause of "the 6th call got slow" surprises in multi-tenant or skewed-data workloads. Force one or the other via `plan_cache_mode = { force_custom_plan | force_generic_plan }` per session/role when a workload has known skew.
- Detection:
  - Compare `EXPLAIN (GENERIC_PLAN)` vs `EXPLAIN ANALYZE EXECUTE ...` for representative parameter values (small and large tenants).
  - Check `pg_prepared_statements` and instrument the application to detect plan-shape changes after the 5th execution.
- Remediation:
  - Before: parameterized query performs inconsistently across values due to plan mismatch.
  - After: restructure query or indexes to make the plan robust across parameter ranges; pin via `SET plan_cache_mode` per role for known-skewed workloads; only consider hinting tools in exceptional cases (see P2).
- Risk/impact: high in multi-tenant or skewed distributions; pinning a custom plan removes planner adaptivity.
- Tests/CI checks: parameter-sweep benchmarks (small and large tenants) + plan shape snapshots.

**P0-10 Use EXPLAIN SERIALIZE to catch “serialization dominates” problems (PostgreSQL)**

- Rationale: PostgreSQL EXPLAIN’s `SERIALIZE` option measures output conversion cost; it can be significant when output functions are expensive or TOAST values must be fetched.
- Detection:
  - EXPLAIN shows big serialization time relative to execution.
  - Symptoms: “fast plan but slow API response,” especially with huge result sets.
- Remediation:
  - Before: `SELECT *` returning wide TOAST columns.
  - After: project only needed columns, paginate, avoid pulling TOAST blobs by default.
- Risk/impact: high; low schema risk; often large application impact.
- Tests/CI checks: response-size tests; query performance tests with realistic projections.

### P0: Concurrency, locking, and timeouts

**P0-11 Lock monitoring and lock-wait logging**

- Rationale: Lock contention can dominate p95/p99 even when individual queries are fast. PostgreSQL provides `pg_locks` and `pg_stat_activity`.
- Detection:
  - Audit runbook includes standard “who is blocking whom” query joining pg_locks + pg_stat_activity.
  - Enable `log_lock_waits` to log lock waits longer than `deadlock_timeout`.
- Remediation:
  - Reduce lock scope (avoid long transactions).
  - Add indexes that reduce scanned rows for locking reads (critical in InnoDB too; InnoDB locks index ranges scanned).
- Risk/impact: high; missteps can cause outages if schema changes increase lock times.
- Tests/CI checks: concurrency tests; deadlock retry logic tests at app layer.

**P0-12 Apply statement/lock/idle-in-tx timeouts safely**

- Rationale: PostgreSQL provides `statement_timeout`, `lock_timeout`, and `idle_in_transaction_session_timeout` to bound runaway behavior; idle-in-transaction can hold locks and block vacuum, contributing to bloat.
- Detection:
  - Identify “idle in transaction” sessions and long-running statements.
  - Review timeouts per role/session; avoid global defaults that break poolers.
- Remediation (per-transaction safety):

  ```sql
  BEGIN;
  SET LOCAL statement_timeout = '2s';
  SET LOCAL lock_timeout = '200ms';
  -- critical statements
  COMMIT;
  ```

- Risk/impact: high safety benefit; risk is false timeouts without idempotent retry strategy.
- Tests/CI checks: integration tests that ensure proper retry/rollback on timeout errors.

### P0: Bulk insert/upsert and write amplification

**P0-13 Bulk load strategy: load first, index later (PostgreSQL)**

- Rationale: PostgreSQL docs state the fastest method for loading a fresh table is: create table → bulk load via `COPY` → create indexes after; building an index on existing data is faster than incrementally maintaining it per row. PG17 adds `COPY ... ON_ERROR ignore` and `LOG_VERBOSITY` for resilient bulk loads.
- Detection:
  - ETL migrations that do row-by-row inserts and maintain indexes/triggers during load.
- Remediation:
  - Before: repeated INSERTs into indexed table.
  - After:

    ```sql
    CREATE TABLE stage_events (...);
    COPY stage_events FROM STDIN (FORMAT csv);
    CREATE INDEX CONCURRENTLY idx_stage_events_created ON stage_events(created_at);
    ```

  - PG17 resilient load:

    ```sql
    COPY stage_events FROM STDIN (FORMAT csv, ON_ERROR ignore, LOG_VERBOSITY verbose);
    ```

- Risk/impact: high performance improvement; operational risk in production pipelines if COPY sources/permissions are mishandled.
- Tests/CI checks: load-time benchmarks; post-load constraint validation.

**P0-14 Correct upsert semantics (PostgreSQL, MySQL, SQLite)**

- Rationale:
  - PostgreSQL `INSERT ... ON CONFLICT DO UPDATE` guarantees atomic insert-or-update outcome, even under high concurrency.
  - MySQL `INSERT ... ON DUPLICATE KEY UPDATE` updates existing rows on unique/PK conflict.
  - SQLite UPSERT uses an `ON CONFLICT` clause and is modeled after PostgreSQL’s syntax.
- Detection:
  - Anti-pattern: read-then-insert pattern (`SELECT` then `INSERT`) under concurrency.
- Remediation:
  - PostgreSQL:

    ```sql
    INSERT INTO accounts(account_id, balance)
    VALUES ($1, $2)
    ON CONFLICT (account_id)
    DO UPDATE SET balance = EXCLUDED.balance;
    ```

  - MySQL:

    ```sql
    INSERT INTO accounts(account_id, balance)
    VALUES (?, ?)
    ON DUPLICATE KEY UPDATE balance = VALUES(balance);
    ```

  - SQLite:

    ```sql
    INSERT INTO accounts(account_id, balance)
    VALUES (?, ?)
    ON CONFLICT(account_id) DO UPDATE SET balance=excluded.balance;
    ```

- Risk/impact: Very high correctness and performance benefit; risk is accidental overwrite and lost-update if you don’t encode business logic in the update clause.
- Tests/CI checks: concurrency tests for idempotency; invariant tests for conflict behavior.

### P0: Migration safety patterns

These rules bound the worst-case impact of every schema change. They map to [GR §6.5–§6.10].

**P0-15 Run every migration with bounded locks**

- Rationale: A blocked `ALTER TABLE` holds `ACCESS EXCLUSIVE` and queues every subsequent query against the table. `lock_timeout` bounds the blast radius; without it, a single migration can cascade into a full outage.
- Detection: review migration scripts for missing `SET lock_timeout` / `SET statement_timeout`; alert when a migration session runs longer than expected.
- Remediation:

  ```sql
  BEGIN;
  SET LOCAL lock_timeout = '5s';
  SET LOCAL statement_timeout = '15min';
  ALTER TABLE orders ADD COLUMN new_col text;
  COMMIT;
  ```

  - Migration harness must catch SQLSTATE `55P03` (`lock_not_available`) and retry with exponential backoff.
- Risk/impact: Very high safety benefit; minimal operational cost.
- Tests/CI checks: lint migration files for `lock_timeout` / `statement_timeout`; integration test that runs a migration against a held-lock fixture and confirms graceful failure.

**P0-16 Use `NOT VALID` then `VALIDATE CONSTRAINT` for FK and CHECK additions on large tables**

- Rationale: `ADD CONSTRAINT` validates synchronously under `ACCESS EXCLUSIVE` for the duration of a full table scan. The two-phase form takes a brief lock to add the constraint, then validates under `SHARE UPDATE EXCLUSIVE` (allows reads and writes).
- Remediation:

  ```sql
  ALTER TABLE orders
    ADD CONSTRAINT fk_orders_user
    FOREIGN KEY (user_id) REFERENCES users(id) NOT VALID;

  ALTER TABLE orders VALIDATE CONSTRAINT fk_orders_user;
  ```

- Risk/impact: Eliminates one of the most common migration outages.
- Tests/CI checks: lint migration files \u2014 reject any `ADD CONSTRAINT FOREIGN KEY` or `ADD CONSTRAINT CHECK` that lacks `NOT VALID` on tables above a configured size threshold.

**P0-17 Add `NOT NULL` via a `NOT VALID` CHECK first (PG12+)**

- Rationale: `ALTER COLUMN ... SET NOT NULL` requires a full table scan under `ACCESS EXCLUSIVE` unless a matching `CHECK (col IS NOT NULL) NOT VALID` has already been validated. With it, `SET NOT NULL` becomes metadata-only.
- Remediation:

  ```sql
  ALTER TABLE t ADD CONSTRAINT t_col_nn CHECK (col IS NOT NULL) NOT VALID;
  ALTER TABLE t VALIDATE CONSTRAINT t_col_nn;
  ALTER TABLE t ALTER COLUMN col SET NOT NULL;
  ALTER TABLE t DROP CONSTRAINT t_col_nn;
  ```

- Risk/impact: Turns a long-blocking migration into three fast operations.
- Tests/CI checks: migration linter rejects bare `SET NOT NULL` on tables above the configured size threshold.

**P0-18 Expand \u2192 migrate \u2192 contract for renames and type changes**

- Rationale: In-place `RENAME COLUMN` requires a synchronous app deploy; in-place `ALTER COLUMN ... TYPE` usually rewrites the table. The expand-migrate-contract pattern lets you ship the change without a coordinated downtime window.
- Remediation steps:
  1. **Expand:** add the new column / new table.
  2. **Backfill:** populate the new column in keyset-paged batches (P0-19) with dual-write from the application.
  3. **Migrate reads:** cut application reads to the new column behind a feature flag.
  4. **Verify:** confirm zero traffic on the old column via `pg_stat_user_tables` / app metrics.
  5. **Contract:** drop the old column.
- Risk/impact: Eliminates the entire class of \"locked migration\" outages.
- Tests/CI checks: integration test verifying both columns are present and consistent during the dual-write window.

**P0-19 Backfill in keyset-paged batches with commit between batches**

- Rationale: A single `UPDATE` over millions of rows holds row locks for the entire transaction, generates massive WAL, balloons the dead-tuple count, and blocks vacuum. Batching with intermediate commits releases locks and lets autovacuum keep up.
- Remediation pattern (PL/pgSQL or app code):

  ```sql
  DO $$
  DECLARE
    last_id bigint := 0;
    batch_size int := 5000;
    rows_updated int;
  BEGIN
    LOOP
      WITH next_batch AS (
        SELECT id FROM orders
        WHERE id > last_id AND new_col IS NULL
        ORDER BY id
        LIMIT batch_size
        FOR UPDATE SKIP LOCKED
      )
      UPDATE orders o
        SET new_col = compute_new_col(o.*)
      FROM next_batch nb
      WHERE o.id = nb.id
      RETURNING o.id INTO last_id;
      GET DIAGNOSTICS rows_updated = ROW_COUNT;
      EXIT WHEN rows_updated = 0;
      COMMIT;
      PERFORM pg_sleep(0.05); -- back-pressure for replicas
    END LOOP;
  END$$;
  ```

- Risk/impact: Bounded transaction size; replicas stay caught up; vacuum runs in parallel.
- Tests/CI checks: integration test on a multi-million-row fixture confirming no transaction exceeds N rows.

### P0: Concurrency and isolation contracts

**P0-20 Choose isolation per workload and document the retry contract**

- Rationale: PostgreSQL's default isolation is `READ COMMITTED`. `REPEATABLE READ` provides snapshot isolation across a multi-statement read; `SERIALIZABLE` (SSI) provides true serializability but **requires the application to retry on SQLSTATE `40001` (`serialization_failure`)**. Higher isolation without retry surfaces as user-visible 500s.
- Detection: review code paths that set `SET TRANSACTION ISOLATION LEVEL`; confirm a corresponding application-side retry exists.
- Remediation: per-transaction isolation only, with documented retry; never set globally.

  ```sql
  BEGIN ISOLATION LEVEL SERIALIZABLE;
  -- multi-statement work
  COMMIT;
  -- application catches 40001 and retries up to N times with backoff
  ```

- Tests/CI checks: a CI test that forces a write-write conflict and asserts the application retries.

**P0-21 Use `FOR UPDATE SKIP LOCKED` for queue consumers; use advisory locks for app-level mutexes**

- Rationale: `SELECT ... FOR UPDATE SKIP LOCKED` is the canonical PostgreSQL queue pattern \u2014 multiple consumers can pull non-overlapping work without blocking each other. `pg_advisory_xact_lock(key)` provides a transaction-scoped mutex without taking a heavyweight `LOCK TABLE`.
- Remediation:

  ```sql
  -- Queue consumer
  WITH job AS (
    SELECT id FROM job_queue
    WHERE status = 'pending'
    ORDER BY created_at
    FOR UPDATE SKIP LOCKED
    LIMIT 1
  )
  UPDATE job_queue SET status = 'running'
  FROM job WHERE job_queue.id = job.id
  RETURNING job_queue.*;

  -- Singleton job (one runner per logical key)
  SELECT pg_advisory_xact_lock(hashtext('nightly-rollup'));
  -- ... do work; lock auto-releases at COMMIT/ROLLBACK
  ```

- Tests/CI checks: concurrent consumer test verifying no double-processing; advisory-lock test verifying mutual exclusion.

### P0: Standard PostgreSQL extension baseline

**P0-22 Standardize on a known set of extensions and audit drift**

| Extension | When to enable | Severity |
|---|---|---|
| `pg_stat_statements` | Always (P0-1) | **P0** |
| `auto_explain` | Always \u2014 captures EXPLAIN of slow queries automatically | **P0** |
| `pgaudit` | Regulated environments (HIPAA, SOC 2, PCI, etc.) | **P0** |
| `pg_partman` | Tables with rolling time-based partitions and retention | **P1** |
| `pg_repack` | Tables exhibiting bloat that VACUUM cannot reclaim (P2-1) | **P2** |
| `pgvector` | Any embedding / similarity search workload | per project |
| `postgis` | Geospatial workload | per project |
| `pg_cron` | In-DB scheduled jobs (single-writer instances only) | per project |
| `pg_uuidv7` (or app-side) | UUIDv7 generation until PG18 ships native support | per project |

- Detection: `SELECT extname, extversion FROM pg_extension;` audited against the project's expected baseline.
- Remediation: codify the baseline in your migration tool; alert on drift.
- Risk/impact: Missing `pg_stat_statements` or `auto_explain` blinds operations; missing `pgaudit` in regulated environments creates compliance exposure.

### P1: Partitioning and large-table posture

**P1-1 Partition only when pruning is real and retention operations matter**

- Rationale: PostgreSQL partitioning benefits typically materialize when the table would otherwise exceed `shared_buffers` and queries filter by, or retention deletes target, the partition key. `enable_partition_pruning` is **on by default since PG11** — verify it, don't enable it.
- Detection:
  - Table size exceeds `shared_buffers`; queries filter by time or key.
  - EXPLAIN shows partition pruning and reduced scanned partitions; if not, partitioning is harming performance.
- Remediation (range partition example, with PK including partition key per P1-2):

  ```sql
  CREATE TABLE events (
    event_id bigint GENERATED ALWAYS AS IDENTITY,
    created_at timestamptz NOT NULL,
    payload jsonb NOT NULL,
    PRIMARY KEY (created_at, event_id)
  ) PARTITION BY RANGE (created_at);
  ```

  PostgreSQL supports range / list / hash partitioning. Use `pg_partman` for automated future-partition creation and retention drops.
- Risk/impact: Potentially huge; operational complexity and partitioning constraint limitations are the main risks.

**P1-2 Unique and primary key constraints on partitioned tables**

- Rationale: PostgreSQL documents a key limitation: for unique or primary key constraints on partitioned tables, the constraint’s columns must include all partition key columns (and partition keys must not be expressions) so uniqueness cannot be violated across partitions.
- Detection:
  - Attempted `PRIMARY KEY (id)` on range-partitioned-by-date table without including date.
- Remediation:
  - Before:

    ```sql
    -- Partition by created_at, but PK lacks created_at
    CREATE TABLE invoice (
      invoice_id uuid PRIMARY KEY,
      created_at timestamptz NOT NULL
    ) PARTITION BY RANGE (created_at);
    ```

  - After (example pattern):

    ```sql
    CREATE TABLE invoice (
      invoice_id uuid NOT NULL,
      created_at timestamptz NOT NULL,
      PRIMARY KEY (created_at, invoice_id)
    ) PARTITION BY RANGE (created_at);
    ```

- Risk/impact: high correctness impact; may require application-level key handling changes.
- Tests/CI checks: uniqueness tests across partitions; partition routing tests.

**P1-3 Online partition maintenance (detach/attach and lock costs)**

- Rationale: PostgreSQL supports `DETACH PARTITION` (takes `ACCESS EXCLUSIVE`) and `DETACH PARTITION CONCURRENTLY` (lighter lock). Caveats for the concurrent form: **cannot run inside a transaction block**, requires a follow-up `ALTER TABLE ... DETACH PARTITION ... FINALIZE` if interrupted, and is **incompatible with default partitions**. Index creation on a partitioned parent uses `ON ONLY ...` then per-partition `CREATE INDEX CONCURRENTLY` followed by `ATTACH PARTITION`.
- Detection:
  - Backfills or retention deletes taking `ACCESS EXCLUSIVE` locks on the parent.
- Remediation: prefer detach/drop for retention; attach staged tables with pre-created matching CHECK constraints to avoid full scans on attach. Build per-partition indexes concurrently then attach to the parent index.
- Risk/impact: high operational impact; mistakes can block writes.
- Tests/CI checks: migration rehearsal on staging with lock monitoring.

### P1: Storage, TOAST, and table parameters (PostgreSQL)

**P1-4 Tune fillfactor for update-heavy tables**

- Rationale: PostgreSQL `fillfactor` reserves free space on pages to make UPDATEs more likely to stay on the same page (more efficient, more HOT updates).
- Detection:
  - Update-heavy table with page splits and high bloat; index maintenance overhead.
- Remediation:
  - Before: default fillfactor=100 on hot-update table.
  - After:

    ```sql
    ALTER TABLE orders SET (fillfactor = 80);
    ```

- Risk/impact: Can reduce bloat and improve write latency; risk is increased initial storage footprint.
- Tests/CI checks: write-path benchmarks; bloat metrics monitoring.

**P1-5 Understand TOAST storage and compression choices**

- Rationale:
  - PostgreSQL TOAST strategies determine whether large values are compressed and/or stored out-of-line.
  - PostgreSQL supports column `COMPRESSION` methods `pglz` (default) and `lz4` (PG14+, when built with lz4). The cluster-wide default is controlled by the `default_toast_compression` GUC. **Note:** `zstd` is **not** a TOAST compression option in core PostgreSQL; it is used for WAL compression (`wal_compression = zstd`, PG15+) and for `pg_basebackup`. Do not conflate the two.
  - Storage mode choices (`PLAIN` / `EXTENDED` / `EXTERNAL` / `MAIN`) trade storage for faster substring operations.
- Detection:
  - EXPLAIN `(SERIALIZE)` shows time dominated by TOAST fetch/decompression.
- Remediation (example patterns):
  - Always prefer `jsonb` over `json` for stored data: `json` stores text and re-parses on every read; `jsonb` stores a decomposed binary form.
  - For large text columns used in substring operations, consider `STORAGE EXTERNAL` (skips compression, faster substring).
  - Set per-column compression:

    ```sql
    ALTER TABLE docs
      ALTER COLUMN body
      SET COMPRESSION lz4;
    ```

  - Set the cluster default (postgresql.conf, PG14+):

    ```conf
    default_toast_compression = lz4
    ```

- Risk/impact: medium-to-high; risk of unexpected storage/CPU tradeoffs and migration time (existing rows are not recompressed by `ALTER`).
- Tests/CI checks: query perf tests on text/json operations; storage usage monitoring.

### P1: Replication and high availability

**P1-6 Monitor replication slot health**

- Rationale: An inactive logical-replication slot retains WAL on the primary until disk-fill. This is one of the top operational outage causes for PostgreSQL deployments using logical replication or CDC tooling (Debezium, Kafka Connect, etc.).
- Detection:

  ```sql
  SELECT slot_name, slot_type, active, database,
         pg_size_pretty(pg_wal_lsn_diff(pg_current_wal_lsn(), restart_lsn)) AS retained_wal
  FROM pg_replication_slots
  ORDER BY pg_wal_lsn_diff(pg_current_wal_lsn(), restart_lsn) DESC;
  ```

- Remediation: alert on `active = false` for > 5 minutes; configure `max_slot_wal_keep_size` to bound retention; drop abandoned slots with `pg_drop_replication_slot()`.
- Risk/impact: Disk-fill on the primary cascades into a cluster-wide outage.
- Tests/CI checks: monitoring rule on slot age and WAL retention; chaos test that disconnects a subscriber and verifies the alert fires.

**P1-7 Set REPLICA IDENTITY explicitly on tables in a logical publication**

- Rationale: Without a primary key, a unique index marked `REPLICA IDENTITY USING INDEX`, or `REPLICA IDENTITY FULL`, logical replication **cannot replicate `UPDATE` or `DELETE`** — the operations succeed on the primary but silently fail on the subscriber.
- Detection:

  ```sql
  SELECT n.nspname, c.relname, c.relreplident
  FROM pg_class c JOIN pg_namespace n ON c.relnamespace = n.oid
  WHERE c.relkind = 'r' AND c.relreplident = 'd' -- 'd' = default; needs PK
    AND c.oid IN (SELECT prrelid FROM pg_publication_rel);
  ```

- Remediation: ensure every published table has a PK, or set `ALTER TABLE t REPLICA IDENTITY USING INDEX <unique_idx>`. Use `REPLICA IDENTITY FULL` only as a last resort — it logs the entire old row on every UPDATE/DELETE.
- Risk/impact: Silent data divergence between primary and subscriber.
- Tests/CI checks: assertion in CI that every table in any `pg_publication` has a non-default `relreplident`.

**P1-8 Define `synchronous_commit` posture per workload**

- Rationale: `synchronous_commit = on` (default) acks the client when WAL is flushed locally only — a primary failure between local fsync and replica receipt loses the transaction. Cross-AZ / cross-region durability requires `synchronous_commit = remote_apply` (or `remote_write`) plus `synchronous_standby_names`.
- Detection: review `synchronous_commit` per role and per session; identify write paths that need stronger durability than the cluster default.
- Remediation:

  ```sql
  ALTER ROLE writer SET synchronous_commit = remote_apply;
  ALTER SYSTEM SET synchronous_standby_names = 'ANY 1 (replica1, replica2)';
  ```

- Risk/impact: Stronger durability has a latency cost; weaker durability has a data-loss cost. The right answer is workload-dependent and must be explicit.
- Tests/CI checks: documented RPO target per workload; monitoring of replication lag against the target.

**P1-9 Backups, PITR, and tested restore cadence**

- Rationale: A backup that has never been restored is not a backup. Cloud-managed PostgreSQL provides snapshots and PITR by default, but the **restore time** (RTO) and **point-in-time precision** (RPO) must be measured.
- Detection: locate the last documented restore drill; if > 90 days, schedule one.
- Remediation: schedule quarterly restore drills into a sandbox environment; record actual RTO/RPO and compare against the documented target. For self-managed PostgreSQL, configure `archive_command` to a durable object store (S3/GCS/Azure Blob) and verify with `pg_verifybackup`. PG17's `pg_basebackup --incremental` + `pg_combinebackup` reduces backup time and storage for large clusters.
- Risk/impact: Untested backups are the most expensive lesson in operations.
- Tests/CI checks: scheduled drill ticket; report attached to quarterly review.

### P2: Index maintenance, bloat, and hinting tools

**P2-1 Manage bloat and rebuild safely**

- Rationale: PostgreSQL provides REINDEX to rebuild indexes, and VACUUM behavior includes index cleanup decisions.
- Detection:
  - Rising index size; VACUUM not keeping up; performance degradation.
- Remediation:
  - `REINDEX` for targeted rebuilds.
  - Use pg_repack to remove bloat online with minimal locking; it is positioned as an alternative to VACUUM FULL in some environments.
- Risk/impact: Operational risk; schedule carefully; require runbooks.
- Tests/CI checks: maintenance rehearsal in staging; post-maintenance benchmarks.

**P2-2 Use planner hints only with explicit governance**

- Rationale: PostgreSQL core planner is typically improved through statistics/schema changes; hinting extensions like pg_hint_plan exist to influence plan decisions via comments.
- Detection: repeated plan instability after addressing stats/indexes/query structure.
- Remediation:
  - Document which hints are used and why; ensure monitoring for plan regressions.
- Risk/impact: high maintenance burden; hints can become wrong as data distributions change.
- Tests/CI checks: plan snapshot tests on representative datasets; alerts on plan drift.

## Modularity and readability checklist

This checklist is **explicitly subordinate** to performance gates above. It aims to make schemas self-explanatory and safe—without introducing performance regressions.

**M0-1 Naming consistency and discoverability**

- Rationale: Consistent naming reduces ambiguity and supports automation and tooling (see [GR §7]).
- Detection: lint schema for mixed singular/plural, mixed casing, unclear abbreviations.
- Remediation:
  - Before: `OrgDeptRole`, `dept_role`, `org_deptRoles`
  - After: consistent `snake_case` and stable naming convention.
- Risk/impact: Renames can be high-risk migrations; only do with explicit compatibility strategies.
- Tests: migration tests; backward compatibility tests (views, dual-write).

**M0-2 Primary keys, timestamps, and NULL policy**

- Rationale: Strong defaults: a single primary key (or partition-key-inclusive composite for partitioned tables, [GR §1.14]), `created_at`/`updated_at` ([GR §1.4]), and NOT NULL by default unless NULL has explicit meaning ([GR §1.5]).
- Detection: schema scan for missing PKs, nullable columns without documented meaning, missing audit fields.
- Remediation: add PK and audit columns; add NOT NULL via the `NOT VALID` CHECK trick (see P0-17) to avoid full-table scans.
- Risk/impact: medium-to-high in existing tables; naive `SET NOT NULL` rewrites the table on older PG.
- Tests: integrity tests; application contract tests.

**M0-3 Foreign keys and deliberate ON DELETE/ON UPDATE**

- Rationale: PostgreSQL constraints docs emphasize FK semantics and indexing considerations; "soft" FKs (referential intent without a constraint) are prohibited ([GR §3.1, §3.4]). `ON UPDATE` is required only when the referenced key is mutable.
- Detection: orphan checks, missing FK constraints, ambiguous cascade behavior.
- Remediation:
  - Before:

    ```sql
    user_id uuid -- "FK" but no constraint
    ```

  - After:

    ```sql
    ALTER TABLE orders
      ADD CONSTRAINT fk_orders_user
      FOREIGN KEY (user_id) REFERENCES users(user_id)
      ON DELETE RESTRICT NOT VALID;
    ALTER TABLE orders VALIDATE CONSTRAINT fk_orders_user;
    ```

- Risk/impact: high correctness improvement; risk is unexpected cascade restrictions in legacy data.
- Tests: FK enforcement tests; cascade behavior tests; query-perf tests for deletes/joins (and add FK indexes per [GR §3.5]).

**M0-4 CHECK constraints and domain validation**

- Rationale: CHECK constraints encode domain rules at the engine level ([GR §1.9]).
- Detection: invalid values in production; status stored as free-form text.
- Remediation:
  - Before: `status text`
  - After:

    ```sql
    ALTER TABLE jobs
      ADD CONSTRAINT chk_jobs_status
      CHECK (status IN ('queued','running','done','failed')) NOT VALID;
    ALTER TABLE jobs VALIDATE CONSTRAINT chk_jobs_status;
    ```

- Risk/impact: medium; backfilling invalid values required.
- Tests: constraint tests; migration pre-checks.

**M0-5 ENUM vs lookup table decision**

- Rationale:
  - PostgreSQL ENUM types are ordered sets of values. **Operational caveats:** adding a value mid-list requires `ALTER TYPE ... ADD VALUE BEFORE/AFTER`, **cannot run inside a transaction block**, and is therefore awkward in versioned migration tools. **Removing a value is unsupported** without recreating the type and rewriting every column that uses it.
  - MySQL ENUM is a string chosen from explicit permitted values.
  - SQLite supports structural enforcement via foreign keys and STRICT tables, but does not have a native enum type.
- Detection: statuses that change frequently; multi-tenant domains with evolving state machines.
- Remediation guidance:
  - **Prefer lookup tables** when you need:
    - additional metadata per value,
    - frequent value lifecycle changes (especially deletions/renames),
    - cross-engine portability,
    - the ability to manage value evolution inside transactional migrations.
  - Prefer ENUM only for stable, truly static sets (days of week, small closed sets that will never lose a member).
- Risk/impact: ENUM migrations are operationally tricky; lookup tables increase join cost (usually negligible with a covering index).
- Tests: migration tests; referential integrity tests; query perf tests on filters.

**M0-6 SQL linting and formatting**

- Rationale: SQLFluff is an established linter/formatter supporting multiple dialects and intended to catch errors and bad SQL before it hits your database.
- Detection: inconsistent formatting, ambiguous joins, use of `SELECT *`, missing schema qualification conventions.
- Remediation: enforce SQLFluff in CI (see CI section).
- Risk/impact: low runtime risk; medium merge churn in large repos.
- Tests: CI lint gate.

## Audit templates, report template, and scoring rubric

### Table-level audit template

Use this as a spreadsheet or markdown table; record **one row per table**, updated per audit cycle.

| Table | Domain/schema | Purpose (1 line) | Size (rows/pages/GB) | Hot queries (top 3–5) | blocking predicates & joins | Current indexes summary | EXPLAIN evidence links | Autovacuum / stats posture | Partitioned? key & pruning | Lock/concurrency risks | P0 findings | P1 findings | Risk (change cost) | Effort (XS–XL) | Owner | Status |
| ----- | ------------- | ---------------- | -------------------: | --------------------- | --------------------------- | ----------------------- | ---------------------- | -------------------------- | -------------------------- | ---------------------- | ----------- | ----------- | ------------------ | -------------- | ----- | ------ |

**Minimum required evidence per hot table**:

- `pg_stat_statements` excerpt or slow-log excerpt.
- `EXPLAIN (ANALYZE, BUFFERS)` (and `WAL` if write-heavy).
- Stats freshness: last analyze/vacuum, and whether autovacuum is enabled at table level.

### Index-level audit template

Record **one row per index** (including PK/unique indexes).

| Index name | Table | Type (btree/gin/gist/brin/…) | Columns / expression | Predicate (partial?) | INCLUDE columns | Supports index-only? | Intended query pattern | Actual plan usage | Write cost risk | Maintenance/bloat notes | Action (keep/drop/modify) | Migration safety plan |
| ---------- | ----- | ---------------------------- | -------------------- | -------------------- | --------------- | -------------------- | ---------------------- | ----------------- | --------------- | ----------------------- | ------------------------- | --------------------- |

Key reminders:

- PostgreSQL index-only scan requirements, visibility map dependence, and INCLUDE design tradeoffs are explicitly documented.
- GIN does not support index-only scans (by design).
- BRIN is designed for very large tables where columns correlate with physical location.

### Report template for an audit cycle

Use this structure to publish an audit report (per domain/schema, quarterly, or per major release):

**Scope and environment**

- DB engine/version, instance sizes, dataset size, sampling window for stats.
- Extensions enabled (e.g., pg_stat_statements).

**Workload summary**

- Top queries by total time and by calls.
- Slow query log summary (thresholds used).

**Findings summary**

- P0 blockers (must-fix)
- P1 improvements
- P2 long-term work

**Per-table findings**

- Table audit table excerpt
- EXPLAIN evidence
- Index proposals and tradeoffs

**Migration plan**

- Online steps, lock strategy, rollback plan
- Verification steps and CI gates

**Decision log**

- Any risk acceptance (explicit owner + reason + date)

Your tiered review rubric in [`Golden Rules for SQL Database Design.md`](Golden%20Rules%20for%20SQL%20Database%20Design.md) provides the structured sign-off block and decision matrix; incorporate it here as the governance layer around approvals and risk acceptance.

### Scoring rubric and grading

A practical scoring rubric must reflect the hierarchy (performance dominates), while still capturing correctness/integrity and maintainability risk.

**Per-table scoring dimensions (0–5 each)**

- **Performance (weight 45%)**: plan quality, index alignment, pruning, IO/WAL footprint, tail latency risk.
- **Correctness/integrity (weight 30%)**: PK/FK/unique/check constraints, null policy, data type correctness.
- **Maintainability (weight 15%)**: naming clarity, domain boundaries, constraint naming, migration readability.
- **Change risk (weight 10%)**: migration downtime risk, backfill complexity, lock risk.

```mermaid
pie title Table Score Weights
  "Performance" : 45
  "Correctness/Integrity" : 30
  "Maintainability" : 15
  "Change Risk" : 10
```

**Grade bands**

- **A (4.5–5.0)**: performance + integrity solid; safe to scale.
- **B (3.5–4.4)**: acceptable; schedule P1 work.
- **C (2.5–3.4)**: performance or integrity issues likely; remediation required before scale.
- **D (<2.5)**: high outage or corruption risk; block major releases until fixed.

This scoring rubric is the **audit-time** weighting (performance dominates because every other dimension has already been gated at design time). The companion **design-time** rubric in [`Golden Rules for SQL Database Design.md`](Golden%20Rules%20for%20SQL%20Database%20Design.md) weights Integrity 40 / Performance 25 / Maintainability 20 / Security 15. blocking / high / medium severities there map to P0 / P1 / P2 here ([GR §10]).

## Refactor and migration workflow, CI gates, and sample SQL fixes

### Step-by-step workflow with effort estimates (XS–XL)

Effort definitions:

- **XS**: < 0.5 day
- **S**: 0.5–1 day
- **M**: 1–3 days
- **L**: 3–5 days
- **XL**: 1–2+ weeks

Workflow:

1. **Baseline capture (S)**
   - Enable pg_stat_statements, collect top queries; enable slow query logging threshold.

2. **Hot query EXPLAIN pack (S–M)**
   - Produce EXPLAIN (ANALYZE, BUFFERS, WAL, FORMAT JSON) for top queries and attach to audit.

3. **P0 index alignment fixes (M–L)**
   - Add missing FK indexes; correct composite index ordering; add partial/expression indexes. Use concurrency-safe creation where possible.

4. **P0 operational guardrails (S)**
   - Apply statement/lock/idle timeouts in a safe, role- or transaction-scoped way.

5. **P1 partitioning or materialized views (L–XL)**
   - Only after evidence shows pruning/retention wins; follow documented partition constraints and index limitations. Consider materialized views for computed summaries.

6. **P2 bloat remediation (S–M)**
   - For heavy bloat, consider pg_repack or REINDEX; validate operational constraints.

7. **Modularity/readability refactors (M)**
   - Naming, constraint naming, documentation comments, domain schema alignment.

8. **README/documentation update (S)**
   - Document only once schema and performance are stable and CI-gated.

### Sample SQL fixes for common issues

**Fix: make an un-indexable predicate indexable (expression index)**

- Before:

  ```sql
  SELECT * FROM users WHERE lower(email) = lower($1);
  ```

- After:

  ```sql
  CREATE INDEX CONCURRENTLY idx_users_lower_email
    ON users (lower(email));

  SELECT user_id, display_name
  FROM users
  WHERE lower(email) = lower($1);
  ```

Expression indexes are explicitly supported.

**Fix: enable index-only scans via INCLUDE**

- Before:

  ```sql
  SELECT display_name FROM users WHERE email = $1;
  ```

- After:

  ```sql
  CREATE INDEX CONCURRENTLY idx_users_email_cover
    ON users(email) INCLUDE (display_name);
  ```

Index-only scans and INCLUDE tradeoffs are explicitly documented.

**Fix: boolean/low-cardinality indexing via partial index**

- Before:

  ```sql
  CREATE INDEX idx_items_is_active ON items(is_active);
  ```

- After:

  ```sql
  CREATE INDEX CONCURRENTLY idx_items_active_org_created
    ON items(org_id, created_at DESC)
    WHERE is_active = true;
  ```

Partial indexes are built over subsets defined by predicates.

**Fix: bulk load properly in PostgreSQL**

- Before:

  ```sql
  INSERT INTO events(...) VALUES (...); -- repeated millions of times
  ```

- After:

  ```sql
  COPY events FROM STDIN (FORMAT csv);
  -- then build indexes after load
  ```

PostgreSQL docs explicitly recommend COPY then indexes for fastest fresh load.

**Fix: safe upsert**

- PostgreSQL atomic UPSERT:

  ```sql
  INSERT INTO inventory(sku, qty)
  VALUES ($1, $2)
  ON CONFLICT (sku) DO UPDATE SET qty = EXCLUDED.qty;
  ```

PostgreSQL guarantees atomic insert-or-update for ON CONFLICT DO UPDATE.

**Fix: partitioning with retention**

- After (range partitions by month):

  ```sql
  CREATE TABLE measurement (
    city_id int NOT NULL,
    logdate date NOT NULL,
    peaktemp int,
    unitsales int
  ) PARTITION BY RANGE (logdate);

  CREATE TABLE measurement_2026_03 PARTITION OF measurement
    FOR VALUES FROM ('2026-03-01') TO ('2026-04-01');
  ```

PostgreSQL partitioning methods and examples are documented, including retention via dropping/detaching partitions.

### CI gates and sample YAML snippets

**Gate categories**

- **SQL lint/format**: SQLFluff with dialect configuration for Postgres/MySQL/SQLite.
- **Schema correctness tests**: pgTAP for PostgreSQL schema/unit assertions.
- **Migration rehearsal**: apply migrations to ephemeral DB; run smoke queries; run EXPLAIN checks.
- **Performance budgets**: pgbench scripts for representative OLTP transactions; pgbench is included as a PostgreSQL benchmark tool.

Example GitHub Actions job (PostgreSQL-focused, adaptable):

```yaml
name: db-ci

on:
  pull_request:
  push:
    branches: [ main ]

jobs:
  postgres-audit:
    runs-on: ubuntu-latest
    services:
      postgres:
        image: postgres:17
        env:
          POSTGRES_PASSWORD: postgres
        ports:
          - 5432:5432
        options: >-
          --health-cmd "pg_isready -U postgres"
          --health-interval 10s
          --health-timeout 5s
          --health-retries 10

    steps:
      - uses: actions/checkout@v4

      - name: Install tools
        run: |
          pip install sqlfluff
          sudo apt-get update
          sudo apt-get install -y postgresql-client

      - name: SQLFluff lint
        run: |
          sqlfluff lint --dialect postgres sql/

      - name: Apply migrations
        env:
          DATABASE_URL: postgresql://postgres:postgres@localhost:5432/postgres
        run: |
          # Replace with Flyway/Liquibase/Alembic/etc
          ./scripts/apply_migrations.sh

      - name: Run schema assertions (pgTAP)
        run: |
          # Example: psql -f test/sql/pgtap_tests.sql
          psql postgresql://postgres:postgres@localhost:5432/postgres -f test/sql/pgtap_tests.sql

      - name: EXPLAIN plan gate (no Seq Scan on hot query)
        run: |
          psql postgresql://postgres:postgres@localhost:5432/postgres <<'SQL'
          EXPLAIN (FORMAT JSON)
          SELECT /* hot query */ 1;
          SQL

      - name: pgbench performance smoke
        run: |
          pgbench -i postgresql://postgres:postgres@localhost:5432/postgres
          pgbench -T 30 -c 10 postgresql://postgres:postgres@localhost:5432/postgres
```

Notes:

- pgbench runs a repeated SQL sequence and reports average transaction rate; you can replace the default script with your own transaction scripts for domain-specific workloads.
- For MySQL, equivalent gates can parse the slow query log and use Performance Schema methodologies; MySQL explicitly states the slow query log surfaces candidate queries and that Performance Schema can be used for repeatable bottleneck analysis.
- For SQLite, CI can run `EXPLAIN QUERY PLAN` checks to ensure index usage in critical queries.

## Component relationships and migration timeline diagrams

### Mermaid schema relationship diagram

```mermaid
erDiagram
  USERS ||--o{ ORDERS : places
  ORDERS ||--o{ ORDER_ITEMS : contains
  PRODUCTS ||--o{ ORDER_ITEMS : referenced_by
  ORGS ||--o{ USERS : owns

  USERS {
    uuid user_id PK
    uuid org_id FK
    text email
    timestamptz created_at
  }
  ORDERS {
    uuid order_id PK
    uuid user_id FK
    uuid org_id FK
    text status
    timestamptz created_at
  }
```

### Mermaid Gantt chart for a performance-first audit/refactor

```mermaid
gantt
  title SQL Table Design Performance-First Audit and Migration
  dateFormat  YYYY-MM-DD
  axisFormat  %b %d

  section Baseline
  Enable stats + slow logging                 :a1, 2026-03-31, 2d
  Capture top queries (pg_stat_statements)    :a2, after a1, 2d
  EXPLAIN pack for hotspots                   :a3, after a2, 3d

  section P0 Fixes
  Missing FK + composite indexes              :b1, after a3, 5d
  Partial/expression/covering indexes         :b2, after b1, 5d
  Timeout + lock guardrails                   :b3, after b1, 2d

  section P1 Scaling
  Partitioning or materialized views          :c1, after b2, 10d
  Bulk-load + upsert hardening                :c2, after b2, 4d

  section Quality Gates
  SQL lint + pgTAP + EXPLAIN gates            :d1, after b2, 4d
  pgbench budget baseline                     :d2, after d1, 3d

  section Documentation
  README + data catalog updates               :e1, after d2, 2d
```

This staged approach aligns with PostgreSQL’s explicit tooling expectations: you profile with EXPLAIN options and use maintenance/statistics pathways (autovacuum/ANALYZE) before making large structural moves like partitioning.
