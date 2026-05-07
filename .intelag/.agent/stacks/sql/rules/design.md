---
id: stacks.sql.rules.design
genre: convention
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
doc_version: 2.0.0
---

## Stable Rule IDs

| ID | Severity | Rule |
| --- | --- | --- |
| `stacks.sql.design.naming-snake-case` | `high` | Use consistent snake_case naming for SQL objects. |
| `stacks.sql.design.primary-key-required` | `blocking` | Every table needs a deliberate primary-key strategy. |
| `stacks.sql.design.no-plaintext-secrets` | `blocking` | Do not store plaintext credentials or API keys. |
# SQL Design Rules

Merged canonical file. Source sections are preserved for editorial de-duplication and rule-ID assignment.

## Source: instructions/sql/Golden Rules for SQL Database Design.md

## Section Break
doc_version: 1.0.0
## Section Break
# Golden Rules for SQL Database Design

**A Senior Engineer's Rubric** · Version 1.0 · 2026

*Schema Design · Normalization · Indexing · Relationships · Performance*

## Section Break
## 1. Table & Schema Design

Well-structured tables form the foundation of a performant, maintainable database. These rules ensure clarity, consistency, and long-term scalability.

| # | Rule | Rationale | Severity |
|---|------|-----------|----------|
| 1.1 | Use singular, lowercase, `snake_case` table names (e.g., `customer_order`, not `CustomerOrders`). | Prevents quoting issues across engines; matches SQL convention. | high |
| 1.2 | Every table must have a single-column surrogate primary key, preferably named `id` or `<table>_id`. | Guarantees row identity; simplifies joins and ORM mapping. | blocking |
| 1.3 | Prefer UUID v7 or `BIGINT IDENTITY/SERIAL` for primary keys over natural keys. | Natural keys change, compound keys complicate joins, and GUIDs are non-sequential. UUIDv7 is time-sortable. | high |
| 1.4 | Every table must include `created_at` (`TIMESTAMPTZ, DEFAULT NOW()`) and `updated_at` columns. | Enables auditing, debugging, and incremental ETL. | high |
| 1.5 | Default all columns to `NOT NULL` unless NULL carries explicit business meaning. | Eliminates three-value logic bugs and enables index efficiency. | blocking |
| 1.6 | Use the smallest appropriate data type: `INT` vs `BIGINT`, `VARCHAR(n)` vs `TEXT`, `DATE` vs `TIMESTAMP`. | Reduces page size, improves cache hit ratio, and shrinks indexes. | medium |
| 1.7 | Store monetary values as `DECIMAL/NUMERIC` with explicit precision (e.g., `DECIMAL(19,4)`), never `FLOAT`. | Floating-point rounding causes financial discrepancies. | blocking |
| 1.8 | Store all timestamps in UTC (`TIMESTAMPTZ`). Convert to local time only at the application layer. | Avoids DST ambiguity and cross-timezone comparison bugs. | high |
| 1.9 | Add `CHECK` constraints for domain validation (e.g., `CHECK (status IN ('active','inactive'))`, `CHECK (price >= 0)`). | Catches invalid data at the engine level before it corrupts downstream. | medium |
| 1.10 | Avoid storing derived/calculated data unless materialized for proven performance reasons. | Keeps a single source of truth; eliminates stale calculation risk. | medium |

> **💡 Pro Tip:** If you find yourself adding a column named `type` or `kind` that changes the meaning of other columns, consider splitting into separate tables (table-per-type) or using PostgreSQL's table inheritance.

## Section Break
## 2. Normalization & Denormalization

Normalize first for correctness, then selectively denormalize with measurement-backed justification.

| # | Rule | Rationale | Severity |
|---|------|-----------|----------|
| 2.1 | Achieve at least Third Normal Form (3NF) for all OLTP tables before considering denormalization. | Eliminates update/insert/delete anomalies. | blocking |
| 2.2 | Never store comma-separated values or JSON arrays when a junction table is appropriate. | Destroys referential integrity, prevents indexing, and blocks joins. | blocking |
| 2.3 | Denormalize only after profiling shows a measurable bottleneck; document every denormalization with a dated comment. | Premature denormalization creates technical debt with no measured payoff. | high |
| 2.4 | When denormalizing, use materialized views or summary tables—not duplicated columns—where the engine supports them. | Materialized views can be refreshed atomically; columns can drift. | medium |
| 2.5 | If you store JSON/JSONB, define a JSON Schema or `CHECK` constraint to enforce structure. | Unvalidated JSON becomes a shadow schema no one understands. | medium |
| 2.6 | Eliminate transitive dependencies: a non-key column should never depend on another non-key column. | Prevents update anomalies (e.g., updating a zip code without its city). | high |

## Section Break
## 3. Relationships & Referential Integrity

Foreign keys are contracts between tables. Treat them with the same rigor as application-level validation.

| # | Rule | Rationale | Severity |
|---|------|-----------|----------|
| 3.1 | Every foreign key must have an explicit `REFERENCES` clause with `ON DELETE` and `ON UPDATE` actions. | Missing cascade/restrict rules leave orphan records or silent failures. | blocking |
| 3.2 | Name foreign key columns as `<referenced_table>_id` (e.g., `customer_id` references `customer.id`). | Self-documenting; enables automated ORM discovery. | high |
| 3.3 | Use junction/bridge tables for many-to-many relationships; never embed arrays of IDs. | Preserves referential integrity and enables efficient querying. | blocking |
| 3.4 | Choose `ON DELETE` actions deliberately: `RESTRICT` for strong entities, `CASCADE` for owned children, `SET NULL` for optional associations. | Wrong action either blocks operations or silently destroys data. | blocking |
| 3.5 | Always index foreign key columns (most engines except MySQL/InnoDB do not auto-index them). | Unindexed FKs cause full table scans on `DELETE`/`UPDATE` of the parent row. | high |
| 3.6 | Use composite foreign keys only when the referenced table has a composite PK; prefer surrogate keys otherwise. | Reduces key width, join complexity, and migration difficulty. | medium |
| 3.7 | For soft-delete patterns, add a `deleted_at TIMESTAMPTZ` column instead of removing rows; maintain FK integrity. | Allows auditability and undo; avoids orphan cascades. | medium |
| 3.8 | Self-referencing FKs (e.g., `employee.manager_id → employee.id`) must allow `NULL` for root nodes. | Prevents a chicken-and-egg insertion problem. | medium |

## Section Break
## 4. Indexing Strategy

Indexes are the single largest lever for read performance. Every index also carries a write penalty—balance accordingly.

| # | Rule | Rationale | Severity |
|---|------|-----------|----------|
| 4.1 | Index every column used in `WHERE`, `JOIN ON`, and `ORDER BY` clauses of frequent queries. | Without an index, the engine falls back to sequential scans. | blocking |
| 4.2 | Create composite indexes with the highest-selectivity column first, followed by range/sort columns. | Column order determines whether the index can satisfy the query plan. | blocking |
| 4.3 | Never index columns with very low cardinality (e.g., boolean flags) in isolation—combine with a selective column. | A standalone low-cardinality index is rarely chosen by the optimizer. | high |
| 4.4 | Use `INCLUDE` (covering index) columns to enable index-only scans for read-heavy queries. | Avoids expensive heap lookups by serving data directly from the index. | medium |
| 4.5 | Use partial/filtered indexes for queries that target a subset (e.g., `WHERE status = 'active'`). | Dramatically smaller index; faster scans and lower maintenance. | medium |
| 4.6 | Add `UNIQUE` indexes/constraints for business-level uniqueness (email, SKU, etc.) beyond the PK. | Prevents duplicate data at the engine level, not just the app level. | blocking |
| 4.7 | Limit total indexes per table to roughly 5–7 on write-heavy tables; each index adds `INSERT`/`UPDATE` overhead. | Write amplification degrades throughput and increases WAL volume. | high |
| 4.8 | Audit unused indexes monthly (`pg_stat_user_indexes`, `sys.dm_db_index_usage_stats`) and drop them. | Dead indexes consume storage, slow writes, and bloat backups. | medium |
| 4.9 | Use expression/functional indexes for computed predicates (e.g., `INDEX ON LOWER(email)`). | Allows the optimizer to use the index when the query applies the same function. | medium |
| 4.10 | Use GIN/GiST indexes for full-text search, JSONB, and array containment queries (PostgreSQL). | B-tree cannot satisfy containment or text-search operators. | medium |

