"""Pre-commit checks of the DNS zone files of infra/dns-zones."""

MIN_TTL = 300


def low_ttl_records(records: list[dict[str, object]], min_ttl: int = MIN_TTL) -> list[str]:
    """Names of the records whose TTL is below the minimum (a TTL equal to the minimum is allowed)."""
    return [str(r["name"]) for r in records if int(str(r["ttl"])) <= min_ttl]
