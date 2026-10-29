"""Maintenance windows of the change calendar (local time, minutes are ignored)."""


def in_window(hour: int, start: int, end: int) -> bool:
    """True when `hour` is inside the window [start, end). Windows are given in whole hours."""
    return start <= hour < end