> **💡 Index Audit Query (PostgreSQL):**
> ```sql
> SELECT relname, indexrelname, idx_scan
> FROM pg_stat_user_indexes
> WHERE idx_scan = 0 AND schemaname = 'public'
> ORDER BY pg_relation_size(indexrelid) DESC;
> ```

## Section Break
## 5. Performance & Scaling Patterns

Design for the query patterns you know today while keeping the door open for the scale you may need tomorrow.

| # | Rule | Rationale | Severity |
|---|------|-----------|----------|
| 5.1 | Design tables for read patterns: if a query always needs columns A, B, C together, ensure they share an index. | Aligning physical storage with access patterns minimizes I/O. | high |
| 5.2 | Partition large tables (100M+ rows) by range, list, or hash—based on actual query filters. | Partition pruning eliminates scanning irrelevant data. | high |
| 5.3 | Use connection pooling (PgBouncer, ProxySQL) and avoid long-held transactions in OLTP systems. | Idle connections consume memory and can cause lock contention. | high |
| 5.4 | Never use `SELECT *`; always specify required columns. | Reduces I/O, prevents index-only scan invalidation, and avoids schema-change surprises. | high |
| 5.5 | Avoid correlated subqueries in `SELECT`/`WHERE`; rewrite as JOINs or lateral joins. | Correlated subqueries execute per-row; joins execute set-based. | medium |
| 5.6 | Use `EXPLAIN ANALYZE` on every new query before deploying to production. | Reveals sequential scans, nested loops, and sort spills before users feel them. | blocking |
| 5.7 | Implement read replicas for reporting/analytics queries; never run analytics on the primary. | Offloads read traffic and prevents long-running queries from blocking writes. | high |
| 5.8 | Set `statement_timeout` / `lock_timeout` at the session or role level to prevent runaway queries. | A single bad query can exhaust all connections and cascade into downtime. | medium |

## Section Break
## 6. Naming Conventions & Documentation

A schema that explains itself reduces onboarding time and prevents misinterpretation.

| # | Rule | Rationale | Severity |
|---|------|-----------|----------|
| 6.1 | Use consistent naming: `snake_case` for tables/columns, `PascalCase` only if the project-wide standard demands it. | Mixing conventions causes quoting errors and slows development. | high |
| 6.2 | Prefix boolean columns with `is_`, `has_`, or `can_` (e.g., `is_active`, `has_verified_email`). | Self-documenting; removes ambiguity about the column's intent. | medium |
| 6.3 | Name indexes as `idx_<table>_<columns>` and constraints as `chk_`, `uq_`, `fk_` prefixes. | Allows instant identification in error messages and monitoring. | medium |
| 6.4 | Add `COMMENT ON TABLE` / `COMMENT ON COLUMN` for every object in the schema. | Surfaces documentation in IDE tooltips and data catalog tools. | medium |
| 6.5 | Maintain versioned migration files (Flyway, Liquibase, Alembic)—never apply ad-hoc DDL in production. | Ensures reproducibility, rollback capability, and audit history. | blocking |
| 6.6 | Keep an up-to-date ERD (entity-relationship diagram) in the repository, regenerated from the live schema. | Visual references accelerate understanding for new team members. | medium |

## Section Break
## 7. Security & Access Control

The database is the last line of defense. Assume the application layer will eventually have a bug.

| # | Rule | Rationale | Severity |
|---|------|-----------|----------|
| 7.1 | Apply the principle of least privilege: application roles should have only `SELECT`/`INSERT`/`UPDATE` on needed tables. | Limits blast radius if credentials are compromised. | blocking |
| 7.2 | Never store plaintext passwords or API keys in any column; use hashed values with bcrypt/argon2. | Plaintext credentials in a breach expose all users. | blocking |
| 7.3 | Encrypt PII columns at rest (TDE or column-level encryption) and mask them in non-production environments. | Regulatory compliance (GDPR, HIPAA) and breach risk reduction. | high |
| 7.4 | Use row-level security (RLS) for multi-tenant databases instead of filtering in the application. | Prevents data leakage even if the app has a logic bug. | high |
| 7.5 | Audit all DDL changes and privileged DML with `pg_audit` or equivalent engine feature. | Creates a forensic trail for security incidents and change management. | medium |

## Section Break
## Quick-Reference Checklist

###  Do This (Every Time)

- [ ] Surrogate PK (`BIGINT` or UUIDv7) on every table
- [ ] `NOT NULL` as the default; NULL only with documented business reason
- [ ] `created_at` and `updated_at` on every table
- [ ] Foreign keys with explicit `ON DELETE` / `ON UPDATE`
- [ ] Index on every FK column
- [ ] `UNIQUE` constraint on natural business identifiers
- [ ] `EXPLAIN ANALYZE` on every new or modified query
- [ ] Migration file committed and peer-reviewed
- [ ] `COMMENT ON TABLE` and `COMMENT ON COLUMN` documented
- [ ] Timestamps stored in UTC (`TIMESTAMPTZ`)

### ❌ Never Do This

- [ ] Store comma-separated values or arrays of IDs in a column
- [ ] Use `FLOAT`/`DOUBLE` for monetary values
- [ ] Apply ad-hoc DDL directly in production
- [ ] Use `SELECT *` in application queries
- [ ] Index a boolean column on its own
- [ ] Skip `ON DELETE` action on foreign keys
- [ ] Store plaintext passwords or secrets
- [ ] Denormalize without a profiled performance justification
- [ ] Leave unused indexes accumulating
- [ ] Mix naming conventions within the same schema

## Section Break
## Severity Guide

| Severity | Meaning | Action Required |
|----------|---------|-----------------|
| blocking | Violation causes data loss, corruption, or security breach. | Must fix before merge; blocks deployment. |
| high | Causes significant performance degradation or maintenance burden. | Must fix within the current sprint. |
| medium | Best practice; improves clarity, maintainability, or minor performance. | Should fix; acceptable to defer with documented reason. |

## Source: instructions/sql/Golden Rules for SQL Database Design_v1.md

## Section Break
doc_version: 1.0.0
## Section Break
# Golden Rules for SQL Database Design

**A Senior Engineer's Rubric** · Version 2.0 · 2026

*Schema Design · Normalization · Indexing · Relationships · Performance · Compliance · Audit*

> **Scope:** Design-time rules. Engine-leaning toward PostgreSQL 16+ where noted, otherwise portable across PG/MySQL/SQLite.
> **Companion:** Audit/operations rules live in [`sql-performance-audit-playbook.md`](sql-performance-audit-playbook.md). Cross-references use `[PB Pn-m]`.
> **Severity model:** blocking / high / medium — unified with the Playbook's P0 / P1 / P2 (see §10).

## Section Break
## 1. Table & Schema Design

Well-structured tables form the foundation of a performant, maintainable database.

