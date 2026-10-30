"""MTU consistency check between both ends of a backbone link."""


def mtu_mismatch(a_mtu: int, b_mtu: int) -> bool:
    return a_mtu != b_mtu
