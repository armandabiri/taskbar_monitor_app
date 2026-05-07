from __future__ import annotations

import re
import sys
from frontmatter import iter_markdown_files, parse_frontmatter, expected_id

REQUIRED = ["id", "genre", "applies_to", "load_mode", "status", "schema_version", "introduced_in", "updated", "owners", "supersedes", "doc_version"]
GENRES = {"convention", "persona", "playbook", "prompt", "routing", "template", "shared", "archive", "glossary", "readme"}
STATUSES = {"active", "draft", "deprecated", "archived"}
SEMVER = re.compile(r"^\d+\.\d+\.\d+$")
CALVER = re.compile(r"^\d{4}\.\d{2}\.\d+$")
DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

errors = []
seen = {}
for path, rel in iter_markdown_files():
    data, _ = parse_frontmatter(path.read_text(encoding="utf-8"))
    if data is None:
        errors.append(f"{rel}: missing frontmatter")
        continue
    for key in REQUIRED:
        if key not in data:
            errors.append(f"{rel}: missing {key}")
    doc_id = data.get("id")
    if doc_id:
        if doc_id in seen:
            errors.append(f"{rel}: duplicate id {doc_id} also in {seen[doc_id]}")
        seen[doc_id] = rel
        if doc_id != expected_id(rel):
            errors.append(f"{rel}: id {doc_id} does not match expected {expected_id(rel)}")
    if data.get("genre") not in GENRES:
        errors.append(f"{rel}: invalid genre {data.get('genre')}")
    if data.get("status") not in STATUSES:
        errors.append(f"{rel}: invalid status {data.get('status')}")
    if str(data.get("schema_version")) != "2":
        errors.append(f"{rel}: unsupported schema_version {data.get('schema_version')}")
    if not SEMVER.match(str(data.get("doc_version", ""))):
        errors.append(f"{rel}: invalid doc_version {data.get('doc_version')}")
    if not CALVER.match(str(data.get("introduced_in", ""))):
        errors.append(f"{rel}: invalid introduced_in {data.get('introduced_in')}")
    if not DATE.match(str(data.get("updated", ""))):
        errors.append(f"{rel}: invalid updated {data.get('updated')}")

if errors:
    print("\n".join(errors))
    sys.exit(1)
print(f"frontmatter ok: {len(seen)} files")
