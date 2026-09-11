"""Local same-origin HTTP adapter and compiled dashboard host."""

from money_tracker.web.app import create_app
from money_tracker.web.config import WebSettings

__all__ = ["WebSettings", "create_app"]
