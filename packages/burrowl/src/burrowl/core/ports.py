"""跨层接口（Protocol）。服务只依赖这些接口，具体实现由组合根注入。"""
from typing import Any, Protocol


class IndexSink(Protocol):
    """记忆提交后的检索投影维护。索引失败不影响已保存的记录。"""

    def on_commit(self, owner_id: str, head: dict | None) -> None:
        """提交发布后调用：使该 Owner 的投影失效并（同步或排队）重建。"""

    def status(self, owner_id: str) -> dict[str, Any]:
        """返回 {"index_status": "indexed|pending|failed|not_configured", "details": {...}}。"""

    def on_owner_removed(self, owner_id: str) -> None:
        """对象移入回收站后调用（R22 D-08）：删除它的全部派生投影。"""


class NullIndex:
    """未配置检索时使用：如实报告 not_configured，而不是假装已索引。"""

    def on_commit(self, owner_id: str, head: dict | None) -> None:
        return None

    def status(self, owner_id: str) -> dict[str, Any]:
        return {"index_status": "not_configured", "details": {}}

    def on_owner_removed(self, owner_id: str) -> None:
        return None
