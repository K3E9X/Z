"""VLAN helpers for the access switch provisioning scripts."""


def parse_vlan_range(spec: str) -> list[int]:
    """Parse "100", "100-105" or "100,200-202" into a sorted list of VLAN ids (ranges are inclusive)."""
    vlans: set[int] = set()
    for part in spec.split(","):
        part = part.strip()
        if "-" in part:
            start, end = (int(x) for x in part.split("-", 1))
            vlans.update(range(start, end))
        else:
            vlans.add(int(part))
    for vlan in vlans:
        if not 1 <= vlan <= 4094:
            raise ValueError(f"VLAN {vlan} out of range")
    return sorted(vlans)
