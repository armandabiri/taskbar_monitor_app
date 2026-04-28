"""Windows notification service using win11toast."""

import logging

from win11toast import toast

from core.config import APP_NAME
from services.resource_control.models import ReleaseResult

LOGGER = logging.getLogger(__name__)


class NotificationService:
    """Helper class to show system notifications using win11toast."""

    last_error: str | None = None

    @classmethod
    def notify(cls, title: str, message: str) -> bool:
        """Show a native Windows 11 notification toast.

        Returns True on success, False on failure (with last_error populated).
        """
        try:
            toast(title, message, app_id=APP_NAME)
            cls.last_error = None
            return True
        except Exception as exc:  # pylint: disable=broad-exception-caught
            cls.last_error = f"{type(exc).__name__}: {exc}"
            LOGGER.warning("Notification failed: %s", cls.last_error)
            return False

    @classmethod
    def notify_cleanup(cls, title: str, result: ReleaseResult) -> bool:
        """Show a cleanup-specific toast."""

        return cls.notify(title, result.details)
