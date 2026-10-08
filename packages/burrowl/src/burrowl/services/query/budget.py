"""查询生命周期内的累计硬预算。活动执行时间与用户停留时间分开：只计实际处理的墙钟。

取消用独立的事件，不等待执行锁，所以长时间的召回也能被中途停止（已完成部分保留）。
"""
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager

from ...core.config import Budget
from ...core.errors import BurrowlError


class Ledger:
    def __init__(self, limits: Budget, *, clock=time.monotonic):
        self.limits = limits.model_dump()
        self.used = dict.fromkeys(self.limits, 0)
        self.clock = clock
        self.cancelled = threading.Event()
        self._lock = threading.RLock()
        self._started: float | None = None
        self._elapsed = 0.0

    def _elapsed_ms(self) -> int:
        running = (self.clock() - self._started) if self._started is not None else 0.0
        return int((self._elapsed + running) * 1000)

    def checkpoint(self) -> None:
        if self.cancelled.is_set():
            raise BurrowlError("BUDGET_EXCEEDED", "操作已取消，已完成部分保留", {"reason": "cancelled"})
        if self._elapsed_ms() >= self.limits["wall_ms"]:
            raise BurrowlError("BUDGET_EXCEEDED", "执行时间预算已用完", {"budget": "wall_ms"})

    def remaining(self, key: str) -> int:
        with self._lock:
            used = self._elapsed_ms() if key == "wall_ms" else self.used[key]
            return max(0, self.limits[key] - used)

    def can(self, key: str, amount: int) -> bool:
        return self.remaining(key) >= amount

    def charge(self, key: str, amount: int) -> None:
        """先检查再扣除；超出即拒绝（不会出现"先做了再说超支"）。"""
        self.checkpoint()
        if key == "wall_ms" or key not in self.used or amount < 0:
            raise ValueError(f"invalid budget charge: {key}")
        with self._lock:
            if self.used[key] + amount > self.limits[key]:
                raise BurrowlError("BUDGET_EXCEEDED", "已达到累计预算上限", {"budget": key,
                               "limit": self.limits[key], "used": self.used[key], "requested": amount})
            self.used[key] += amount

    def snapshot(self) -> dict[str, int]:
        with self._lock:
            return {**self.used, "wall_ms": self._elapsed_ms()}

    @contextmanager
    def active(self) -> Iterator[None]:
        """计量一段实际处理时间；同一查询不能并发执行两段处理。"""
        with self._lock:
            if self._started is not None:
                raise BurrowlError("LOCKED", "同一查询已有正在执行的操作")
            self._started = self.clock()
        try:
            self.checkpoint()
            yield
        finally:
            with self._lock:
                self._elapsed += self.clock() - self._started
                self._started = None
