"""Parse the flat inventory export (one `hostname;site;role` per line) used by the ops scripts."""


def parse_inventory(text: str) -> list[dict[str, str]]:
    hosts = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        hostname, site, role = (field.strip() for field in line.split(";"))
        hosts.append({"hostname": hostname, "site": site, "role": role})
    return hosts
