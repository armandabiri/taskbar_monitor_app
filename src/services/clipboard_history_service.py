"""Clipboard history tracking with persistent output templates."""

from __future__ import annotations

import json

from PyQt6.QtCore import QSettings
from PyQt6.QtGui import QClipboard

MAX_HISTORY_ITEMS = 30
_HISTORY_KEY = "clipboard_history_items"
_TEMPLATES_KEY = "clipboard_custom_templates"
_DEFAULT_TEMPLATE = "Quoted Line"
_DEFAULT_TEMPLATES = {
    "Quoted Line": "{quoted_csv}",
    "JSON Array": "[{json_items}]",
    "Lines": "{lines}",
    "Bullets": "{bullets}",
    "Numbered": "{numbered}",
    "Markdown Quote": "{quotes}",
}


class ClipboardHistoryService:
    """Stores recent clipboard text entries and renders them via templates."""

    def __init__(self, settings: QSettings) -> None:
        self._settings = settings
        self._history = self._load_json_list(_HISTORY_KEY)
        self._custom_templates = self._load_json_dict(_TEMPLATES_KEY)

    def sync_text(self, text: str) -> None:
        """Record a new clipboard text value if it is meaningful."""
        normalized = self._normalize_text(text)
        if normalized is None:
            return
        self._push_history(normalized)
        self._save()

    def clear_history(self) -> None:
        """Clear the stored clipboard history."""
        self._history.clear()
        self._save()

    def history_items(self) -> list[str]:
        """Return the clipboard history from newest to oldest."""
        return list(self._history)

    def template_names(self) -> list[str]:
        """Return built-in and custom template names."""
        return list(_DEFAULT_TEMPLATES) + sorted(self._custom_templates)

    def template_body(self, name: str) -> str:
        """Return the current body for the template name."""
        return self._custom_templates.get(name, _DEFAULT_TEMPLATES.get(name, _DEFAULT_TEMPLATES[_DEFAULT_TEMPLATE]))

    def save_template(self, name: str, body: str) -> bool:
        """Persist a custom template."""
        normalized_name = self._normalize_name(name)
        normalized_body = self._normalize_template_body(body)
        if normalized_name is None or normalized_body is None:
            return False
        self._custom_templates[normalized_name] = normalized_body
        self._save()
        return True

    def delete_template(self, name: str) -> None:
        """Delete a custom template when present."""
        self._custom_templates.pop(name, None)
        self._save()

    def is_custom_template(self, name: str) -> bool:
        """Return True when the template is user-defined."""
        return name in self._custom_templates

    def copy_history_item(self, text: str, clipboard: QClipboard) -> None:
        """Move a history item back into the system clipboard."""
        normalized = self._normalize_text(text)
        if normalized is None:
            return
        self._push_history(normalized)
        self._save()
        clipboard.setText(normalized)

    def copy_combined_items(
        self,
        texts: list[str],
        clipboard: QClipboard,
        template_name: str,
        template_body: str | None = None,
    ) -> bool:
        """Copy selected history items rendered through the chosen template."""
        combined = self.render_items(texts, template_name, template_body)
        if combined is None:
            return False
        self.copy_history_item(combined, clipboard)
        return True

    def render_items(
        self,
        texts: list[str],
        template_name: str,
        template_body: str | None = None,
    ) -> str | None:
        """Render selected items using a template body and placeholders."""
        items = [item for text in texts if (item := self._normalize_text(text)) is not None]
        if not items:
            return None
        body = self._normalize_template_body(template_body) if template_body is not None else self.template_body(template_name)
        if body is None:
            return None
        replacements = self._replacement_map(items)
        for key, value in replacements.items():
            body = body.replace(f"{{{key}}}", value)
        return body

    @staticmethod
    def default_template_name() -> str:
        """Return the default template name."""
        return _DEFAULT_TEMPLATE

    @staticmethod
    def preview_text(text: str, limit: int = 60) -> str:
        """Build a one-line preview of a clipboard entry."""
        single_line = " ".join(text.split())
        return single_line if len(single_line) <= limit else f"{single_line[:limit - 3]}..."

    @staticmethod
    def template_help() -> str:
        """Return supported placeholders for custom templates."""
        return "{quoted_csv} {json_items} {lines} {bullets} {numbered} {quotes}"

    def _replacement_map(self, items: list[str]) -> dict[str, str]:
        quoted = [json.dumps(item) for item in items]
        return {
            "quoted_csv": ", ".join(quoted),
            "json_items": ", ".join(quoted),
            "lines": "\n".join(items),
            "bullets": "\n".join(f"- {item}" for item in items),
            "numbered": "\n".join(f"{index}. {item}" for index, item in enumerate(items, start=1)),
            "quotes": "\n".join(f"> {item}" for item in items),
        }

    def _push_history(self, text: str) -> None:
        self._history = [item for item in self._history if item != text]
        self._history.insert(0, text)
        del self._history[MAX_HISTORY_ITEMS:]

    def _save(self) -> None:
        self._settings.setValue(_HISTORY_KEY, json.dumps(self._history))
        self._settings.setValue(_TEMPLATES_KEY, json.dumps(self._custom_templates))
        self._settings.sync()

    def _load_json_list(self, key: str) -> list[str]:
        raw = self._settings.value(key, "[]")
        try:
            value = json.loads(str(raw))
        except (TypeError, ValueError):
            return []
        return [item for item in value if isinstance(item, str)]

    def _load_json_dict(self, key: str) -> dict[str, str]:
        raw = self._settings.value(key, "{}")
        try:
            value = json.loads(str(raw))
        except (TypeError, ValueError):
            return {}
        return {str(k): str(v) for k, v in value.items() if isinstance(v, str)}

    def _normalize_text(self, text: str) -> str | None:
        normalized = text.replace("\r\n", "\n").strip()
        return normalized or None

    def _normalize_name(self, name: str) -> str | None:
        normalized = " ".join(name.split()).strip()
        return None if not normalized else normalized

    def _normalize_template_body(self, body: str | None) -> str | None:
        if body is None:
            return None
        normalized = body.replace("\r\n", "\n").strip()
        return None if not normalized else normalized
