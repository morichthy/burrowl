"""通用"版本化文档"：HEAD + 不可变修订 + 比较并交换（CAS）。

阅读会话、维护计划等可变工作状态共用这一实现，替代旧系统里各写一套锁和原子写。
目录布局：
    <dir>/HEAD.json          {"revision": n, "digest": sha256, "updated_at": ...}
    <dir>/revisions/<n>.json 不可变的完整快照
    <dir>/write.lock         写入期间的排他锁
"""
import copy
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .atomic import exclusive_lock, read_json, write_json_atomic, write_new_json
from .clock import Clock, utc_now
from .errors import BurrowlError
from .hashing import canonical_hash


class RevisionedDoc:
    def __init__(self, directory: Path, *, clock: Clock = utc_now):
        self.dir = Path(directory)
        self.clock = clock

    @property
    def exists(self) -> bool:
        return (self.dir / "HEAD.json").is_file()

    def _revision_path(self, revision: int) -> Path:
        return self.dir / "revisions" / f"{revision:06d}.json"

    def head(self) -> dict[str, Any]:
        head = read_json(self.dir / "HEAD.json")
        if not isinstance(head, dict) or type(head.get("revision")) is not int:
            raise BurrowlError("INTEGRITY_ERROR", "版本化文档 HEAD 损坏", {"dir": self.dir.name})
        return head

    def read(self, revision: int | None = None) -> tuple[dict[str, Any], int]:
        """返回 (内容, 修订号)。读取时校验指纹，损坏即报错而不是返回部分数据。"""
        head = self.head()
        number = head["revision"] if revision is None else revision
        value = read_json(self._revision_path(number))
        if revision is None and canonical_hash(value) != head["digest"]:
            raise BurrowlError("INTEGRITY_ERROR", "版本化文档内容指纹不匹配", {"dir": self.dir.name})
        return value, number

    def create(self, value: dict[str, Any]) -> int:
        (self.dir / "revisions").mkdir(parents=True, exist_ok=True)
        with exclusive_lock(self.dir / "write.lock", "revdoc-create", clock=self.clock):
            if self.exists:
                raise BurrowlError("VERSION_CONFLICT", "文档已存在")
            return self._publish(value, 1)

    def update(self, expected_revision: int | None,
               change: Callable[[dict[str, Any]], dict[str, Any] | None]) -> tuple[dict[str, Any], int]:
        """在锁内读取最新内容并应用 change。

        expected_revision 为 None 时表示"基于最新版本"——调用方接受服务端串行化；
        给出具体值时不一致即 VERSION_CONFLICT。change 返回 None 表示无变化。
        """
        with exclusive_lock(self.dir / "write.lock", "revdoc-update", clock=self.clock):
            current, revision = self.read()
            if expected_revision is not None and expected_revision != revision:
                raise BurrowlError("VERSION_CONFLICT", "文档已被更新，请基于最新版本重试",
                               {"expected_revision": expected_revision, "current_revision": revision})
            # 传入副本：change 可以就地修改并返回它，比较时仍能看出变化。
            updated = change(copy.deepcopy(current))
            if updated is None or canonical_hash(updated) == canonical_hash(current):
                return current, revision
            return updated, self._publish(updated, revision + 1)

    def _publish(self, value: dict[str, Any], revision: int) -> int:
        write_new_json(self._revision_path(revision), value)
        write_json_atomic(self.dir / "HEAD.json", {"revision": revision, "digest": canonical_hash(value),
                                                    "updated_at": self.clock()})
        return revision