| # | Rule | Rationale | Severity |
|---|------|-----------|----------|
| 1.1 | Use singular, lowercase, `snake_case` table names (e.g., `customer_order`, not `CustomerOrders`). | Prevents quoting issues across engines; matches SQL convention. | high |
| 1.2 | Every **non-partitioned** table must have a single-column surrogate primary key. On **partitioned** tables, the PK and every UNIQUE constraint must include all partition-key columns (see 1.14). | PostgreSQL cannot enforce uniqueness across partitions otherwise. | blocking |
| 1.3 | Prefer `BIGINT GENERATED ALWAYS AS IDENTITY` (PG10+, SQL-standard) for surrogate PKs. Use UUIDs only when distributed generation or opacity is required; UUIDv4 (`gen_random_uuid()`) hurts B-tree locality, so prefer UUIDv7 via app-side generation or the `pg_uuidv7` extension when chosen. Avoid `SERIAL`/`BIGSERIAL` for new tables. | Identity columns avoid implicit sequence-ownership bugs; UUIDv4 PKs cause page-split write amplification on hot tables. | high |
| 1.4 | Every table must include `created_at TIMESTAMPTZ NOT NULL DEFAULT now()` and `updated_at TIMESTAMPTZ NOT NULL DEFAULT now()`. | Enables auditing, debugging, and incremental ETL. | high |
| 1.5 | Default all columns to `NOT NULL` unless NULL carries explicit business meaning. | Eliminates three-valued-logic bugs, improves planner cardinality estimates, and makes `IS NOT NULL` predicates unnecessary. | blocking |
| 1.6 | Choose the smallest correct data type for the domain. In PostgreSQL, prefer `text` over `varchar(n)` (see 1.12); choose `int` vs `bigint` and `date` vs `timestamptz` based on the domain, not perceived storage savings. | Smaller types reduce page count for fixed-width columns and shrink indexes. | medium |
| 1.7 | Never store monetary values in `FLOAT`/`DOUBLE`. Use `numeric(p,s)` with precision derived from the largest legal value × 10ˢ, **or** integer minor units (cents as `BIGINT`) on hot OLTP paths (see 1.15). | Floating-point rounding causes financial discrepancies; integer arithmetic is branch-free and indexable. | blocking |
| 1.8 | Store all timestamps as `TIMESTAMPTZ` in UTC. Convert to local time only at the application or report layer. | Avoids DST ambiguity and cross-timezone comparison bugs. | high |
| 1.9 | Add `CHECK` constraints for domain validation (e.g., `CHECK (status IN ('active','inactive'))`, `CHECK (price >= 0)`). On large tables, add as `NOT VALID` then `VALIDATE CONSTRAINT` to avoid an `ACCESS EXCLUSIVE` scan ([PB P0-16]). | Catches invalid data at the engine level. | medium |
| 1.10 | Avoid storing derived/calculated data unless materialized for proven performance reasons. Prefer `GENERATED ALWAYS AS (expr) STORED` over application-managed duplicates (see 1.13). | Keeps a single source of truth; eliminates stale-calculation drift. | medium |
| 1.11 | Use `BIGINT GENERATED ALWAYS AS IDENTITY` instead of `SERIAL`/`BIGSERIAL` for new tables (PG10+). | Identity columns are SQL-standard, avoid implicit sequence-ownership bugs, and prevent direct sequence manipulation. | high |
| 1.12 | In PostgreSQL, prefer `text` over `varchar(n)` unless a length cap is a real domain rule. | `text`, `varchar`, and `varchar(n)` share identical `varlena` storage; `varchar(n)` only adds a CHECK that can break online migrations when raised. | medium |
| 1.13 | Use `GENERATED ALWAYS AS (expr) STORED` (PG12+) for derived columns that are read often and computed deterministically. | Materializes once on write; eligible for indexing; eliminates application drift. | medium |
| 1.14 | For partitioned tables, the primary key and every UNIQUE constraint must include all partition-key columns. | PostgreSQL cannot enforce uniqueness across partitions otherwise ([PB P1-2]). | blocking |
| 1.15 | Prefer integer minor units (e.g., cents as `BIGINT`) for money on hot OLTP paths; reserve `numeric(p,s)` for tax/FX/aggregate calculations where fractional units are required. | Integer arithmetic is branch-free and indexable; `numeric` is arbitrary-precision and ~10× slower. | high |
| 1.16 | Use `CITEXT` (extension) for case-insensitive UNIQUE columns such as email; otherwise enforce via a unique expression index on `lower(col)`. | Avoids duplicate-by-case bugs; documents the case-insensitive semantic at the type level. | medium |
| 1.17 | Use `tstzrange` / `daterange` + `EXCLUDE USING gist (resource_id WITH =, period WITH &&)` for non-overlap constraints (bookings, shifts, schedules). | Engine-enforced; eliminates an entire class of double-booking races. | high |
| 1.18 | Use `DEFERRABLE INITIALLY DEFERRED` foreign keys only when cyclic insert ordering or end-of-transaction validation is genuinely required. | Otherwise pay the deferred-constraint queue cost without benefit. | medium |
| 1.19 | Use `INET`/`CIDR` for IP addresses, `MACADDR` for MAC addresses, `tsvector` for full-text search columns, and `uuid` as a first-class type — never `text` substitutes. | Native types provide validation, indexable operators, and accurate storage. | medium |

> **Pro Tip:** If you find yourself adding a column named `type` or `kind` that changes the meaning of other columns, consider splitting into separate tables (table-per-type) or using a discriminated polymorphism pattern. PostgreSQL table inheritance is rarely the right tool because it does not enforce uniqueness or FKs across children.

## Section Break
## 2. Normalization & Denormalization

Normalize first for correctness, then selectively denormalize with measurement-backed justification.

| # | Rule | Rationale | Severity |
|---|------|-----------|----------|
| 2.1 | Achieve at least Third Normal Form (3NF) for all OLTP tables before considering denormalization. | Eliminates update/insert/delete anomalies. | blocking |
| 2.2 | Never store comma-separated values when a junction table is appropriate. **Native arrays / JSONB arrays are acceptable** when (a) the array represents an entity's own value (tags, labels, search terms), (b) elements are not first-class entities with their own attributes, and (c) you do not need engine-enforced FK integrity on each element. Use a junction table for relationships to first-class entities. | CSV-in-text destroys integrity and indexing; native arrays + GIN are a legitimate PG pattern. | blocking |
| 2.3 | Denormalize only when (a) the slow query is in the top-20 by total time in `pg_stat_statements`, (b) p95 exceeds the documented SLO, **and** (c) indexing/query rewrite alternatives have been EXPLAIN-tested and rejected. Document the dated trigger metric in a `COMMENT ON TABLE`. | Premature denormalization creates technical debt with no measured payoff. | high |
| 2.4 | When denormalizing, prefer materialized views, summary tables, or `GENERATED ... STORED` columns over duplicated free-form columns. | Refreshable atomically; columns can drift silently. | medium |
| 2.5 | If you store JSON/JSONB, define a JSON Schema or `CHECK (jsonb_matches_schema(...))` constraint to enforce structure. Always use `jsonb`, never `json`, for stored data. | Unvalidated JSON becomes a shadow schema no one understands; `json` re-parses on every read. | medium |
| 2.6 | Eliminate transitive dependencies: a non-key column should never depend on another non-key column. | Prevents update anomalies. | high |

## Section Break
## 3. Relationships & Referential Integrity

Foreign keys are contracts between tables.

