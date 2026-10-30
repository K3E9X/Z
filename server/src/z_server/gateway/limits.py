"""Rate limits (docs/05 §2 step 3) and per-connector call statistics for health (p50/p95, error rate).

Sliding one-minute windows kept in process memory. With several gateway replicas each enforces its share;
the Helm chart divides `per_minute` by the replica count (`gateway.replicas`) through Z_GATEWAY_REPLICAS.
"""

from __future__ import annotations

import os
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field

WINDOW_S = 60.0
STATS_SIZE = 500


@dataclass
class SlidingWindow:
    hits: dict[str, deque[float]] = field(default_factory=lambda: defaultdict(deque))

    def allow(self, key: str, limit: int, now: float | None = None) -> float:
        """0 when allowed (and counted), else the seconds to wait."""
        at = time.monotonic() if now is None else now
        q = self.hits[key]
        while q and q[0] <= at - WINDOW_S:
            q.popleft()
        if len(q) >= limit:
            return max(0.1, q[0] + WINDOW_S - at)
        q.append(at)
        return 0.0


def replica_share(limit: int) -> int:
    replicas = max(1, int(os.environ.get("Z_GATEWAY_REPLICAS", "1") or 1))
    return max(1, limit // replicas)


@dataclass
class CallStats:
    latencies: dict[str, deque[tuple[float, int, bool]]] = field(
        default_factory=lambda: defaultdict(lambda: deque(maxlen=STATS_SIZE))
    )

    def record(self, connector: str, latency_ms: int, ok: bool) -> None:
        self.latencies[connector].append((time.time(), latency_ms, ok))

    def summary(self, connector: str, since_s: float = 900.0) -> dict[str, float | int | None]:
        cutoff = time.time() - since_s
        rows = [r for r in self.latencies.get(connector, ()) if r[0] >= cutoff]
        if not rows:
            return {"calls": 0, "latency_p50_ms": None, "latency_p95_ms": None, "error_rate": None}
        values = sorted(r[1] for r in rows)

        def pct(p: float) -> int:
            return values[min(len(values) - 1, round(p * (len(values) - 1)))]

        return {
            "calls": len(rows),
            "latency_p50_ms": pct(0.5),
            "latency_p95_ms": pct(0.95),
            "error_rate": round(sum(1 for r in rows if not r[2]) / len(rows), 4),
        }
