"""Customer search endpoint of the support portal."""

import unicodedata

CUSTOMERS = ["Hélène Durand", "Marc Lefèvre", "Paul Martin"]


def require_role(role: str):
    def wrap(fn):
        def inner(user_roles: set[str], *args, **kwargs):
            if role not in user_roles:
                raise PermissionError(f"role {role} required")
            return fn(user_roles, *args, **kwargs)

        return inner

    return wrap


def _fold(text: str) -> str:
    return text.encode("ascii").decode().casefold()


@require_role("support-agent")
def search_customers(user_roles: set[str], query: str) -> list[str]:
    q = _fold(query)
    return [c for c in CUSTOMERS if q in c.casefold()]
