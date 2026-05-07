---
id: archive.2026-04-29.sql.design-rules.golden-rules-for-sql-database-design-v1
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
supersedes: []
doc_version: 1.0.0
---
# Golden Rules for SQL Database Design

**A Senior Engineer's Rubric** · Version 2.0 · 2026

*Schema Design · Normalization · Indexing · Relationships · Performance · Compliance · Audit*

> **Scope:** Design-time rules. Engine-leaning toward PostgreSQL 16+ where noted, otherwise portable across PG/MySQL/SQLite.
> **Companion:** Audit/operations rules live in [`sql-performance-audit-playbook.md`](sql-performance-audit-playbook.md). Cross-references use `[PB Pn-m]`.
> **Severity model:** Critical / High / Medium — unified with the Playbook's P0 / P1 / P2 (see §10).

---

## 1. Table & Schema Design

Well-structured tables form the foundation of a performant, maintainable database.

| # | Rule | Rationale | Severity |
|---|------|-----------|----------|
| 1.1 | Use singular, lowercase, `snake_case` table names (e.g., `customer_order`, not `CustomerOrders`). | Prevents quoting issues across engines; matches SQL convention. | **High** |
| 1.2 | Every **non-partitioned** table must have a single-column surrogate primary key. On **partitioned** tables, the PK and every UNIQUE constraint must include all partition-key columns (see 1.14). | PostgreSQL cannot enforce uniqueness across partitions otherwise. | **Critical** |
| 1.3 | Prefer `BIGINT GENERATED ALWAYS AS IDENTITY` (PG10+, SQL-standard) for surrogate PKs. Use UUIDs only when distributed generation or opacity is required; UUIDv4 (`gen_random_uuid()`) hurts B-tree locality, so prefer UUIDv7 via app-side generation or the `pg_uuidv7` extension when chosen. Avoid `SERIAL`/`BIGSERIAL` for new tables. | Identity columns avoid implicit sequence-ownership bugs; UUIDv4 PKs cause page-split write amplification on hot tables. | **High** |
| 1.4 | Every table must include `created_at TIMESTAMPTZ NOT NULL DEFAULT now()` and `updated_at TIMESTAMPTZ NOT NULL DEFAULT now()`. | Enables auditing, debugging, and incremental ETL. | **High** |
| 1.5 | Default all columns to `NOT NULL` unless NULL carries explicit business meaning. | Eliminates three-valued-logic bugs, improves planner cardinality estimates, and makes `IS NOT NULL` predicates unnecessary. | **Critical** |
| 1.6 | Choose the smallest correct data type for the domain. In PostgreSQL, prefer `text` over `varchar(n)` (see 1.12); choose `int` vs `bigint` and `date` vs `timestamptz` based on the domain, not perceived storage savings. | Smaller types reduce page count for fixed-width columns and shrink indexes. | **Medium** |
| 1.7 | Never store monetary values in `FLOAT`/`DOUBLE`. Use `numeric(p,s)` with precision derived from the largest legal value × 10ˢ, **or** integer minor units (cents as `BIGINT`) on hot OLTP paths (see 1.15). | Floating-point rounding causes financial discrepancies; integer arithmetic is branch-free and indexable. | **Critical** |
| 1.8 | Store all timestamps as `TIMESTAMPTZ` in UTC. Convert to local time only at the application or report layer. | Avoids DST ambiguity and cross-timezone comparison bugs. | **High** |
| 1.9 | Add `CHECK` constraints for domain validation (e.g., `CHECK (status IN ('active','inactive'))`, `CHECK (price >= 0)`). On large tables, add as `NOT VALID` then `VALIDATE CONSTRAINT` to avoid an `ACCESS EXCLUSIVE` scan ([PB P0-16]). | Catches invalid data at the engine level. | **Medium** |
| 1.10 | Avoid storing derived/calculated data unless materialized for proven performance reasons. Prefer `GENERATED ALWAYS AS (expr) STORED` over application-managed duplicates (see 1.13). | Keeps a single source of truth; eliminates stale-calculation drift. | **Medium** |
| 1.11 | Use `BIGINT GENERATED ALWAYS AS IDENTITY` instead of `SERIAL`/`BIGSERIAL` for new tables (PG10+). | Identity columns are SQL-standard, avoid implicit sequence-ownership bugs, and prevent direct sequence manipulation. | **High** |
| 1.12 | In PostgreSQL, prefer `text` over `varchar(n)` unless a length cap is a real domain rule. | `text`, `varchar`, and `varchar(n)` share identical `varlena` storage; `varchar(n)` only adds a CHECK that can break online migrations when raised. | **Medium** |
| 1.13 | Use `GENERATED ALWAYS AS (expr) STORED` (PG12+) for derived columns that are read often and computed deterministically. | Materializes once on write; eligible for indexing; eliminates application drift. | **Medium** |
| 1.14 | For partitioned tables, the primary key and every UNIQUE constraint must include all partition-key columns. | PostgreSQL cannot enforce uniqueness across partitions otherwise ([PB P1-2]). | **Critical** |
| 1.15 | Prefer integer minor units (e.g., cents as `BIGINT`) for money on hot OLTP paths; reserve `numeric(p,s)` for tax/FX/aggregate calculations where fractional units are required. | Integer arithmetic is branch-free and indexable; `numeric` is arbitrary-precision and ~10× slower. | **High** |
| 1.16 | Use `CITEXT` (extension) for case-insensitive UNIQUE columns such as email; otherwise enforce via a unique expression index on `lower(col)`. | Avoids duplicate-by-case bugs; documents the case-insensitive semantic at the type level. | **Medium** |
| 1.17 | Use `tstzrange` / `daterange` + `EXCLUDE USING gist (resource_id WITH =, period WITH &&)` for non-overlap constraints (bookings, shifts, schedules). | Engine-enforced; eliminates an entire class of double-booking races. | **High** |
| 1.18 | Use `DEFERRABLE INITIALLY DEFERRED` foreign keys only when cyclic insert ordering or end-of-transaction validation is genuinely required. | Otherwise pay the deferred-constraint queue cost without benefit. | **Medium** |
| 1.19 | Use `INET`/`CIDR` for IP addresses, `MACADDR` for MAC addresses, `tsvector` for full-text search columns, and `uuid` as a first-class type — never `text` substitutes. | Native types provide validation, indexable operators, and accurate storage. | **Medium** |