| # | Rule | Rationale | Severity |
|---|------|-----------|----------|
| 3.1 | Every foreign key must declare an explicit `ON DELETE` action. `ON UPDATE` is required only when the referenced key is mutable (rare with surrogate PKs). | Missing cascade/restrict rules leave orphan records or silent failures. | blocking |
| 3.2 | Name foreign key columns as `<referenced_table>_id`. | Self-documenting; enables automated ORM discovery. | high |
| 3.3 | Use junction/bridge tables for many-to-many relationships between first-class entities. | Preserves referential integrity and enables efficient querying. | blocking |
| 3.4 | Choose `ON DELETE` actions deliberately: `RESTRICT`/`NO ACTION` for strong entities, `CASCADE` for owned children, `SET NULL` for optional associations. Document the choice in `COMMENT ON CONSTRAINT`. | Wrong action either blocks operations or silently destroys data. | blocking |
| 3.5 | Always index foreign key columns. PostgreSQL does not auto-index them; deletes/updates on the referenced table scan the referencing table without an index. | Unindexed FKs cause `O(n)` scans on parent DELETE/UPDATE. | blocking |
| 3.6 | Use composite foreign keys only when the referenced table has a composite PK; prefer surrogate keys otherwise. Composite FKs are correct when they reinforce a multi-column invariant (e.g., tenant-scoping). | Reduces key width and join complexity, but composites are required to prevent cross-tenant leakage. | high |
| 3.7 | For soft-delete patterns, add a `deleted_at TIMESTAMPTZ` column and back hot read paths with a partial index `WHERE deleted_at IS NULL`. Keep FK integrity intact. | Allows auditability and undo without paying the index cost on tombstones. | high |
| 3.8 | Self-referencing FKs (e.g., `employee.manager_id → employee.id`) must allow `NULL` for root nodes. | Prevents a chicken-and-egg insertion problem. | medium |
| 3.9 | Add new FKs and CHECKs on large tables with `ADD CONSTRAINT ... NOT VALID` followed by `VALIDATE CONSTRAINT` ([PB P0-16]). | The two-phase form takes a brief lock, then validates under `SHARE UPDATE EXCLUSIVE` instead of `ACCESS EXCLUSIVE`. | high |

## Section Break
## 4. Indexing Strategy

Indexes are the single largest lever for read performance. Every index also carries a write penalty.

