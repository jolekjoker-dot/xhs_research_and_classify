"""Full-link tracing — records timing and data for each retrieval/LLM step"""

import time
from dataclasses import dataclass, field


@dataclass
class Span:
    """a single step in the trace"""
    step: str
    detail: str = ""
    duration_ms: float = 0
    data: dict = field(default_factory=dict)


class Trace:
    """collects spans across a retrieval → rerank → LLM pipeline"""

    def __init__(self):
        self.spans: list[Span] = []
        self._timers: dict[str, float] = {}

    def start(self, step: str, detail: str = "") -> None:
        self._timers[step] = time.perf_counter()

    def end(self, step: str, detail: str = "", **data) -> Span:
        t0 = self._timers.pop(step, time.perf_counter())
        span = Span(
            step=step,
            detail=detail,
            duration_ms=round((time.perf_counter() - t0) * 1000, 1),
            data=data,
        )
        self.spans.append(span)
        return span

    def total_ms(self) -> float:
        return round(sum(s.duration_ms for s in self.spans), 1)

    def summary(self) -> str:
        lines = ["检索追踪:"]
        for s in self.spans:
            detail = f" — {s.detail}" if s.detail else ""
            lines.append(f"  [{s.step}] {s.duration_ms}ms{detail}")
        lines.append(f"  总耗时: {self.total_ms()}ms")
        return "\n".join(lines)

    def to_dict(self) -> dict:
        return {
            "steps": [{"step": s.step, "detail": s.detail, "duration_ms": s.duration_ms, "data": s.data} for s in self.spans],
            "total_ms": self.total_ms(),
        }
