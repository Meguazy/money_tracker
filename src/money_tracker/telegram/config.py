"""Secure, mutually exclusive Telegram bot configuration."""

from __future__ import annotations

import os
import stat
from dataclasses import dataclass, field
from pathlib import Path

from money_tracker.messaging import BotEnvironment, MessagingScope

_PRODUCTION_TOKEN_FILE = "MONEY_TRACKER_TELEGRAM_PRODUCTION_TOKEN_FILE"
_TEST_TOKEN_FILE = "MONEY_TRACKER_TELEGRAM_TEST_TOKEN_FILE"


def _positive_float(name: str, default: float) -> float:
    """Read a positive duration without reflecting its raw value in errors."""

    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        value = float(raw)
    except ValueError as error:
        raise ValueError(f"{name} must be a number") from error
    if value <= 0:
        raise ValueError(f"{name} must be positive")
    return value


@dataclass(frozen=True, slots=True)
class TelegramSettings:
    """Non-secret Telegram runtime settings and selected credential file.

    Attributes:
        environment: Explicit production or test bot selection.
        bot_key: Stable non-secret persistence scope.
        token_file: Owner-only file selected for this environment.
        connect_timeout_seconds: Maximum TCP/TLS connection duration.
        request_timeout_seconds: Maximum non-poll request duration.
        poll_timeout_seconds: Bot API long-poll duration.
        retry_base_seconds: First transport retry delay.
        retry_maximum_seconds: Maximum transport retry delay.
    """

    environment: BotEnvironment
    bot_key: str
    token_file: Path = field(repr=False)
    connect_timeout_seconds: float = 5.0
    request_timeout_seconds: float = 10.0
    poll_timeout_seconds: float = 20.0
    retry_base_seconds: float = 2.0
    retry_maximum_seconds: float = 60.0

    def __post_init__(self) -> None:
        """Reject durations that could make shutdown or retry unbounded."""

        if not 0 < self.connect_timeout_seconds <= 30:
            raise ValueError("Telegram connect timeout must be at most 30 seconds")
        if not 0 < self.request_timeout_seconds <= 60:
            raise ValueError("Telegram request timeout must be at most 60 seconds")
        if not 1 <= self.poll_timeout_seconds <= 50:
            raise ValueError("Telegram poll timeout must be between 1 and 50 seconds")
        if not (0 < self.retry_base_seconds <= self.retry_maximum_seconds <= 300):
            raise ValueError("Telegram retry delays must be positive and bounded")

    @classmethod
    def from_env(cls) -> TelegramSettings:
        """Load one explicit bot environment without credential fallback.

        Returns:
            Validated non-secret settings for exactly one bot.

        Raises:
            RuntimeError: If the environment or either separate credential path
                is absent.
            ValueError: If environment or duration configuration is invalid.
        """

        scope = MessagingScope.from_env()
        environment = scope.environment
        production_path = os.getenv(_PRODUCTION_TOKEN_FILE, "").strip()
        test_path = os.getenv(_TEST_TOKEN_FILE, "").strip()
        if not production_path or not test_path:
            raise RuntimeError(
                f"{_PRODUCTION_TOKEN_FILE} and {_TEST_TOKEN_FILE} are required"
            )
        paths = {
            BotEnvironment.PRODUCTION: Path(production_path),
            BotEnvironment.TEST: Path(test_path),
        }
        if paths[BotEnvironment.PRODUCTION] == paths[BotEnvironment.TEST]:
            raise RuntimeError("production and test token files must be different")
        selected = paths[environment]
        if not selected.is_absolute():
            raise RuntimeError("Telegram token file paths must be absolute")
        return cls(
            environment=environment,
            bot_key=scope.bot_key,
            token_file=selected,
            connect_timeout_seconds=_positive_float(
                "MONEY_TRACKER_TELEGRAM_CONNECT_TIMEOUT_SECONDS", 5.0
            ),
            request_timeout_seconds=_positive_float(
                "MONEY_TRACKER_TELEGRAM_REQUEST_TIMEOUT_SECONDS", 10.0
            ),
            poll_timeout_seconds=_positive_float(
                "MONEY_TRACKER_TELEGRAM_POLL_TIMEOUT_SECONDS", 20.0
            ),
            retry_base_seconds=_positive_float(
                "MONEY_TRACKER_TELEGRAM_RETRY_BASE_SECONDS", 2.0
            ),
            retry_maximum_seconds=_positive_float(
                "MONEY_TRACKER_TELEGRAM_RETRY_MAXIMUM_SECONDS", 60.0
            ),
        )

    def read_token(self) -> str:
        """Read the selected owner-only regular file without following symlinks.

        Returns:
            The credential used only to construct the Bot API client.

        Raises:
            RuntimeError: If the file is unsafe, missing, oversized, malformed,
                not owned by the current user, or not mode 0600.
        """

        try:
            file_stat = self.token_file.lstat()
            if not stat.S_ISREG(file_stat.st_mode) or self.token_file.is_symlink():
                raise RuntimeError("Telegram token file must be a regular file")
            if stat.S_IMODE(file_stat.st_mode) != 0o600:
                raise RuntimeError("Telegram token file must have mode 0600")
            if file_stat.st_uid != os.getuid():
                raise RuntimeError("Telegram token file must be owned by this user")
            descriptor = os.open(
                self.token_file,
                os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
            )
            with os.fdopen(descriptor, encoding="utf-8") as handle:
                opened = os.fstat(handle.fileno())
                if (
                    opened.st_dev != file_stat.st_dev
                    or opened.st_ino != file_stat.st_ino
                    or not stat.S_ISREG(opened.st_mode)
                    or stat.S_IMODE(opened.st_mode) != 0o600
                    or opened.st_uid != os.getuid()
                ):
                    raise RuntimeError("Telegram token file changed during validation")
                token = handle.read(257).strip()
        except OSError:
            raise RuntimeError("Telegram token file cannot be read safely") from None
        if (
            len(token) > 256
            or len(token) < 20
            or any(character.isspace() for character in token)
        ):
            raise RuntimeError("Telegram token file contains an invalid credential")
        if ":" not in token or "/" in token:
            raise RuntimeError("Telegram token file contains an invalid credential")
        return token
