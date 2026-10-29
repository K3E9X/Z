"""MAC address helpers for the CPE (customer box) provisioning API."""

import re

_HEX12 = re.compile(r"^[0-9a-f]{12}$")


def normalize_mac(value: str) -> str:
    """Return the MAC as aa:bb:cc:dd:ee:ff. Accepts colons or no separator."""
    raw = value.strip().lower().replace(":", "")
    if not _HEX12.match(raw):
        raise ValueError(f"invalid MAC address: {value!r}")
    return ":".join(raw[i : i + 2] for i in range(0, 12, 2))
