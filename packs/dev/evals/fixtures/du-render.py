"""Weekly availability report: percentage per site."""


def availability(up_minutes: int, total_minutes: int) -> str:
    if total_minutes <= 0:
        raise ValueError("total_minutes must be positive")
    return f"{100 * up_minutes / total_minutes:.2f} %"
