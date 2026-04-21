"""Windows notification service using win11toast."""

import logging
from win11toast import toast
from core.config import APP_NAME

LOGGER = logging.getLogger(__name__)


class NotificationService:
    """Helper class to show system notifications using win11toast."""

    @classmethod
    def notify(cls, title: str, message: str) -> None:
        """Show a native Windows 11 notification toast."""
        try:
            # Adding app_id helps Windows group notifications and show them more reliably
            toast(title, message, app_id=APP_NAME)
        except Exception:  # pylint: disable=broad-exception-caught
            LOGGER.exception("Failed to show Windows notification via win11toast")