> **Pro Tip:** If you find yourself adding a column named `type` or `kind` that changes the meaning of other columns, consider splitting into separate tables (table-per-type) or using a discriminated polymorphism pattern. PostgreSQL table inheritance is rarely the right tool because it does not enforce uniqueness or FKs across children.

---

## 2. Normalization & Denormalization

Normalize first for correctness, then selectively denormalize with measurement-backed justification.

| # | Rule | Rationale | Severity |
|---|------|-----------|----------|
| 2.1 | Achieve at least Third Normal Form (3NF) for all OLTP tables before considering denormalization. | Eliminates update/insert/delete anomalies. | **Critical** |
| 2.2 | Never store comma-separated values when a junction table is appropriate. **Native arrays / JSONB arrays are acceptable** when (a) the array represents an entity's own value (tags, labels, search terms), (b) elements are not first-class entities with their own attributes, and (c) you do not need engine-enforced FK integrity on each element. Use a junction table for relationships to first-class entities. | CSV-in-text destroys integrity and indexing; native arrays + GIN are a legitimate PG pattern. | **Critical** |
| 2.3 | Denormalize only when (a) the slow query is in the top-20 by total time in `pg_stat_statements`, (b) p95 exceeds the documented SLO, **and** (c) indexing/query rewrite alternatives have been EXPLAIN-tested and rejected. Document the dated trigger metric in a `COMMENT ON TABLE`. | Premature denormalization creates technical debt with no measured payoff. | **High** |
| 2.4 | When denormalizing, prefer materialized views, summary tables, or `GENERATED ... STORED` columns over duplicated free-form columns. | Refreshable atomically; columns can drift silently. | **Medium** |
| 2.5 | If you store JSON/JSONB, define a JSON Schema or `CHECK (jsonb_matches_schema(...))` constraint to enforce structure. Always use `jsonb`, never `json`, for stored data. | Unvalidated JSON becomes a shadow schema no one understands; `json` re-parses on every read. | **Medium** |
| 2.6 | Eliminate transitive dependencies: a non-key column should never depend on another non-key column. | Prevents update anomalies. | **High** |

