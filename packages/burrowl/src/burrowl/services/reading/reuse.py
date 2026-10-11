"""筛选结果的进程内复用（R18 D-02）。

归属判定（memory.owner_route）之后为同一需求开阅读会话时，第一轮发现召回与判定是完全相同的计算。
输入（查询计划、语料、范围、过滤、条件、筛选与重排配置）和检索状态指纹（IndexService.search_state）
都相同时，直接返回上一次的结果；缺口由调用方现算后替换。

- 只是派生结果，不落盘：进程退出即丢失，丢了只是重新召回；CLI 每次是新进程，自然不复用。
- 有时限和条数上限；存取都做深拷贝，调用方修改返回值不影响缓存。
"""
import threading
import time
from collections import OrderedDict
from copy import deepcopy

from ...core.clock import utc_now

REUSE_SECONDS = 600  # 判定到开阅读通常在几分钟内；更久就重新召回，避免依赖长期驻留的结果
REUSE_ITEMS = 8


class ScreenReuse:
    def __init__(self, *, seconds: float = REUSE_SECONDS, items: int = REUSE_ITEMS):
        self.seconds = seconds
        self.items = items
        self._entries: OrderedDict[str, dict] = OrderedDict()
        self._lock = threading.Lock()

    def get(self, key: str, state: str) -> tuple[dict, dict] | None:
        """命中时返回 (结果副本, 来源 {origin, at, readiness_gaps})；检索状态变了或已超时返回 None。"""
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                return None
            if entry["state"] != state or time.monotonic() - entry["stored"] > self.seconds:
                del self._entries[key]
                return None
            self._entries.move_to_end(key)
            return deepcopy(entry["result"]), {"origin": entry["origin"], "at": entry["at"],
                                               "readiness_gaps": entry["readiness_gaps"]}

    def put(self, key: str, state: str, result: dict, origin: str, *, readiness_gaps: int) -> None:
        """readiness_gaps：result["gaps"] 开头有几条是就绪缺口（命中时由调用方换成现算的）。"""
        with self._lock:
            self._entries[key] = {"state": state, "result": deepcopy(result), "origin": origin,
                                  "readiness_gaps": readiness_gaps, "at": utc_now(), "stored": time.monotonic()}
            self._entries.move_to_end(key)
            while len(self._entries) > self.items:
                self._entries.popitem(last=False)
