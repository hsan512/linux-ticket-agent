"""Tests for the daily spending cap, with a fake clock so a day can pass instantly."""

from concurrent.futures import ThreadPoolExecutor
from datetime import date

from api.budget import DailyBudget


def _budget(limit: float = 1.0) -> tuple[DailyBudget, list[date]]:
    day = [date(2026, 11, 7)]
    return DailyBudget(limit, today=lambda: day[0]), day


def test_spending_adds_up_to_the_limit() -> None:
    budget, _ = _budget()
    budget.add(0.4)
    assert not budget.exhausted()
    budget.add(0.6)
    assert budget.exhausted()  # reaching the limit exactly counts as spent


def test_a_new_day_starts_from_zero() -> None:
    budget, day = _budget()
    budget.add(1.5)
    assert budget.exhausted()
    day[0] = date(2026, 11, 8)  # midnight passes, instantly
    assert not budget.exhausted()
    budget.add(0.9)
    assert not budget.exhausted()  # yesterday's spending no longer counts


def test_cost_added_on_a_new_day_is_not_lost() -> None:
    budget, day = _budget()
    budget.add(0.9)
    day[0] = date(2026, 11, 8)
    budget.add(1.0)  # first call of the day: reset, then add
    assert budget.exhausted()


def test_concurrent_adds_are_all_counted() -> None:
    budget, _ = _budget(limit=1_000_000)
    with ThreadPoolExecutor(8) as pool:
        list(pool.map(lambda _: budget.add(0.25), range(4000)))
    assert budget._spent == 1000.0  # 0.25 is exact in binary, so == is safe here