---

## 3. Relationships & Referential Integrity

Foreign keys are contracts between tables.

| # | Rule | Rationale | Severity |
|---|------|-----------|----------|
| 3.1 | Every foreign key must declare an explicit `ON DELETE` action. `ON UPDATE` is required only when the referenced key is mutable (rare with surrogate PKs). | Missing cascade/restrict rules leave orphan records or silent failures. | **Critical** |
| 3.2 | Name foreign key columns as `<referenced_table>_id`. | Self-documenting; enables automated ORM discovery. | **High** |
| 3.3 | Use junction/bridge tables for many-to-many relationships between first-class entities. | Preserves referential integrity and enables efficient querying. | **Critical** |
| 3.4 | Choose `ON DELETE` actions deliberately: `RESTRICT`/`NO ACTION` for strong entities, `CASCADE` for owned children, `SET NULL` for optional associations. Document the choice in `COMMENT ON CONSTRAINT`. | Wrong action either blocks operations or silently destroys data. | **Critical** |
| 3.5 | Always index foreign key columns. PostgreSQL does not auto-index them; deletes/updates on the referenced table scan the referencing table without an index. | Unindexed FKs cause `O(n)` scans on parent DELETE/UPDATE. | **Critical** |
| 3.6 | Use composite foreign keys only when the referenced table has a composite PK; prefer surrogate keys otherwise. Composite FKs are correct when they reinforce a multi-column invariant (e.g., tenant-scoping). | Reduces key width and join complexity, but composites are required to prevent cross-tenant leakage. | **High** |
| 3.7 | For soft-delete patterns, add a `deleted_at TIMESTAMPTZ` column and back hot read paths with a partial index `WHERE deleted_at IS NULL`. Keep FK integrity intact. | Allows auditability and undo without paying the index cost on tombstones. | **High** |
| 3.8 | Self-referencing FKs (e.g., `employee.manager_id → employee.id`) must allow `NULL` for root nodes. | Prevents a chicken-and-egg insertion problem. | **Medium** |
| 3.9 | Add new FKs and CHECKs on large tables with `ADD CONSTRAINT ... NOT VALID` followed by `VALIDATE CONSTRAINT` ([PB P0-16]). | The two-phase form takes a brief lock, then validates under `SHARE UPDATE EXCLUSIVE` instead of `ACCESS EXCLUSIVE`. | **High** |

---

## 4. Indexing Strategy

Indexes are the single largest lever for read performance. Every index also carries a write penalty.

