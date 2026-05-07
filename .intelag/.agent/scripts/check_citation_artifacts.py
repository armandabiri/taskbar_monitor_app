from __future__ import annotations

import re
import sys
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass
from frontmatter import iter_markdown_files

PATTERNS = ["", "filecite"]
TURN = re.compile(r"turn[0-9]+(?:search|view)")
errors = []
for path, rel in iter_markdown_files():
    text = path.read_text(encoding="utf-8")
    for token in PATTERNS:
        if token in text:
            errors.append(f"{rel}: contains {token}")
    if TURN.search(text):
        errors.append(f"{rel}: contains turn search/view artifact")
if errors:
    print("\n".join(errors))
    sys.exit(1)
print("citation artifacts ok")
