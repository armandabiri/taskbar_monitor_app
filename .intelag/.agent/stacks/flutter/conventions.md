---
id: stacks.flutter.conventions
genre: convention
applies_to:
  - flutter
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
| `stacks.flutter.logging.no-console-output` | `high` | Legacy `P-1` migrated from the Flutter prohibited-pattern set. |
| `stacks.flutter.architecture.cloud-abstraction` | `high` | Legacy `P-2` migrated from the Flutter prohibited-pattern set. |
| `stacks.flutter.ui.use-ui-kit` | `high` | Legacy `P-3` migrated from the Flutter prohibited-pattern set. |
| `stacks.flutter.imports.absolute-package-imports` | `high` | Legacy `P-4` migrated from the Flutter prohibited-pattern set. |
| `stacks.flutter.types.no-unexplained-dynamic` | `high` | Legacy `P-5` migrated from the Flutter prohibited-pattern set. |
| `stacks.flutter.null-safety.no-unjustified-bang` | `high` | Legacy `P-6` migrated from the Flutter prohibited-pattern set. |
| `stacks.flutter.riverpod.no-business-logic-in-provider` | `high` | Legacy `P-10` migrated from the Flutter prohibited-pattern set. |
| `stacks.flutter.i18n.no-hardcoded-user-text` | `high` | Legacy `P-11` migrated from the Flutter prohibited-pattern set. |
| `stacks.flutter.ui.no-setstate-shared-widget` | `high` | Legacy `P-12` migrated from the Flutter prohibited-pattern set. |
| `stacks.flutter.riverpod.use-notifier` | `high` | Legacy `P-13` migrated from the Flutter prohibited-pattern set. |
| `stacks.flutter.riverpod.one-provider-per-concern` | `high` | Legacy `P-14` migrated from the Flutter prohibited-pattern set. |
| `stacks.flutter.architecture.no-direct-rds` | `high` | Legacy `P-15` migrated from the Flutter prohibited-pattern set. |
| `stacks.flutter.testing.generate-tests` | `high` | Legacy `P-17` migrated from the Flutter prohibited-pattern set. |
| `stacks.flutter.architecture.domain-pure-dart` | `high` | Legacy `P-18` migrated from the Flutter prohibited-pattern set. |
## Shared Rule References

- See `shared.logging` for cross-stack logging requirements.
- See `shared.secrets` for secrets and PII requirements.

# Intelag Flutter & Dart Development Standard

## Section Break
## HARD RULES

### Logging

- Never use `print`, `debugPrint`, `log`, or `developer.log`. Use `IntelagLogger` exclusively.
- Use lazy formatting. Do not interpolate strings in log calls on hot paths. Pass parameters separately so the message is only constructed if the log level is active.
- Log errors before throwing exceptions.
- Log levels: `debug` (dev only), `info`, `warning`, `error`, `fatal`. Choose correctly.
- Never log tokens, passwords, PII, or secrets. Use anonymized identifiers (user ID, restaurant ID).

### Code Style and Formatting

