"""A daily cap on the agent's OpenAI spending, shared by all requests.

The last line of defence in code: the per-IP rate limit can be dodged with many
addresses. The count lives in memory (lost on restart) and a ticket already running can
go slightly over, so an OpenAI project spending limit is the hard cap.
"""

from __future__ import annotations

import datetime
import threading
from collections.abc import Callable


def utc_today() -> datetime.date:
    """Return the current date in UTC."""
    return datetime.datetime.now(datetime.timezone.utc).date()


class DailyBudget:
    """Money spent today against a limit. The day is UTC; `today` is the clock, so a
    test can pass a fake one."""

    def __init__(
        self, limit_usd: float, today: Callable[[], datetime.date] = utc_today
    ) -> None:
        self._today = today
        self._day = today()
        self._spent = 0.0
        self._lock = threading.Lock()
        self._limit_usd = limit_usd

    def _roll_over(self) -> None:
        """Start a new day's count when the date has changed. Call with the lock
        held."""
        today = self._today()
        if self._day != today:
            self._day = today
            self._spent = 0.0

    def exhausted(self) -> bool:
        """True once today's spending has reached the limit."""
        with self._lock:
            self._roll_over()
            return self._spent >= self._limit_usd

    def add(self, cost: float) -> None:
        """Count one ticket's cost against today."""
        with self._lock:
            self._roll_over()
            self._spent += cost

    def spent_today(self) -> float:
        """Return the total spent today, in USD."""
        with self._lock:
            self._roll_over()
            return self._spent
