---
id: stacks.python.conventions
genre: convention
applies_to:
  - python
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
| `stacks.python.naming.snake-case-files` | `medium` | Use snake_case for Python files and packages. |
| `stacks.python.cli.cmd-prefix` | `medium` | Use the `cmd_` prefix for CLI command handlers. |
| `stacks.python.config.env-driven` | `high` | Keep environment-specific values in configuration. |
## Shared Rule References

- See `shared.logging` for cross-stack logging requirements.
- See `shared.secrets` for secrets and PII requirements.

Intelag Repository and Package Standard

HARD RULES

* Full PEP8 compliance.
* Must pass MyPy strict mode.
* All functions fully type annotated.
* Avoid Any and type ignore unless absolutely unavoidable.
* Replace magic numbers and strings with constants or enums.
* Prefix unused variables with underscore.
* Separate imports into four groups: standard library, third party, intelag packages, internal modules.
* Use absolute imports only, no relative imports.
* Do not use if TYPE_CHECKING.
* Every file starts with a module level docstring.
* All public functions have exactly one line docstrings.
* Never use print, use logger with lazy percent formatting.
* Log errors before raising exceptions.
* Error handlers return safe defaults.
* Avoid getattr, setattr, delattr, hasattr.
* File size under 800 lines.
* Each file defines example_usage and calls it under main.
* Review code twice and remove duplication.
* Prefer minimal simple code.

Repository Structure

* Root contains intelag_main_package for orchestration.
* intelag_main_package includes cli, services, models, config, utils.
* intelag_packages for internal pip installable packages.
* intelag_submodules for shared git submodules.
* scripts for non package utilities.
* pyproject.toml for build system and CLI entry points.
* README.md architecture first.
* Lint and mypy configs at root.

Naming Conventions

* Packages and directories use snake_case with intelag_ prefix.
* Classes use PascalCase.
* Functions and attributes use snake_case.
* CLI subcommand functions use cmd_ prefix.
* Constants use UPPER_SNAKE_CASE.

CLI Design Pattern

* Use argparse with subcommands.
* cli module acts as facade and exports public CLI components via all.
* Complex subcommands use class pattern with configure_parser(parser) and run(args).

Coding Standards

* Each file begins with module docstring.
* Imports grouped as standard, third party, intelag shared, internal project.
* Use absolute imports only.
* No TYPE_CHECKING.
* Public functions have exactly one line docstrings.
* All parameters and return values fully typed.

Configuration Pattern

* Use YAML for all configurations.
* Load configs via intelag_config_model.
* Store defaults in config directory.
* Allow user override with --config flag.
* Use importlib.resources for packaged defaults.

Execution Block Pattern

* Each backend or utility file defines example_usage.
* example_usage is called under if main for manual testing.

## Section Break