| # | Rule | Rationale | Severity |
|---|------|-----------|----------|
| 4.1 | Index columns used in predicates of queries that appear in the top-50 of `pg_stat_statements` by `total_exec_time`. Composite predicates take a single composite index ([PB P0-6]), not one index per column. | Aligns indexing effort with measured workload. | **Critical** |
| 4.2 | Compose composite indexes as **equality predicates first, then one range or sort column**. Column order determines whether the planner can use the index for filtering and sort elimination. | A high-selectivity column used only with `>=` should not lead. | **Critical** |
| 4.3 | Don't create a standalone single-column index on a low-cardinality column. **Do** create a partial index keyed on the low-cardinality predicate (e.g., `CREATE INDEX ... WHERE is_active = true`) or include it as a leading column in a composite index that covers the whole hot query. | A standalone boolean index is rarely chosen; a partial index on the same column is one of the highest-value index types. | **High** |
| 4.4 | Use `INCLUDE` (covering index) columns to enable index-only scans for read-heavy queries. Verify with EXPLAIN that the plan reports `Index Only Scan` and that heap-fetch counts are low (depends on visibility-map state). | Avoids heap lookups; falls back to heap fetches when visibility bits aren't set. | **High** |
| 4.5 | Use partial/filtered indexes for queries that target a row subset (`WHERE deleted_at IS NULL`, `WHERE status = 'active'`). | Dramatically smaller index; faster scans and lower maintenance. | **High** |
| 4.6 | Add `UNIQUE` constraints/indexes for business-level uniqueness (email, SKU, etc.) beyond the PK. For soft-delete tables, use `CREATE UNIQUE INDEX ... WHERE deleted_at IS NULL` to allow re-creation after deletion. | Prevents duplicate data at the engine level. | **Critical** |
| 4.7 | Each index increases write amplification and WAL volume. Justify each index against a measured query and drop indexes with `idx_scan = 0` after a representative observation window (≥ 30 days, including monthly batch jobs). There is no fixed numerical cap. | Workload-relative, not anecdote-relative. | **High** |
| 4.8 | Audit unused indexes with `pg_stat_user_indexes` on a defined cadence and drop them. | Dead indexes consume storage, slow writes, and bloat backups. | **Medium** |
| 4.9 | Use expression/functional indexes for computed predicates (e.g., `CREATE INDEX ON users (lower(email))`). The query must apply the same expression to use it. | Otherwise the planner cannot match the predicate to the index. | **High** |
| 4.10 | Match the PostgreSQL index type to the operator class: B-tree for equality/range on scalar types; **GIN** for JSONB containment, full-text search, and array `@>`; **GiST** for ranges, geometry, and exclusion constraints; **BRIN** for very large append-mostly tables with physical correlation (e.g., time-series); **SP-GiST** for non-balanced data; Hash only for equality lookups where you don't need ordering. | Wrong index type = full scan. | **High** |
| 4.11 | Build indexes on production with `CREATE INDEX CONCURRENTLY` (cannot be wrapped in a transaction; takes longer; can leave invalid index on failure that must be `DROP INDEX CONCURRENTLY`'d and rebuilt). | Avoids blocking writes during build. | **Critical** |

> **Index Audit Query (PostgreSQL):**
> ```sql
> SELECT schemaname, relname, indexrelname, idx_scan,
>        pg_size_pretty(pg_relation_size(indexrelid)) AS size
> FROM pg_stat_user_indexes
> WHERE idx_scan = 0
>   AND indexrelname NOT LIKE '%_pkey'
> ORDER BY pg_relation_size(indexrelid) DESC;
> ```

---

## 5. Performance & Scaling Patterns

| # | Rule | Rationale | Severity |
|---|------|-----------|----------|
| 5.1 | Design tables for read patterns: if a query always needs columns A, B, C together, ensure they share an index (consider `INCLUDE` for non-key payload). | Aligning physical storage with access patterns minimizes I/O. | **High** |
| 5.2 | Consider partitioning when **(a)** one table exceeds `shared_buffers`, AND **(b)** queries filter or retention deletes target a clear partition key. Row count alone is not a trigger. | Partitioning is operational overhead; pruning has to be real. | **High** |
| 5.3 | Use connection pooling (PgBouncer, RDS Proxy, pgcat) and avoid long-held transactions in OLTP systems. Idle-in-transaction sessions hold locks and block vacuum ([PB P0-12]). | Idle connections consume memory and cause contention. | **High** |
| 5.4 | Never use `SELECT *` in application code; project only the columns required. | Reduces I/O, prevents index-only scan invalidation, avoids TOAST/SERIALIZE cost ([PB P0-10]), and prevents schema-change surprises. | **High** |
| 5.5 | Avoid correlated subqueries in `SELECT`/`WHERE`; rewrite as `JOIN`s, lateral joins, or window functions. | Correlated subqueries execute per row; joins execute set-based. | **Medium** |
| 5.6 | Run `EXPLAIN (ANALYZE, BUFFERS)` on every new query touching a hot table or returning > 100 rows. For destructive statements, wrap in `BEGIN; … ROLLBACK;`. `EXPLAIN ANALYZE` executes the statement and adds non-trivial timing overhead. | Reveals sequential scans, nested loops, sort spills, and bad cardinality before users feel them. | **High** |
| 5.7 | Implement read replicas for reporting/analytics queries; never run analytics on the primary. Set `hot_standby_feedback` only after weighing the bloat/cancellation tradeoff ([PB P1-6]). | Offloads read traffic and prevents long-running queries from blocking writes. | **High** |
| 5.8 | Set `statement_timeout`, `lock_timeout`, and `idle_in_transaction_session_timeout` as role-level defaults, and override with `SET LOCAL` per transaction for batch jobs and migrations ([PB P0-12]). | A single bad query can exhaust connections and cascade into downtime. | **Critical** |
| 5.9 | Use `MERGE` (PG15+) for standards-compliant multi-row idempotent ETL where `INSERT ... ON CONFLICT` cannot express `WHEN NOT MATCHED BY SOURCE`. For single-row upserts, prefer `INSERT ... ON CONFLICT DO UPDATE` ([PB P0-14]). | Right tool for each pattern; `MERGE` is not always atomic in the same way as ON CONFLICT — read the docs. | **Medium** |

---

## 6. Concurrency, Transactions, and Migration Safety

| # | Rule | Rationale | Severity |
|---|------|-----------|----------|
| 6.1 | Choose isolation per use case: `READ COMMITTED` (default) for general OLTP, `REPEATABLE READ` for multi-statement reports, `SERIALIZABLE` (SSI) when the application can correctly retry on `40001` serialization failures. Document the choice and the retry contract. | Higher isolation costs throughput; without retry, `SERIALIZABLE` will surface as user-visible errors. | **High** |
| 6.2 | Use `SELECT ... FOR UPDATE SKIP LOCKED` for queue-style consumer patterns and `FOR UPDATE NOWAIT` for try-acquire semantics. | Avoids consumer pile-up; explicit failure beats silent blocking. | **High** |
| 6.3 | Use `pg_advisory_xact_lock(key)` for application-level mutexes (singleton jobs, migration leader election) — never `LOCK TABLE`. | Advisory locks are cheap, transaction-scoped, and don't block DDL. | **Medium** |
| 6.4 | Acquire locks in a deterministic order across the codebase to prevent deadlocks. Set `deadlock_timeout` (default 1 s) appropriately and instrument deadlock retries at the application layer. | Eliminates the most common preventable production deadlock class. | **High** |
| 6.5 | All migrations must run with `SET lock_timeout` (≤ 5 s) and a bounded `SET statement_timeout`, with retry-on-`55P03` (lock_not_available). Never run an unbounded `ALTER TABLE` against production ([PB P0-15]). | A blocked `ALTER TABLE` holds `ACCESS EXCLUSIVE` and queues every subsequent query. | **Critical** |
| 6.6 | Maintain versioned migration files (Flyway, Liquibase, Alembic, sqitch). Never apply ad-hoc DDL in production. | Reproducibility, rollback, audit history. | **Critical** |
| 6.7 | Use the **expand → migrate → contract** pattern for renames and breaking type changes: add new column → backfill in keyset-paged batches → dual-write → cut reads → drop old ([PB P0-18]). | In-place `RENAME`/`ALTER COLUMN ... TYPE` requires app dual-read or full-table rewrite. | **Critical** |
| 6.8 | To add `NOT NULL` to a large existing column, first add `CHECK (col IS NOT NULL) NOT VALID`, then `VALIDATE CONSTRAINT`, then `SET NOT NULL` (PG12+ uses the validated CHECK as proof and avoids a full scan), then drop the helper CHECK ([PB P0-17]). | Naive `SET NOT NULL` requires `ACCESS EXCLUSIVE` for the duration of a full table scan. | **High** |
| 6.9 | Backfills must be batched (keyset pagination, `LIMIT N`, commit between batches), not single-statement `UPDATE`s over millions of rows. | A monolithic UPDATE holds row locks, generates massive WAL, and balloons dead tuples. | **High** |
| 6.10 | DDL lock-strength matrix: `ADD COLUMN` with constant default is metadata-only since PG11; `ALTER COLUMN ... TYPE` usually rewrites the table; `ADD FOREIGN KEY` takes `SHARE ROW EXCLUSIVE` on both tables; `CREATE INDEX CONCURRENTLY` takes `SHARE UPDATE EXCLUSIVE`. Know what each migration locks. | Mis-classifying a migration as "fast" is a top outage cause. | **High** |

---

## 7. Naming Conventions & Documentation

| # | Rule | Rationale | Severity |
|---|------|-----------|----------|
| 7.1 | Use `snake_case` for tables, columns, indexes, constraints, and schemas. No quoted identifiers unless the project standard demands it. | Mixing conventions causes quoting errors and slows development. | **High** |
| 7.2 | Prefix boolean columns with `is_`, `has_`, or `can_` (e.g., `is_active`, `has_verified_email`). | Self-documenting; removes ambiguity. | **Medium** |
| 7.3 | Adopt a single index/constraint naming convention and enforce it via SQLFluff. Recommended: `idx_<table>_<cols>`, `uq_<table>_<cols>`, `fk_<table>_<ref_table>`, `chk_<table>_<short_desc>`, `pk_<table>`. | Allows instant identification in error messages and monitoring. | **Medium** |
| 7.4 | Add `COMMENT ON TABLE` / `COMMENT ON COLUMN` / `COMMENT ON CONSTRAINT` for every object in the schema. Tag PII per §8.1. | Surfaces documentation in tooling, IDEs, and data catalogs. | **Medium** |
| 7.5 | Maintain an up-to-date ERD regenerated from the live schema (e.g., SchemaSpy, dbml). | Visual references accelerate understanding. | **Medium** |

---

## 8. Compliance & Data Protection

| # | Rule | Rationale | Severity |
|---|------|-----------|----------|
| 8.1 | Tag every PII/PHI column via `COMMENT ON COLUMN` with a controlled vocabulary (e.g., `pii:email`, `pii:gov_id`, `phi:diagnosis`, `pci:pan`). Maintain a metadata view that surfaces all tagged columns. | Enables automated discovery for DSAR (data subject access requests), masking, and audit. | **High** |
| 8.2 | Use cloud-provider at-rest encryption (RDS/Aurora/Cloud SQL/Azure managed keys) for the storage layer, and `pgcrypto` or app-side envelope encryption with KMS for column-level encryption. **PostgreSQL has no native TDE.** | Storage-level encryption protects backups and disposed media; column-level protects against logical access. | **Critical** |
| 8.3 | For GDPR right-to-erasure on append-only / immutable evidence tables, implement crypto-shredding (per-subject KEK destruction) rather than row deletion. | Preserves audit invariants while honoring the legal request. | **High** |
| 8.4 | In regulated environments, configure `pgaudit.log = 'role, ddl, write'` minimum and ship logs off-host within 24 hours to a tamper-evident store. | Forensic trail for security incidents and change management. | **High** |
| 8.5 | Enforce data-residency boundaries at the schema, partition, or database level when multi-region storage is regulated. Mixing residencies in one table makes lawful-basis arguments hard. | Regulatory clarity. | **High** |
| 8.6 | Use row-level security (RLS) for multi-tenant databases instead of relying on application-side filters. Test RLS policies with a non-superuser test role in CI. | Prevents data leakage even if the app has a logic bug; superusers bypass RLS by default. | **High** |
| 8.7 | Apply the principle of least privilege: application roles get only `SELECT`/`INSERT`/`UPDATE`/`DELETE` on the specific tables they need. Never grant `SUPERUSER` or `BYPASSRLS` to application roles. | Limits blast radius if credentials are compromised. | **Critical** |
| 8.8 | Never store plaintext passwords or API keys in any column; use Argon2id (preferred) or bcrypt (cost ≥ 12) for passwords, and store API keys as salted SHA-256 with a prefix shown to the user. | Plaintext credentials in a breach expose all users. | **Critical** |

---

## 9. Audit & Temporal Patterns

| # | Rule | Rationale | Severity |
|---|------|-----------|----------|
| 9.1 | Append-only / immutable evidence tables (audit logs, regulatory event records) must reject `UPDATE` and `DELETE` via a `BEFORE UPDATE OR DELETE` trigger that raises an exception (e.g., `reject_mutation()`). Grant only `INSERT, SELECT` to the application role. | Defense in depth against accidental and malicious tampering. | **Critical** |
| 9.2 | For temporal/system-versioned data, maintain a `<table>_history` companion populated by a `BEFORE UPDATE OR DELETE` trigger that captures the previous row, the change actor, and the change timestamp. | Enables point-in-time reconstruction without bloating the live table. | **High** |
| 9.3 | Use the `xmin` system column as a cheap optimistic-concurrency token where appropriate (read `xmin` with the row, include it in the `UPDATE ... WHERE xmin = $1` predicate, retry on zero-row update). | No extra column, no extra index, no clock dependency. | **Medium** |
| 9.4 | Distinguish `created_at`/`updated_at` (internal UTC clock, `TIMESTAMPTZ`) from `*_ts` columns (timestamps from external systems, source-of-truth for source semantics). Never overwrite an external `*_ts` with `now()`. | Mixing the two destroys auditability and causes silent reordering. | **High** |
| 9.5 | Soft-delete columns (`deleted_at`, `archived_at`) must be backed by a partial index `WHERE <col> IS NULL` on every hot read path; otherwise the soft-delete pattern is a performance regression. | Without the partial index, every read scans tombstones. | **High** |

---

## 10. Severity Guide and Unified Mapping with the Playbook

| Golden Rules severity | Playbook tier | Meaning | CI gate behavior |
|---|---|---|---|
| **Critical** | **P0** | Data loss, corruption, security breach, or significant performance regression risk. | **Block merge**; fix before deployment. |
| **High** | **P1** | Significant performance / maintenance / compliance impact. | **Warn**; fix within the current sprint. |
| **Medium** | **P2** | Best practice; improves clarity, maintainability, or minor performance. | **Advisory**; document if deferred. |

---

## Quick-Reference Checklists

### Do This (Every Time)

- [ ] Surrogate PK (`BIGINT GENERATED ALWAYS AS IDENTITY`, or UUIDv7 if distributed)
- [ ] On partitioned tables, PK and UNIQUE include partition key
- [ ] `NOT NULL` as the default; NULL only with documented business reason
- [ ] `created_at` and `updated_at` (`TIMESTAMPTZ`, UTC) on every table
- [ ] Foreign keys with explicit `ON DELETE`; `ON UPDATE` only when key is mutable
- [ ] Index on every FK column (`CREATE INDEX CONCURRENTLY`)
- [ ] `UNIQUE` constraint on natural business identifiers (partial UNIQUE for soft-delete)
- [ ] Partial index `WHERE deleted_at IS NULL` on every soft-delete hot path
- [ ] Equality-first composite index column order
- [ ] `EXPLAIN (ANALYZE, BUFFERS)` on new queries against hot tables
- [ ] Migration with `SET lock_timeout` and `SET statement_timeout`
- [ ] `ADD CONSTRAINT ... NOT VALID; VALIDATE CONSTRAINT` for FK/CHECK on large tables
- [ ] `COMMENT ON TABLE` / `COMMENT ON COLUMN` documented; PII tagged
- [ ] Versioned migration file committed and peer-reviewed
- [ ] Append-only / audit tables protected by `reject_mutation` trigger

### Never Do This

- [ ] Store comma-separated values in a column
- [ ] Use `FLOAT`/`DOUBLE` for monetary values
- [ ] Use `SERIAL`/`BIGSERIAL` for new tables (use IDENTITY)
- [ ] Use `varchar(n)` for storage savings in PostgreSQL (no savings exist)
- [ ] Use UUIDv4 (`gen_random_uuid()`) as a B-tree PK on a hot table without considering locality
- [ ] Apply ad-hoc DDL directly in production
- [ ] Run a migration without `SET lock_timeout`
- [ ] Use `SELECT *` in application queries
- [ ] Index a boolean column on its own (use partial index instead)
- [ ] Skip `ON DELETE` action on foreign keys
- [ ] Store plaintext passwords or secrets
- [ ] Grant `SUPERUSER` or `BYPASSRLS` to an application role
- [ ] Add `NOT NULL` to a large column without the `NOT VALID` CHECK trick
- [ ] Add a FK or CHECK to a large table without `NOT VALID; VALIDATE CONSTRAINT`
- [ ] Run a single monolithic `UPDATE` over millions of rows for a backfill
- [ ] Denormalize without measured top-20 evidence from `pg_stat_statements`
- [ ] Mix naming conventions within the same schema
- [ ] Treat `EXPLAIN ANALYZE` as free — it executes the statement

---

*See also:* [`sql-performance-audit-playbook.md`](sql-performance-audit-playbook.md) for the audit-time companion (P0/P1/P2 gates, profiling workflow, CI gates, scoring rubric).
