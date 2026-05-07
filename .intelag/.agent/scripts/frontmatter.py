from __future__ import annotations

from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
LOCAL_PREFIXES = ("plans/", "reports/", "issues/", "requests/", "summary/")


def iter_markdown_files():
    for path in ROOT.rglob("*.md"):
        rel = path.relative_to(ROOT).as_posix()
        if rel.startswith(LOCAL_PREFIXES) or rel == "CONTENT_FREEZE.md":
            continue
        yield path, rel


def parse_frontmatter(text: str):
    match = re.match(r"(?s)^---\s*\n(.*?)\n---\s*\n", text)
    if not match:
        return None, text
    raw = match.group(1)
    data = {}
    current = None
    for line in raw.splitlines():
        if not line.strip():
            continue
        if line.startswith("  - ") and current:
            data.setdefault(current, []).append(line[4:].strip())
            continue
        if ":" in line:
            key, value = line.split(":", 1)
            key = key.strip()
            value = value.strip()
            current = key
            if value == "":
                data[key] = []
            elif value == "[]":
                data[key] = []
            else:
                data[key] = value
    return data, text[match.end():]


def expected_id(rel: str) -> str:
    value = re.sub(r"\.md$", "", rel).replace("/README", ".readme")
    value = re.sub(r"[^a-z0-9/.-]+", "-", value.lower())
    return value.replace("/", ".")
