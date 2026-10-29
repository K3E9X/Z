"""Five-field cron expressions (minute hour day-of-month month day-of-week) with ranges, steps, lists and
names, evaluated in an IANA time zone. Day-of-month and day-of-week combine with OR when both are restricted,
like Vixie cron."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

MONTHS = {
    n: i for i, n in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)
}
DAYS = {n: i for i, n in enumerate(["sun", "mon", "tue", "wed", "thu", "fri", "sat"])}
FIELDS: tuple[tuple[int, int, dict[str, int]], ...] = (
    (0, 59, {}),
    (0, 23, {}),
    (1, 31, {}),
    (1, 12, MONTHS),
    (0, 7, DAYS),
)
ALIASES = {
    "@hourly": "0 * * * *",
    "@daily": "0 0 * * *",
    "@weekly": "0 0 * * 0",
    "@monthly": "0 0 1 * *",
    "@yearly": "0 0 1 1 *",
}


class CronError(ValueError):
    pass


def _value(token: str, names: dict[str, int]) -> int:
    token = token.lower()
    if token in names:
        return names[token]
    if not token.isdigit():
        raise CronError(f"invalid value {token!r}")
    return int(token)


def _field(spec: str, low: int, high: int, names: dict[str, int]) -> tuple[set[int], bool]:
    values: set[int] = set()
    for part in spec.split(","):
        step = 1
        if "/" in part:
            part, raw = part.split("/", 1)
            if not raw.isdigit() or int(raw) < 1:
                raise CronError(f"invalid step {raw!r}")
            step = int(raw)
        if part == "*":
            start, end = low, high
        elif "-" in part:
            a, b = part.split("-", 1)
            start, end = _value(a, names), _value(b, names)
        else:
            start = _value(part, names)
            end = high if step > 1 else start
        if not (low <= start <= high and low <= end <= high and start <= end):
            raise CronError(f"{part!r} out of range {low}-{high}")
        values.update(range(start, end + 1, step))
    return values, spec != "*"


class Cron:
    def __init__(self, expression: str, timezone: str = "UTC") -> None:
        expression = ALIASES.get(expression.strip(), expression.strip())
        parts = expression.split()
        if len(parts) != 5:
            raise CronError("a cron expression has five fields: minute hour day month weekday")
        parsed = [_field(p, lo, hi, names) for p, (lo, hi, names) in zip(parts, FIELDS, strict=True)]
        (
            (self.minutes, _),
            (self.hours, _),
            (self.days, self.dom_restricted),
            (self.months, _),
            (dow, self.dow_restricted),
        ) = parsed
        self.weekdays = {d % 7 for d in dow}
        try:
            self.tz = ZoneInfo(timezone)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise CronError(f"unknown time zone {timezone!r}") from exc

    def _day_ok(self, local: datetime) -> bool:
        dom = local.day in self.days
        dow = (local.isoweekday() % 7) in self.weekdays
        if self.dom_restricted and self.dow_restricted:
            return dom or dow
        return dom and dow

    def next_after(self, after: datetime) -> datetime:
        """First matching minute strictly after `after` (aware datetime), returned in UTC."""
        local = (after.astimezone(self.tz) + timedelta(minutes=1)).replace(second=0, microsecond=0)
        for _ in range(366 * 2):  # days
            if local.month in self.months and self._day_ok(local):
                for h in sorted(x for x in self.hours if x >= local.hour):
                    start = local.minute if h == local.hour else 0
                    for minute in sorted(x for x in self.minutes if x >= start):
                        candidate = local.replace(hour=h, minute=minute)
                        if candidate.astimezone(UTC) > after.astimezone(UTC):
                            return candidate.astimezone(UTC)
            local = (local + timedelta(days=1)).replace(hour=0, minute=0)
        raise CronError("the expression never fires")
