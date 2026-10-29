"""Render the NAS client list of the RADIUS servers from a YAML-like dict."""


def render_clients(clients: dict[str, str]) -> str:
    """{"bras-lyo-01": "10.20.0.11"} -> FreeRADIUS `client` blocks (the rest of each block is added at deploy)."""
    blocks = []
    for name in sorted(clients):
        blocks.append(f"client {name} {{\n    ipaddr = {clients[name]}\n}}")
    return "\n".join(blocks) + "\n"