- Full compliance with `flutter_lints` (or the team's `analysis_options.yaml`). Zero analysis warnings in CI.
- All public classes, methods, and top-level functions have single-line `///` doc comments. No more, no less.
- All parameters and return values are fully typed. Never use `dynamic` unless wrapping a genuinely untyped boundary (e.g., raw JSON decode). If you use `dynamic`, add a comment explaining why.
- Replace magic numbers and hardcoded strings with named constants, enums, or l10n keys.
- Prefix unused variables and parameters with underscore.
- File size under 600 lines. If a file exceeds this, split it.
- Prefer `const` constructors wherever possible. Mark widgets, objects, and collections `const` when all members are compile-time constants.
- Prefer `final` for all local variables and fields that are not reassigned. Never use `var` when `final` works.
- Use trailing commas on all argument lists, parameter lists, and collection literals to ensure consistent `dart format` output.

### Null Safety

- Full sound null safety. No escape hatches.
- Never use the bang operator (`!`) unless you can prove the value is non-null at that point and a comment justifies it. Prefer null-aware operators (`?.`, `??`, `??=`), pattern matching, or early returns.
- Never use `as` for downcasting without a preceding `is` check or a guaranteed type context.

### Imports

- Separate imports into four groups, in this order, separated by a blank line:
  1. Dart SDK (`dart:async`, `dart:io`, etc.)
  2. Flutter SDK (`package:flutter/...`)
  3. Third-party packages (`package:riverpod/...`, `package:go_router/...`, etc.)
  4. Intelag packages and internal project imports (`package:restaurant_ui_kit/...`, `package:intelag_logger/...`, then project-relative)
- Use absolute package imports only. No relative imports (`../`, `./`).
- Never import an entire package barrel if you only need one symbol. Be specific where possible without being impractical.
- **Auto sort:** Add `import_sorter: ^4.6.0` to `dev_dependencies` in `pubspec.yaml`. Then run **Sort Flutter/Dart Imports** (Command Palette or Intelag menu) or rely on pre-commit to sort into `// Dart imports:`, `// Flutter imports:`, `// Package imports:`, `// Project imports:`.

### Architecture Boundaries

- Domain layer is pure Dart. No Flutter imports, no AWS imports, no package dependencies beyond Dart core. Entities and use cases live here.
- Presentation layer never calls data sources, repositories, or cloud APIs directly. It calls use cases via Riverpod providers only.
- Data flow is strictly: `UI → Provider → UseCase → Repository → DataSource`. No shortcuts.
- All cloud/backend access goes through the abstraction layer. No direct AWS SDK calls from UI, providers, or use cases. Violation is a blocking code review finding.
- No business logic in providers. Providers orchestrate; use cases contain logic.

### State Management (Riverpod)

- Use `Notifier` / `AsyncNotifier` (Riverpod 2.x+ code-gen style). Do not use legacy `StateNotifier` or `ChangeNotifier`.
- One provider per concern. No "god" providers that manage multiple unrelated pieces of state.
- Use `ref.watch` for reactive rebuilds. Use `ref.read` for one-shot actions (button taps, callbacks). Never call `ref.watch` inside callbacks or async functions.
- No circular provider dependencies. If you have one, your architecture is wrong — refactor.
- Feature-level providers live in `features/<feature>/presentation/providers/`. Cross-cutting providers live in `shared/providers/`.

### Routing (GoRouter)

- All route definitions live in `app/router/`. No route definitions elsewhere.
- Use typed routes (`go_router_builder`) or named route constants. No hardcoded string paths in `context.go()` or `context.push()`.
- Use `ShellRoute` for layout shells (nav bars, drawers). Use redirect guards for auth and role-based access.
- Do not nest `GoRouter` instances.

### Design System and UI

- All screens use only the internal UI kit components (`AppPrimaryButton`, `AppTextField`, etc.). Raw `ElevatedButton`, `TextField`, `TextFormField`, etc. are prohibited in feature code.
- All visual tokens (colors, typography, spacing, radii, shadows) come from the theme package. No inline `Color(0xFF...)`, `FontWeight.bold`, or `EdgeInsets.all(16)` scattered through features. Reference the theme.
- Limit button variants to the prescribed set (primary, secondary, outline, icon, FAB, toggle). Do not invent new button styles per feature.
- Every shared widget must be stateless or manage state via Riverpod — no `setState` in shared components. Feature-level widgets may use `setState` only for ephemeral, widget-local UI state (e.g., animation controllers, form focus).

### Error Handling

- All repository methods return a typed result (e.g., `Either<Failure, T>`, sealed class, or equivalent). No throwing exceptions across layer boundaries.
- Use specific `Failure` subclasses. No generic "something went wrong" — every failure carries enough context to display a meaningful message and to log diagnostics.
- Every `try-catch` in the data layer must log the error via `IntelagLogger` before returning a `Failure`.
- UI must handle all states: loading, data, error, and empty. No unhandled `AsyncValue.error` or missing error widgets.

### Offline and Sync

- Offline is a first-class architectural concern, not an add-on. Repository implementations check connectivity and route to local or remote data sources accordingly.
- All pending mutations go through the sync queue with timestamps and entity version fields.
- Conflict resolution strategy is defined per entity (last-write-wins, server-wins, merge). Document the strategy for each entity in the sync adapter file.
- Do not cache everything. Define explicitly which data is available offline (scoped per restaurant, per shift). Document this in the sync registry.

### Security

- Tokens and secrets stored in `flutter_secure_storage` only. Never in `SharedPreferences`, plain files, or local DB without encryption.
- Local database must be encrypted (SQLCipher for Drift, Isar encryption, etc.).
- All API traffic over HTTPS. No plain HTTP in any environment.
- Validate and sanitize all user input before sending to backend or persisting locally. Use parameterized queries in local DB — no string concatenation.
- Clear tokens on sign-out and session invalidation.

### Internationalization (i18n)

- Every user-facing string lives in ARB files (`assets/translations/`). No hardcoded text in Dart files.
- Use the generated `AppLocalizations` class. No raw string keys.
- Date, number, and currency formatting must respect the current locale.

### Testing

- Unit tests for all use cases and repository implementations.
- Widget tests for all shared UI kit components.
- Integration tests for sync flows and critical user journeys.
- Mock repositories and external services. Tests must be fast and deterministic.
- Tests are written alongside features, not deferred to a later phase.

### Crash Reporting

- `runZonedGuarded` in `main.dart`. Hook `FlutterError.onError` and `PlatformDispatcher.instance.onError`.
- Crash reporting service (Crashlytics or Sentry) initialized before any other service.
- Attach non-PII context to crash reports: user role, restaurant ID, app version, sync state. No tokens or PII.

## Section Break
## NAMING CONVENTIONS

| Element                       | Convention                      | Example                                  |
| ----------------------------- | ------------------------------- | ---------------------------------------- |
| Directories and file names    | `snake_case`                    | `order_sync_adapter.dart`                |
| Classes, enums, typedefs      | `PascalCase`                    | `OrderEntity`, `SyncStatus`              |
| Functions, methods, variables | `camelCase`                     | `fetchOrders`, `syncQueue`               |
| Constants and enum values     | `camelCase` (Dart convention)   | `defaultTimeout`, `OrderStatus.pending`  |
| Private members               | `_camelCase`                    | `_syncEngine`, `_handleError`            |
| Riverpod providers            | `camelCase` + `Provider` suffix | `orderListProvider`, `authStateProvider` |
| Route names                   | `camelCase`                     | `orderDetail`, `employeeList`            |
| ARB keys                      | `camelCase`                     | `loginButtonLabel`, `orderEmptyMessage`  |
| UI kit components             | `App` prefix + `PascalCase`     | `AppPrimaryButton`, `AppTextField`       |
| Test files                    | match source + `_test`          | `order_repository_test.dart`             |

## Section Break
## IMPORT ORDER (ENFORCED)

```dart
// 1. Dart SDK
import 'dart:async';
import 'dart:io';

// 2. Flutter SDK
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

// 3. Third-party packages
import 'package:dio/dio.dart';
import 'package:go_router/go_router.dart';
import 'package:riverpod/riverpod.dart';

// 4. Intelag packages and internal project
import 'package:intelag_logger/intelag_logger.dart';
import 'package:restaurant_ui_kit/restaurant_ui_kit.dart';
import 'package:restaurant_app/core/errors/failures.dart';
import 'package:restaurant_app/domain/entities/order_entity.dart';
```

## Section Break
## PROHIBITIONS

| ID   | Rule                                                                                       |
| ---- | ------------------------------------------------------------------------------------------ |
| stacks.flutter.logging.no-console-output  | No `print`, `debugPrint`, `log`, or `developer.log`. Use `IntelagLogger` only.             |
| stacks.flutter.architecture.cloud-abstraction  | No direct AWS/cloud SDK calls from UI, providers, or use cases. Abstraction layer only.    |
| stacks.flutter.ui.use-ui-kit  | No raw Material widgets (`ElevatedButton`, `TextField`, etc.) in feature code. Use UI kit. |
| stacks.flutter.imports.absolute-package-imports  | No relative imports. Absolute package imports only.                                        |
| stacks.flutter.types.no-unexplained-dynamic  | No `dynamic` without a justifying comment. No untyped `Map` or `List`.                     |
| stacks.flutter.null-safety.no-unjustified-bang  | No `!` (bang operator) without a preceding null guarantee and a comment.                   |
| shared.secrets.no-pii-in-logs  | No tokens, passwords, or PII in logs, crash reports, or analytics.                         |
| shared.secrets.secure-token-storage  | No `SharedPreferences` for tokens or secrets. Use `flutter_secure_storage`.                |
| shared.secrets.https-only  | No plain HTTP. HTTPS/TLS only.                                                             |
| stacks.flutter.riverpod.no-business-logic-in-provider | No business logic in providers. Logic lives in use cases.                                  |
| stacks.flutter.i18n.no-hardcoded-user-text | No hardcoded user-facing strings in Dart. All text in ARB files.                           |
| stacks.flutter.ui.no-setstate-shared-widget | No `setState` in shared/UI kit widgets. Riverpod or `const` only.                          |
| stacks.flutter.riverpod.use-notifier | No `StateNotifier` or `ChangeNotifier`. Use `Notifier` / `AsyncNotifier`.                  |
| stacks.flutter.riverpod.one-provider-per-concern | No God providers. One concern per provider.                                                |
| stacks.flutter.architecture.no-direct-rds | No direct RDS connections from the client. API Gateway + Lambda only.                      |
| shared.secrets.encrypt-local-data | No unencrypted local database in any environment.                                          |
| stacks.flutter.testing.generate-tests | No testing deferred to "later." Tests ship with features.                                  |
| stacks.flutter.architecture.domain-pure-dart | No Flutter or package imports in the domain layer. Pure Dart only.                         |

## Section Break
## FILE STRUCTURE RULES

- Every feature follows: `features/<name>/presentation/{screens, widgets, providers}/`.
- Every shared widget lives in `shared/widgets/` or in the `restaurant_ui_kit` package.
- Every domain entity lives in `domain/entities/`. Every repository contract in `domain/repositories/`.
- Every data model (DTO) lives in `data/models/`. DTOs handle serialization. Domain entities do not.
- DTOs map to domain entities via explicit mapper methods or extensions. No `json_serializable` annotations on domain entities.
- Sync adapters and strategies live in `core/sync/`.
- Route config lives in `app/router/`. Guards in `app/router/guards/`. Shells in `app/router/shells/`.

## Section Break
## CODE REVIEW CHECKLIST (BLOCKING FINDINGS)

A PR must not be approved if any of the following are true:

1. Any `print` or `debugPrint` statement exists.
2. Any direct cloud SDK call outside the abstraction layer.
3. Any raw Material widget in feature code.
4. Any untyped `dynamic` without justification.
5. Any hardcoded user-facing string (not in ARB).
6. Any token or secret in `SharedPreferences` or plain storage.
7. Any file over 600 lines.
8. Any provider containing business logic that should be a use case.
9. Any missing error/loading/empty state handling in UI.
10. Any feature merged without accompanying tests.
11. Any relative import.
12. Any `IntelagLogger` call containing PII.

## Section Break
## PERFORMANCE GUIDELINES

- Profile before optimizing. Use Flutter DevTools, the Performance overlay, and `Timeline` events to identify actual bottlenecks. Do not guess.
- Use `const` widgets to avoid unnecessary rebuilds. Mark subtrees `const` wherever possible.
- Keep `build` methods lean. Extract expensive computations into providers or use `select` to watch only the fields you need.
- Use `ListView.builder` / `GridView.builder` for long lists. Never render unbounded children in `Column` or `Row` inside `SingleChildScrollView`.
- Avoid rebuilding the entire widget tree. Use granular Riverpod providers and `Consumer` widgets scoped to the smallest subtree that depends on the state.
- Cache images. Use `CachedNetworkImage` or equivalent. Set `cacheWidth` / `cacheHeight` on `Image` to decode at display resolution, not native resolution.
- Minimize shader compilation jank. Warm up shaders in CI if targeting release mode on devices that exhibit it.
- Debounce and throttle: search inputs, scroll listeners, connectivity checks. Use the provided `Debouncer` and `Throttler` utilities.
- For expensive local DB queries, run them off the main isolate using `compute()` or `Isolate.run()`.
- Lazy-load features and heavy assets. Use deferred imports or route-level code splitting where the platform supports it.

## Section Break
## GIT AND WORKFLOW RULES

- Follow the chained branching pattern as defined in the Development Workflow Instructions.
- Branch naming: `feature/widget_[name]` for widgets, `feature/[name]` for features, `fix/[name]` for bugs.
- Commit messages: `feat:`, `fix:`, `refactor:`, `test:`, `docs:`, `chore:` prefixes. Keep the subject under 72 characters.
- PRs target the correct base branch (chained pattern). Never target `main` directly unless it is the first branch in the chain.
- Do not merge your own PRs. All PRs require at least one approval.
- CI must pass (analysis, tests, formatting) before approval.

## Section Break
*Last Updated: February 2026*
*Maintainer: Intelag Development Team*