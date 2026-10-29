"""Interface rate computation for the SNMP poller (ifInOctets / ifOutOctets are Counter32)."""

COUNTER32_MODULO = 2**32


def rate_bps(prev_octets: int, curr_octets: int, interval_s: float) -> float:
    """Bits per second between two samples of an octet counter."""
    if interval_s <= 0:
        raise ValueError("interval must be positive")
    delta = curr_octets - prev_octets
    return delta * 8 / interval_s