| # | Rule | Rationale | Severity |
|---|------|-----------|----------|
| 4.1 | Index columns used in predicates of queries that appear in the top-50 of `pg_stat_statements` by `total_exec_time`. Composite predicates take a single composite index ([PB P0-6]), not one index per column. | Aligns indexing effort with measured workload. | blocking |
| 4.2 | Compose composite indexes as **equality predicates first, then one range or sort column**. Column order determines whether the planner can use the index for filtering and sort elimination. | A high-selectivity column used only with `>=` should not lead. | blocking |
| 4.3 | Don't create a standalone single-column index on a low-cardinality column. **Do** create a partial index keyed on the low-cardinality predicate (e.g., `CREATE INDEX ... WHERE is_active = true`) or include it as a leading column in a composite index that covers the whole hot query. | A standalone boolean index is rarely chosen; a partial index on the same column is one of the highest-value index types. | high |
| 4.4 | Use `INCLUDE` (covering index) columns to enable index-only scans for read-heavy queries. Verify with EXPLAIN that the plan reports `Index Only Scan` and that heap-fetch counts are low (depends on visibility-map state). | Avoids heap lookups; falls back to heap fetches when visibility bits aren't set. | high |
| 4.5 | Use partial/filtered indexes for queries that target a row subset (`WHERE deleted_at IS NULL`, `WHERE status = 'active'`). | Dramatically smaller index; faster scans and lower maintenance. | high |
| 4.6 | Add `UNIQUE` constraints/indexes for business-level uniqueness (email, SKU, etc.) beyond the PK. For soft-delete tables, use `CREATE UNIQUE INDEX ... WHERE deleted_at IS NULL` to allow re-creation after deletion. | Prevents duplicate data at the engine level. | blocking |
| 4.7 | Each index increases write amplification and WAL volume. Justify each index against a measured query and drop indexes with `idx_scan = 0` after a representative observation window (≥ 30 days, including monthly batch jobs). There is no fixed numerical cap. | Workload-relative, not anecdote-relative. | high |
| 4.8 | Audit unused indexes with `pg_stat_user_indexes` on a defined cadence and drop them. | Dead indexes consume storage, slow writes, and bloat backups. | medium |
| 4.9 | Use expression/functional indexes for computed predicates (e.g., `CREATE INDEX ON users (lower(email))`). The query must apply the same expression to use it. | Otherwise the planner cannot match the predicate to the index. | high |
| 4.10 | Match the PostgreSQL index type to the operator class: B-tree for equality/range on scalar types; **GIN** for JSONB containment, full-text search, and array `@>`; **GiST** for ranges, geometry, and exclusion constraints; **BRIN** for very large append-mostly tables with physical correlation (e.g., time-series); **SP-GiST** for non-balanced data; Hash only for equality lookups where you don't need ordering. | Wrong index type = full scan. | high |
| 4.11 | Build indexes on production with `CREATE INDEX CONCURRENTLY` (cannot be wrapped in a transaction; takes longer; can leave invalid index on failure that must be `DROP INDEX CONCURRENTLY`'d and rebuilt). | Avoids blocking writes during build. | blocking |

> **Index Audit Query (PostgreSQL):**
> ```sql
> SELECT schemaname, relname, indexrelname, idx_scan,
>        pg_size_pretty(pg_relation_size(indexrelid)) AS size
> FROM pg_stat_user_indexes
> WHERE idx_scan = 0
>   AND indexrelname NOT LIKE '%_pkey'
> ORDER BY pg_relation_size(indexrelid) DESC;
> ```

## Section Break
## 5. Performance & Scaling Patterns

| # | Rule | Rationale | Severity |
|---|------|-----------|----------|
| 5.1 | Design tables for read patterns: if a query always needs columns A, B, C together, ensure they share an index (consider `INCLUDE` for non-key payload). | Aligning physical storage with access patterns minimizes I/O. | high |
| 5.2 | Consider partitioning when **(a)** one table exceeds `shared_buffers`, AND **(b)** queries filter or retention deletes target a clear partition key. Row count alone is not a trigger. | Partitioning is operational overhead; pruning has to be real. | high |
| 5.3 | Use connection pooling (PgBouncer, RDS Proxy, pgcat) and avoid long-held transactions in OLTP systems. Idle-in-transaction sessions hold locks and block vacuum ([PB P0-12]). | Idle connections consume memory and cause contention. | high |
| 5.4 | Never use `SELECT *` in application code; project only the columns required. | Reduces I/O, prevents index-only scan invalidation, avoids TOAST/SERIALIZE cost ([PB P0-10]), and prevents schema-change surprises. | high |
| 5.5 | Avoid correlated subqueries in `SELECT`/`WHERE`; rewrite as `JOIN`s, lateral joins, or window functions. | Correlated subqueries execute per row; joins execute set-based. | medium |
| 5.6 | Run `EXPLAIN (ANALYZE, BUFFERS)` on every new query touching a hot table or returning > 100 rows. For destructive statements, wrap in `BEGIN; … ROLLBACK;`. `EXPLAIN ANALYZE` executes the statement and adds non-trivial timing overhead. | Reveals sequential scans, nested loops, sort spills, and bad cardinality before users feel them. | high |
| 5.7 | Implement read replicas for reporting/analytics queries; never run analytics on the primary. Set `hot_standby_feedback` only after weighing the bloat/cancellation tradeoff ([PB P1-6]). | Offloads read traffic and prevents long-running queries from blocking writes. | high |
| 5.8 | Set `statement_timeout`, `lock_timeout`, and `idle_in_transaction_session_timeout` as role-level defaults, and override with `SET LOCAL` per transaction for batch jobs and migrations ([PB P0-12]). | A single bad query can exhaust connections and cascade into downtime. | blocking |
| 5.9 | Use `MERGE` (PG15+) for standards-compliant multi-row idempotent ETL where `INSERT ... ON CONFLICT` cannot express `WHEN NOT MATCHED BY SOURCE`. For single-row upserts, prefer `INSERT ... ON CONFLICT DO UPDATE` ([PB P0-14]). | Right tool for each pattern; `MERGE` is not always atomic in the same way as ON CONFLICT — read the docs. | medium |

## Section Break
## 6. Concurrency, Transactions, and Migration Safety

| # | Rule | Rationale | Severity |
|---|------|-----------|----------|
| 6.1 | Choose isolation per use case: `READ COMMITTED` (default) for general OLTP, `REPEATABLE READ` for multi-statement reports, `SERIALIZABLE` (SSI) when the application can correctly retry on `40001` serialization failures. Document the choice and the retry contract. | Higher isolation costs throughput; without retry, `SERIALIZABLE` will surface as user-visible errors. | high |
| 6.2 | Use `SELECT ... FOR UPDATE SKIP LOCKED` for queue-style consumer patterns and `FOR UPDATE NOWAIT` for try-acquire semantics. | Avoids consumer pile-up; explicit failure beats silent blocking. | high |
| 6.3 | Use `pg_advisory_xact_lock(key)` for application-level mutexes (singleton jobs, migration leader election) — never `LOCK TABLE`. | Advisory locks are cheap, transaction-scoped, and don't block DDL. | medium |
| 6.4 | Acquire locks in a deterministic order across the codebase to prevent deadlocks. Set `deadlock_timeout` (default 1 s) appropriately and instrument deadlock retries at the application layer. | Eliminates the most common preventable production deadlock class. | high |
| 6.5 | All migrations must run with `SET lock_timeout` (≤ 5 s) and a bounded `SET statement_timeout`, with retry-on-`55P03` (lock_not_available). Never run an unbounded `ALTER TABLE` against production ([PB P0-15]). | A blocked `ALTER TABLE` holds `ACCESS EXCLUSIVE` and queues every subsequent query. | blocking |
| 6.6 | Maintain versioned migration files (Flyway, Liquibase, Alembic, sqitch). Never apply ad-hoc DDL in production. | Reproducibility, rollback, audit history. | blocking |
| 6.7 | Use the **expand → migrate → contract** pattern for renames and breaking type changes: add new column → backfill in keyset-paged batches → dual-write → cut reads → drop old ([PB P0-18]). | In-place `RENAME`/`ALTER COLUMN ... TYPE` requires app dual-read or full-table rewrite. | blocking |
| 6.8 | To add `NOT NULL` to a large existing column, first add `CHECK (col IS NOT NULL) NOT VALID`, then `VALIDATE CONSTRAINT`, then `SET NOT NULL` (PG12+ uses the validated CHECK as proof and avoids a full scan), then drop the helper CHECK ([PB P0-17]). | Naive `SET NOT NULL` requires `ACCESS EXCLUSIVE` for the duration of a full table scan. | high |
| 6.9 | Backfills must be batched (keyset pagination, `LIMIT N`, commit between batches), not single-statement `UPDATE`s over millions of rows. | A monolithic UPDATE holds row locks, generates massive WAL, and balloons dead tuples. | high |
| 6.10 | DDL lock-strength matrix: `ADD COLUMN` with constant default is metadata-only since PG11; `ALTER COLUMN ... TYPE` usually rewrites the table; `ADD FOREIGN KEY` takes `SHARE ROW EXCLUSIVE` on both tables; `CREATE INDEX CONCURRENTLY` takes `SHARE UPDATE EXCLUSIVE`. Know what each migration locks. | Mis-classifying a migration as "fast" is a top outage cause. | high |

## Section Break
## 7. Naming Conventions & Documentation

| # | Rule | Rationale | Severity |
|---|------|-----------|----------|
| 7.1 | Use `snake_case` for tables, columns, indexes, constraints, and schemas. No quoted identifiers unless the project standard demands it. | Mixing conventions causes quoting errors and slows development. | high |
| 7.2 | Prefix boolean columns with `is_`, `has_`, or `can_` (e.g., `is_active`, `has_verified_email`). | Self-documenting; removes ambiguity. | medium |
| 7.3 | Adopt a single index/constraint naming convention and enforce it via SQLFluff. Recommended: `idx_<table>_<cols>`, `uq_<table>_<cols>`, `fk_<table>_<ref_table>`, `chk_<table>_<short_desc>`, `pk_<table>`. | Allows instant identification in error messages and monitoring. | medium |
| 7.4 | Add `COMMENT ON TABLE` / `COMMENT ON COLUMN` / `COMMENT ON CONSTRAINT` for every object in the schema. Tag PII per §8.1. | Surfaces documentation in tooling, IDEs, and data catalogs. | medium |
| 7.5 | Maintain an up-to-date ERD regenerated from the live schema (e.g., SchemaSpy, dbml). | Visual references accelerate understanding. | medium |

## Section Break
## 8. Compliance & Data Protection

| # | Rule | Rationale | Severity |
|---|------|-----------|----------|
| 8.1 | Tag every PII/PHI column via `COMMENT ON COLUMN` with a controlled vocabulary (e.g., `pii:email`, `pii:gov_id`, `phi:diagnosis`, `pci:pan`). Maintain a metadata view that surfaces all tagged columns. | Enables automated discovery for DSAR (data subject access requests), masking, and audit. | high |
| 8.2 | Use cloud-provider at-rest encryption (RDS/Aurora/Cloud SQL/Azure managed keys) for the storage layer, and `pgcrypto` or app-side envelope encryption with KMS for column-level encryption. **PostgreSQL has no native TDE.** | Storage-level encryption protects backups and disposed media; column-level protects against logical access. | blocking |
| 8.3 | For GDPR right-to-erasure on append-only / immutable evidence tables, implement crypto-shredding (per-subject KEK destruction) rather than row deletion. | Preserves audit invariants while honoring the legal request. | high |
| 8.4 | In regulated environments, configure `pgaudit.log = 'role, ddl, write'` minimum and ship logs off-host within 24 hours to a tamper-evident store. | Forensic trail for security incidents and change management. | high |
| 8.5 | Enforce data-residency boundaries at the schema, partition, or database level when multi-region storage is regulated. Mixing residencies in one table makes lawful-basis arguments hard. | Regulatory clarity. | high |
| 8.6 | Use row-level security (RLS) for multi-tenant databases instead of relying on application-side filters. Test RLS policies with a non-superuser test role in CI. | Prevents data leakage even if the app has a logic bug; superusers bypass RLS by default. | high |
| 8.7 | Apply the principle of least privilege: application roles get only `SELECT`/`INSERT`/`UPDATE`/`DELETE` on the specific tables they need. Never grant `SUPERUSER` or `BYPASSRLS` to application roles. | Limits blast radius if credentials are compromised. | blocking |
| 8.8 | Never store plaintext passwords or API keys in any column; use Argon2id (preferred) or bcrypt (cost ≥ 12) for passwords, and store API keys as salted SHA-256 with a prefix shown to the user. | Plaintext credentials in a breach expose all users. | blocking |

## Section Break
## 9. Audit & Temporal Patterns

| # | Rule | Rationale | Severity |
|---|------|-----------|----------|
| 9.1 | Append-only / immutable evidence tables (audit logs, regulatory event records) must reject `UPDATE` and `DELETE` via a `BEFORE UPDATE OR DELETE` trigger that raises an exception (e.g., `reject_mutation()`). Grant only `INSERT, SELECT` to the application role. | Defense in depth against accidental and malicious tampering. | blocking |
| 9.2 | For temporal/system-versioned data, maintain a `<table>_history` companion populated by a `BEFORE UPDATE OR DELETE` trigger that captures the previous row, the change actor, and the change timestamp. | Enables point-in-time reconstruction without bloating the live table. | high |
| 9.3 | Use the `xmin` system column as a cheap optimistic-concurrency token where appropriate (read `xmin` with the row, include it in the `UPDATE ... WHERE xmin = $1` predicate, retry on zero-row update). | No extra column, no extra index, no clock dependency. | medium |
| 9.4 | Distinguish `created_at`/`updated_at` (internal UTC clock, `TIMESTAMPTZ`) from `*_ts` columns (timestamps from external systems, source-of-truth for source semantics). Never overwrite an external `*_ts` with `now()`. | Mixing the two destroys auditability and causes silent reordering. | high |
| 9.5 | Soft-delete columns (`deleted_at`, `archived_at`) must be backed by a partial index `WHERE <col> IS NULL` on every hot read path; otherwise the soft-delete pattern is a performance regression. | Without the partial index, every read scans tombstones. | high |

## Section Break
## 10. Severity Guide and Unified Mapping with the Playbook

| Golden Rules severity | Playbook tier | Meaning | CI gate behavior |
|---|---|---|---|
| blocking | **P0** | Data loss, corruption, security breach, or significant performance regression risk. | **Block merge**; fix before deployment. |
| high | **P1** | Significant performance / maintenance / compliance impact. | **Warn**; fix within the current sprint. |
| medium | **P2** | Best practice; improves clarity, maintainability, or minor performance. | **Advisory**; document if deferred. |

## Section Break
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

## Section Break
*See also:* [`sql-performance-audit-playbook.md`](sql-performance-audit-playbook.md) for the audit-time companion (P0/P1/P2 gates, profiling workflow, CI gates, scoring rubric).

## Source: instructions/sql/src/Golden Rules for SQL Database Design.md

## Section Break
doc_version: 1.0.0
## Section Break
# Golden Rules for SQL Database Design

**A Senior Engineer's Rubric** · Version 1.1 · 2026

*Schema Design · Normalization · Indexing · Relationships · Performance · Multi-Schema ORM · Services*

## Section Break
## How to use this document

These rules are **product-agnostic** defaults. Where the Intelag SQL Suite uses a specific convention (PostgreSQL schemas per bounded context, SQLAlchemy `SchemaBase`, YAML/model generators), callouts mark **Intelag alignment** so agents and humans can stay consistent without forking the rubric.

## Section Break
## 1. Table & Schema Design

Well-structured tables form the foundation of a performant, maintainable database. These rules ensure clarity, consistency, and long-term scalability.

| # | Rule | Rationale | Severity |
|---|------|-----------|----------|
| 1.1 | Use lowercase `snake_case` table names. Choose **either** singular **or** plural per product and apply it everywhere (e.g. `customer_order` vs `customer_orders`). | Prevents quoting issues across engines; mixed singular/plural breaks mental models and automation. | high |
| 1.2 | Every table must have a single-column surrogate primary key, preferably `<entity>_id` (or `id` if globally consistent). | Guarantees row identity; simplifies joins and ORM mapping. | blocking |
| 1.3 | Prefer UUID (v4/v7) or `BIGINT IDENTITY/SERIAL` for primary keys over natural keys. | Natural keys change, compound keys complicate joins, and sequential IDs leak volume; UUIDv7 is time-sortable. | high |
| 1.4 | Every table must include `created_at` (`TIMESTAMPTZ`, non-nullable, default now) and `updated_at` (`TIMESTAMPTZ`, maintained on change). | Enables auditing, debugging, and incremental ETL. | high |
| 1.5 | Default all columns to `NOT NULL` unless NULL carries explicit business meaning. | Eliminates three-value logic bugs and enables index efficiency. | blocking |
| 1.6 | Use the smallest appropriate data type: `INT` vs `BIGINT`, `VARCHAR(n)` vs `TEXT`, `DATE` vs `TIMESTAMP`. | Reduces page size, improves cache hit ratio, and shrinks indexes. | medium |
| 1.7 | Store monetary values as `DECIMAL/NUMERIC` with explicit precision (e.g. `DECIMAL(19,4)`), never `FLOAT`. | Floating-point rounding causes financial discrepancies. | blocking |
| 1.8 | Store all timestamps in UTC (`TIMESTAMPTZ`). Convert to local time only at the application layer. | Avoids DST ambiguity and cross-timezone comparison bugs. | high |
| 1.9 | Add `CHECK` constraints for domain validation (e.g. status enums, non-negative amounts, `start <= end` for paired date/time columns). | Catches invalid data at the engine level before it corrupts downstream. | medium |
| 1.10 | Avoid storing derived/calculated data unless materialized for proven performance reasons. | Keeps a single source of truth; eliminates stale calculation risk. | medium |
| 1.11 | **Intelag alignment:** In multi-schema PostgreSQL, set the table's schema in DDL and in the ORM (`__table_args__` / equivalent) so metadata, migrations, and `search_path` assumptions stay aligned. | Omitting schema while using schema-qualified FK strings breaks mapper configuration and validation tooling. | high |

> **💡 Pro Tip:** If you find yourself adding a column named `type` or `kind` that changes the meaning of other columns, consider splitting into separate tables (table-per-type) or using PostgreSQL's table inheritance.

## Section Break
## 2. Normalization & Denormalization

Normalize first for correctness, then selectively denormalize with measurement-backed justification.

| # | Rule | Rationale | Severity |
|---|------|-----------|----------|
| 2.1 | Achieve at least Third Normal Form (3NF) for all OLTP tables before considering denormalization. | Eliminates update/insert/delete anomalies. | blocking |
| 2.2 | Never store comma-separated values or JSON arrays when a junction table is appropriate. | Destroys referential integrity, prevents indexing, and blocks joins. | blocking |
| 2.3 | Denormalize only after profiling shows a measurable bottleneck; document every denormalization with a dated comment. | Premature denormalization creates technical debt with no measured payoff. | high |
| 2.4 | When denormalizing, use materialized views or summary tables—not duplicated columns—where the engine supports them. | Materialized views can be refreshed atomically; columns can drift. | medium |
| 2.5 | If you store JSON/JSONB, define a JSON Schema or `CHECK` constraint to enforce structure. | Unvalidated JSON becomes a shadow schema no one understands. | medium |
| 2.6 | Eliminate transitive dependencies: a non-key column should never depend on another non-key column. | Prevents update anomalies (e.g., updating a zip code without its city). | high |

## Section Break
## 3. Relationships & Referential Integrity

Foreign keys are contracts between tables. Treat them with the same rigor as application-level validation.

| # | Rule | Rationale | Severity |
|---|------|-----------|----------|
| 3.1 | Every foreign key must have an explicit `REFERENCES` target with deliberate `ON DELETE` and `ON UPDATE` actions (or database defaults you have explicitly accepted). | Missing cascade/restrict rules leave orphan records or silent failures. | blocking |
| 3.2 | Name foreign key columns as `<referenced_entity>_id` (e.g. `organization_id`, `employee_id`) when referencing surrogate keys. | Self-documenting; enables automated ORM discovery. | high |
| 3.3 | Use junction/bridge tables for many-to-many relationships; never embed arrays of IDs. | Preserves referential integrity and enables efficient querying. | blocking |
| 3.4 | Choose `ON DELETE` actions deliberately: `RESTRICT` for strong entities, `CASCADE` for owned children, `SET NULL` for optional associations. | Wrong action either blocks operations or silently destroys data. | blocking |
| 3.5 | Always index foreign key columns (most engines except MySQL/InnoDB do not auto-index them). | Unindexed FKs cause full table scans on `DELETE`/`UPDATE` of the parent row. | high |
| 3.6 | Use composite foreign keys only when the referenced table has a composite PK; prefer surrogate keys otherwise. | Reduces key width, join complexity, and migration difficulty. | medium |
| 3.7 | For soft-delete patterns, add a `deleted_at TIMESTAMPTZ` (nullable) and/or an `is_active` flag with indexing strategy; maintain FK integrity. | Allows auditability and undo; avoids orphan cascades. | medium |
| 3.8 | Self-referencing FKs (e.g. `employee.manager_id → employee.id`) must allow `NULL` for root nodes. | Prevents a chicken-and-egg insertion problem. | medium |
| 3.9 | **PostgreSQL multi-schema:** Reference the full qualified target (`schema.table.column`) in FK definitions when tables live in different schemas, and keep `search_path` or ORM metadata consistent. | Ambiguous or path-dependent resolution causes migration drift and broken tooling. | high |
| 3.10 | **Intelag alignment:** Maintain **one module** (or equivalent) that defines schema names, domain-to-table ownership, and **schema-qualified FK string constants** used across models. Never scatter duplicate `"schema.table.column"` literals. | Centralizes renames, audits, ERD styling, and YAML/model round-trips. | high |

## Section Break
## 4. Indexing Strategy

Indexes are the single largest lever for read performance. Every index also carries a write penalty—balance accordingly.

| # | Rule | Rationale | Severity |
|---|------|-----------|----------|
| 4.1 | Index every column used in `WHERE`, `JOIN ON`, and `ORDER BY` clauses of frequent queries. | Without an index, the engine falls back to sequential scans. | blocking |
| 4.2 | Create composite indexes with the highest-selectivity column first, followed by range/sort columns. | Column order determines whether the index can satisfy the query plan. | blocking |
| 4.3 | Never index columns with very low cardinality (e.g. boolean flags) in isolation—combine with a selective column or use a **partial** index. | A standalone low-cardinality index is rarely chosen by the optimizer. | high |
| 4.4 | Use `INCLUDE` (covering index) columns to enable index-only scans for read-heavy queries. | Avoids expensive heap lookups by serving data directly from the index. | medium |
| 4.5 | Use partial/filtered indexes for queries that target a subset (e.g. `WHERE status = 'active'` or `WHERE is_active = 'Y'`). | Dramatically smaller index; faster scans and lower maintenance. | medium |
| 4.6 | Add `UNIQUE` indexes/constraints for business-level uniqueness (email, SKU, composite natural keys) beyond the PK. | Prevents duplicate data at the engine level, not just the app level. | blocking |
| 4.7 | Limit total indexes per table to roughly 5–7 on write-heavy tables; each index adds `INSERT`/`UPDATE` overhead. | Write amplification degrades throughput and increases WAL volume. | high |
| 4.8 | Audit unused indexes monthly (`pg_stat_user_indexes`, `sys.dm_db_index_usage_stats`) and drop them. | Dead indexes consume storage, slow writes, and bloat backups. | medium |
| 4.9 | Use expression/functional indexes for computed predicates (e.g. `INDEX ON LOWER(email)`). | Allows the optimizer to use the index when the query applies the same function. | medium |
| 4.10 | Use GIN/GiST indexes for full-text search, JSONB, and array containment queries (PostgreSQL). | B-tree cannot satisfy containment or text-search operators. | medium |

> **💡 Index Audit Query (PostgreSQL):**
> ```sql
> SELECT relname, indexrelname, idx_scan
> FROM pg_stat_user_indexes
> WHERE idx_scan = 0 AND schemaname = 'public'
> ORDER BY pg_relation_size(indexrelid) DESC;
> ```

## Section Break
## 5. Performance & Scaling Patterns

Design for the query patterns you know today while keeping the door open for the scale you may need tomorrow.

| # | Rule | Rationale | Severity |
|---|------|-----------|----------|
| 5.1 | Design tables for read patterns: if a query always needs columns A, B, C together, ensure they share an index. | Aligning physical storage with access patterns minimizes I/O. | high |
| 5.2 | Partition large tables (100M+ rows) by range, list, or hash—based on actual query filters. | Partition pruning eliminates scanning irrelevant data. | high |
| 5.3 | Use connection pooling (PgBouncer, ProxySQL) and avoid long-held transactions in OLTP systems. | Idle connections consume memory and can cause lock contention. | high |
| 5.4 | Never use `SELECT *`; always specify required columns. | Reduces I/O, prevents index-only scan invalidation, and avoids schema-change surprises. | high |
| 5.5 | Avoid correlated subqueries in `SELECT`/`WHERE`; rewrite as JOINs or lateral joins. | Correlated subqueries execute per-row; joins execute set-based. | medium |
| 5.6 | Use `EXPLAIN ANALYZE` on every new query before deploying to production. | Reveals sequential scans, nested loops, and sort spills before users feel them. | blocking |
| 5.7 | Implement read replicas for reporting/analytics queries; never run analytics on the primary. | Offloads read traffic and prevents long-running queries from blocking writes. | high |
| 5.8 | Set `statement_timeout` / `lock_timeout` at the session or role level to prevent runaway queries. | A single bad query can exhaust all connections and cascade into downtime. | medium |

## Section Break
## 6. Naming Conventions & Documentation

A schema that explains itself reduces onboarding time and prevents misinterpretation.

| # | Rule | Rationale | Severity |
|---|------|-----------|----------|
| 6.1 | Use consistent naming: `snake_case` for tables/columns, `PascalCase` only if the project-wide standard demands it. | Mixing conventions causes quoting errors and slows development. | high |
| 6.2 | Prefix boolean columns with `is_`, `has_`, or `can_` (e.g. `is_active`, `has_verified_email`). If the engine stores `CHAR(1)` / `VARCHAR(1)` flags (`Y`/`N`), enforce with `CHECK`. | Self-documenting; avoids ambiguous truthy strings. | medium |
| 6.3 | Name indexes as `idx_<table>_<columns>` (or `ix_<table>_<columns>` if consistent) and constraints with explicit names: `chk_`, `uq_`, `fk_`. | Allows instant identification in error messages and monitoring. | medium |
| 6.4 | **Multi-schema:** Auto-generated `ix_*` names may include schema/table qualifiers; treat **column sets** as the semantic identity when comparing models or migrations—human-chosen `idx_*` names should still be stable and explicit. | Prevents false drift between environments that differ only by `search_path` or metadata naming. | medium |
| 6.5 | Add `COMMENT ON TABLE` / `COMMENT ON COLUMN` for every object in the schema. | Surfaces documentation in IDE tooltips and data catalog tools. | medium |
| 6.6 | Maintain versioned migration files (Flyway, Liquibase, Alembic)—never apply ad-hoc DDL in production. | Ensures reproducibility, rollback capability, and audit history. | blocking |
| 6.7 | Keep an up-to-date ERD in the repository, regenerated from models or the live schema. | Visual references accelerate understanding for new team members. | medium |

## Section Break
## 7. Security & Access Control

The database is the last line of defense. Assume the application layer will eventually have a bug.

| # | Rule | Rationale | Severity |
|---|------|-----------|----------|
| 7.1 | Apply the principle of least privilege: application roles should have only `SELECT`/`INSERT`/`UPDATE` on needed tables. | Limits blast radius if credentials are compromised. | blocking |
| 7.2 | Never store plaintext passwords or API keys in any column; use hashed values with bcrypt/argon2. | Plaintext credentials in a breach expose all users. | blocking |
| 7.3 | Encrypt PII columns at rest (TDE or column-level encryption) and mask them in non-production environments. | Regulatory compliance (GDPR, HIPAA) and breach risk reduction. | high |
| 7.4 | Use row-level security (RLS) for multi-tenant databases instead of filtering only in the application. | Prevents data leakage even if the app has a logic bug. | high |
| 7.5 | Audit all DDL changes and privileged DML with `pg_audit` or equivalent engine feature. | Creates a forensic trail for security incidents and change management. | medium |

## Section Break
## 8. Multi-schema PostgreSQL & bounded contexts (DDD)

When a single database hosts multiple **bounded contexts**, use PostgreSQL **schemas** as physical boundaries—not separate meanings crammed into one flat namespace.

| # | Rule | Rationale | Severity |
|---|------|-----------|----------|
| 8.1 | Map one bounded context → one PostgreSQL `schema` name (short, stable, lowercase). | Clarifies ownership, migration ordering, and permission boundaries. | high |
| 8.2 | Keep **shared kernel** tables (e.g. organization, location) in a dedicated schema; depend on them via explicit FKs rather than copying IDs without constraints. | Prevents silent divergence and orphan references across contexts. | blocking |
| 8.3 | Document which tables belong to which context (single source of truth); derive reverse lookups (table → context) from that map—do not duplicate. | Tools (ERD, validators, `SchemaManager`) stay consistent. | high |
| 8.4 | Prefer **many small, named migrations** per context over monolithic dumps when contexts evolve at different speeds. | Reduces merge conflict and blast radius. | medium |
| 8.5 | For SQLite or tests, use `schema_translate_map` (or equivalent) so the same models target a single logical schema without forking class definitions. | Keeps test and prod metadata aligned. | medium |

## Section Break
## 9. ORM alignment (SQLAlchemy-style declarative models)

These rules apply when the database is authored and migrated through an ORM layer.

| # | Rule | Rationale | Severity |
|---|------|-----------|----------|
| 9.1 | Declare **every** `ForeignKey` at the column (or constraint) level with the same qualification you use in DDL (`schema.table.column` when using multiple schemas). | Enables relationship join inference, Alembic autogenerate, and static validation. | blocking |
| 9.2 | When validating FK targets against `MetaData.tables`, use the table's **`key`** (schema-qualified internal key), not the bare `Table.name` only. | Otherwise every cross-schema FK appears to target a “missing” table. | blocking |
| 9.3 | Name all non-PK constraints (`ForeignKeyConstraint`, `UniqueConstraint`, `CheckConstraint`, indexes) explicitly. | Stable names in migrations and clearer error messages. | high |
| 9.4 | Keep `relationship()` definitions consistent with FK columns (`back_populates`, `uselist`); resolve string class names by ensuring the declarative registry or imports load related modules before `configure_mappers()`. | Avoids `InvalidRequestError` / `NoForeignKeysError` at runtime. | high |
| 9.5 | For model ↔ YAML ↔ generated-model round-trips: ensure FK targets are **serializable** (literal strings or constants resolvable to strings). Registry indirection (`ForeignKey(ORG_ID)`) must be expanded when exporting to YAML. | Prevents `foreign_key: null` in specs and empty FK metadata in generated code. | high |
| 9.6 | Type conventions: `Uuid`/`UUID` for `*_id` columns, `DateTime(timezone=True)` for timestamps, `Numeric` for money—match validator rules if the project runs structural checks. | Catches drift before production DDL. | medium |

## Section Break
## 10. Application services & persistence boundary

Database design succeeds only if the layer above it respects transactions and invariants.

| # | Rule | Rationale | Severity |
|---|------|-----------|----------|
| 10.1 | **Validate at the boundary** (DTO/schema) then persist; do not rely on the database alone for app-level rules, but do rely on the database for integrity it enforces best (FK, uniqueness, checks). | Defense in depth; clearer error handling. | high |
| 10.2 | Use a **unit of work** / session per request or use-case; commit once at the end of a successful business operation. | Avoids partial updates and inconsistent read-your-writes behavior. | blocking |
| 10.3 | Map domain exceptions to HTTP/API errors explicitly; map integrity errors (`UniqueViolation`, `ForeignKeyViolation`) to conflict or client-error responses with safe messages. | Prevents leaking internals and improves client behavior. | high |
| 10.4 | Services should accept filters and payloads that mirror **indexed** columns for list endpoints (`organization_id`, `status`, date ranges). | Prevents accidental full scans from generic “search everything” APIs. | medium |
| 10.5 | Idempotent writes where the business requires them (use natural or external keys plus `ON CONFLICT` or explicit upsert patterns). | Safer retries and message-driven workflows. | medium |

## Section Break
## Quick-Reference Checklist

###  Do This (Every Time)

- [ ] Surrogate PK (`UUID` or `BIGINT`/identity) on every table
- [ ] `NOT NULL` as the default; NULL only with documented business reason
- [ ] `created_at` and `updated_at` on every table (`TIMESTAMPTZ`)
- [ ] Foreign keys with explicit targets and deliberate `ON DELETE` / `ON UPDATE`
- [ ] Index on every FK column (or covering composite that includes it)
- [ ] `UNIQUE` constraint on natural business identifiers where applicable
- [ ] `CHECK` constraints on status/value domains and sensible date/time pairs
- [ ] `EXPLAIN ANALYZE` on every new or modified heavy query
- [ ] Migration file committed and peer-reviewed
- [ ] `COMMENT ON TABLE` / `COMMENT ON COLUMN` where the project expects catalog docs
- [ ] **Multi-schema:** schema on table metadata matches FK qualification
- [ ] **ORM:** FK registry / constants centralized; YAML exports resolve symbols to strings
- [ ] **Validation/tooling:** FK target checks use metadata `Table.key`, not name alone

### ❌ Never Do This

- [ ] Store comma-separated values or arrays of IDs in a column when a junction table fits
- [ ] Use `FLOAT`/`DOUBLE` for monetary values
- [ ] Apply ad-hoc DDL directly in production
- [ ] Use `SELECT *` in application queries
- [ ] Index a boolean (or `Y`/`N` flag) alone without partial predicate or leading selective column
- [ ] Omit `ON DELETE` / `ON UPDATE` choice without documenting the DB default
- [ ] Store plaintext passwords or secrets
- [ ] Denormalize without a profiled performance justification
- [ ] Leave unused indexes accumulating
- [ ] Mix naming conventions within the same schema
- [ ] Scatter duplicate schema-qualified FK strings across the codebase

## Section Break
## Severity Guide

| Severity | Meaning | Action Required |
|----------|---------|-----------------|
| blocking | Violation causes data loss, corruption, or security breach. | Must fix before merge; blocks deployment. |
| high | Causes significant performance degradation or maintenance burden. | Must fix within the current sprint. |
| medium | Best practice; improves clarity, maintainability, or minor performance. | Should fix; acceptable to defer with documented reason. |

## Section Break
## Document history

| Version | Date | Summary |
|---------|------|---------|
| 1.0 | 2026 | Initial rubric (tables through security). |
| 1.1 | 2026 | Multi-schema DDD, ORM/metadata rules, registry FK strings, service boundary, checklist expansion, plural/singular table naming nuance, Intelag alignment callouts. |
