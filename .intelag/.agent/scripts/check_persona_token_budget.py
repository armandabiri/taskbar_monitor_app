from __future__ import annotations

import sys
from frontmatter import iter_markdown_files, parse_frontmatter

BUDGET_TOKENS = 102_400
CHARS_PER_TOKEN = 4
errors = []
checked = 0
for path, rel in iter_markdown_files():
    data, body = parse_frontmatter(path.read_text(encoding="utf-8"))
    if not data or data.get("genre") != "persona":
        continue
    checked += 1
    approx_tokens = max(1, len(body) // CHARS_PER_TOKEN)
    if approx_tokens > BUDGET_TOKENS:
        errors.append(f"{rel}: approx {approx_tokens} tokens exceeds {BUDGET_TOKENS}")
if errors:
    print("\n".join(errors))
    sys.exit(1)
print(f"persona token budget ok: {checked} personas under {BUDGET_TOKENS} tokens")
