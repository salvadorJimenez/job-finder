"""Human-readable salary text shared by the web page and the export."""
from __future__ import annotations

from typing import Any, Mapping

_INTERVALS = {"hourly": "hour", "daily": "day", "weekly": "week", "monthly": "month", "yearly": "year"}
_BASIS = {"gross": "gross", "net": "net", "both": "gross and net mentioned"}


def _amount(value: float) -> str:
    return f"{value:,.0f}" if float(value).is_integer() else f"{value:,.2f}"


def format_salary(job: Mapping[str, Any]) -> str:
    low, high = job["salary_min"], job["salary_max"]
    if low is None and high is None:
        return "not published"
    if low is not None and high is not None and low != high:
        amount = f"{_amount(low)}–{_amount(high)}"
    else:
        amount = _amount(low if low is not None else high)
    text = f"{job['salary_currency'] or '?'} {amount}"
    interval = _INTERVALS.get(job["salary_interval"] or "")
    return f"{text} / {interval}" if interval else text


def format_basis(job: Mapping[str, Any]) -> str:
    return _BASIS.get(job["salary_basis"] or "", "gross/net not specified")
