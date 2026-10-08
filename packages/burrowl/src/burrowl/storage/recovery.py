"""记忆目录的健康检查。只报告，不自动发布孤立提交、不删除历史。

写锁的残留由 core.atomic.exclusive_lock 在下次写入时自动处理（仅当能证明持有进程已退出）。
"""
from typing import Any

from ..core.atomic import holder_is_dead, read_json
from .memory_store import MemoryStore
from .owners import Owner


def inspect(store: MemoryStore, owner: Owner) -> dict[str, Any]:
    snapshot = store.read_snapshot(owner)
    reachable = {m["commit_id"] for m in store.iter_manifests(owner, snapshot)}
    lock_path = store.path(owner, "write.lock")
    lock, lock_state = None, "absent"
    if lock_path.exists():
        lock = read_json(lock_path)
        lock_state = "exited" if isinstance(lock, dict) and holder_is_dead(lock) else "active_or_unknown"
    commits, staging = store.path(owner, "commits"), store.path(owner, "staging")
    orphans = sorted(p.name for p in commits.iterdir() if p.is_dir() and p.name not in reachable) \
        if commits.exists() else []
    staged = sorted(p.name for p in staging.iterdir() if p.is_dir()) if staging.exists() else []
    return {"owner_id": owner.owner_id, "head": snapshot.head, "lock": lock, "lock_state": lock_state,
            "unreachable_commits": orphans, "uncommitted_staging": staged,
            "advice": "孤立提交不会自动接入 HEAD；用原 request_id 重试即可得到幂等结果"}
