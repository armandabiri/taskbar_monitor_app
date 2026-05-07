from __future__ import annotations

import re
import sys
from frontmatter import iter_markdown_files

SEVERITIES = {"blocking", "high", "medium", "low"}
ID_RE = re.compile(r"`((?:stacks|shared|conventions|archive|routing|personas|templates)\.[a-z0-9.-]+)`")
errors = []

for path, rel in iter_markdown_files():
    if not any(part in rel for part in ("conventions", "playbooks", "shared/logging", "shared/secrets", "design-rules")):
        continue
    ids = set()
    for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.startswith("|") or "---" in line:
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        found = ID_RE.search(cells[0]) if cells else None
        if not found:
            continue
        rule_id = found.group(1)
        if rule_id in ids:
            errors.append(f"{rel}:{lineno}: duplicate rule id {rule_id}")
        ids.add(rule_id)
        if len(cells) < 2 or cells[1].strip("`") not in SEVERITIES:
            errors.append(f"{rel}:{lineno}: invalid or missing severity for {rule_id}")

if errors:
    print("\n".join(errors))
    sys.exit(1)
print("rule ids ok")
