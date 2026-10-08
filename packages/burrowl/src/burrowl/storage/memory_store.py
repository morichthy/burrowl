"""单 Owner 的不可变记忆提交。HEAD.json 的原子替换是唯一可见性边界。

目录（<memory_home>/）：
    owner.json                            对象描述
    HEAD.json                             {commit_id, generation, manifest_hash}
    commits/<COM-id>/manifest.json        本次提交后的全部记录指针、幂等账本、父提交
    commits/<COM-id>/records/<MEM-id>.json  本次变化的完整记录（旧修订留在旧提交里）
    commits/<COM-id>/receipt.json         提交回执
    request-receipts/<request_id>.json    "无变化"请求的幂等回执
    staging/                              发布前的暂存（崩溃残留由 recovery 检查）
    write.lock                            写锁
"""
from collections.abc import Callable
from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path

from ..core.atomic import Fault, exclusive_lock, no_fault, read_json, write_json_atomic, write_new_json
from ..core.clock import Clock, utc_now
from ..core.errors import BurrowlError
from ..core.hashing import canonical_hash
from ..core.ids import new_id, safe_segment
from ..core.pathsafe import ReadScope, safe_path
from ..core.records import CONTENT_FIELDS, content_hash, record_hash
from .owners import Owner

MANIFEST_FIELDS = {"schema_version", "commit_id", "generation", "owner_id", "record_heads", "request_ledger",
                   "parent_commit_id", "parent_manifest_hash"}
POINTER_KINDS = ("policy", "goal", "checkpoint")


@dataclass
class Snapshot:
    head: dict | None
    manifest: dict | None
    records: dict[str, dict] = field(default_factory=dict)

    @property
    def commit_id(self) -> str | None:
        return self.head["commit_id"] if self.head else None

    @property
    def generation(self) -> int:
        return self.head["generation"] if self.head else 0

    def pointer(self, kind: str) -> dict | None:
        ref = (self.manifest or {}).get("pointers", {}).get(kind)
        return self.records.get(ref["record_id"]) if ref else None


# prepare(snapshot) → (变化的完整记录列表, 逐条结果)
Prepare = Callable[[Snapshot], tuple[list[dict], list[dict]]]


class MemoryStore:
    def __init__(self, root: Path, *, clock: Clock = utc_now, id_factory: Callable[[], str] | None = None,
                 fault: Fault | None = None):
        self.root = Path(root).resolve()
        self.clock = clock
        self.new_commit_id = id_factory or (lambda: new_id("COM"))
        self.fault = fault or no_fault

    def path(self, owner: Owner, relative: str = "", scope: ReadScope | None = None) -> Path:
        locator = owner.memory_home + ("/" + relative if relative else "")
        return safe_path(self.root, locator, scope)

    # ---------- 读取 ----------
    def read_snapshot(self, owner: Owner) -> Snapshot:
        scope = ReadScope()
        head, manifest = self._head(owner, scope)
        records = ({rid: self._record(owner, rid, entry, scope) for rid, entry in manifest["record_heads"].items()}
                   if manifest else {})
        scope.recheck()
        return Snapshot(head, manifest, records)

    def _head(self, owner: Owner, scope: ReadScope) -> tuple[dict | None, dict | None]:
        """HEAD 与其提交清单（不读记录正文）。"""
        head_path = self.path(owner, "HEAD.json", scope)
        if not head_path.exists():
            return None, None
        head = read_json(head_path)
        if not isinstance(head, dict) or set(head) != {"commit_id", "generation", "manifest_hash"}:
            raise BurrowlError("INTEGRITY_ERROR", "HEAD 结构损坏", {"owner_id": owner.owner_id})
        manifest = self._manifest(owner, head["commit_id"], head["manifest_hash"], scope)
        if manifest["generation"] != head["generation"]:
            raise BurrowlError("INTEGRITY_ERROR", "HEAD 与提交清单代次不一致", {"owner_id": owner.owner_id})
        return head, manifest

    def _manifest(self, owner: Owner, commit_id: str, expected_hash: str, scope: ReadScope | None = None) -> dict:
        value = read_json(self.path(owner, f"commits/{safe_segment(commit_id)}/manifest.json", scope))
        if canonical_hash(value) != expected_hash:
            raise BurrowlError("INTEGRITY_ERROR", "提交清单指纹不匹配", {"commit_id": commit_id})
        if (not isinstance(value, dict) or not MANIFEST_FIELDS <= value.keys() or value["schema_version"] != 1
                or value["commit_id"] != commit_id or type(value["generation"]) is not int or value["generation"] < 1
                or not isinstance(value["record_heads"], dict) or not isinstance(value["request_ledger"], dict)
                or value["owner_id"] != owner.owner_id):
            raise BurrowlError("INTEGRITY_ERROR", "提交清单结构、版本或归属损坏", {"commit_id": commit_id})
        return value

    def _record(self, owner: Owner, rid: str, entry: dict, scope: ReadScope | None = None) -> dict:
        rid = safe_segment(rid)
        if not isinstance(entry, dict) or not {"commit_id", "path", "revision", "record_hash"} <= entry.keys():
            raise BurrowlError("INTEGRITY_ERROR", "记录指针结构损坏", {"record_id": rid})
        expected = f"commits/{safe_segment(entry['commit_id'])}/records/{rid}.json"
        if entry["path"] != expected:
            raise BurrowlError("INTEGRITY_ERROR", "记录定位与身份不一致", {"record_id": rid})
        value = read_json(self.path(owner, expected, scope))
        if (not isinstance(value, dict) or not set(CONTENT_FIELDS) <= value.keys()
                or value.get("record_id") != rid or value.get("owner_id") != owner.owner_id
                or value.get("revision") != entry["revision"] or value.get("record_hash") != entry["record_hash"]
                or record_hash(value) != entry["record_hash"] or content_hash(value) != value.get("content_hash")):
            raise BurrowlError("INTEGRITY_ERROR", "记录身份、版本或指纹不匹配", {"record_id": rid})
        return value

    def iter_manifests(self, owner: Owner, snapshot: Snapshot | None = None, scope: ReadScope | None = None):
        """从 HEAD 沿父链遍历全部提交清单（每份都校验父指纹）。

        scope：调用方提供时在整次遍历中复用目录枚举（调用方负责最后 recheck）。
        """
        manifest = snapshot.manifest if snapshot else self._head(owner, scope or ReadScope())[1]
        seen: set[str] = set()
        while manifest:
            if manifest["commit_id"] in seen:
                raise BurrowlError("INTEGRITY_ERROR", "历史提交出现循环")
            seen.add(manifest["commit_id"])
            yield manifest
            parent = manifest["parent_commit_id"]
            manifest = self._manifest(owner, parent, manifest["parent_manifest_hash"], scope) if parent else None

    def read_record(self, owner: Owner, record_id: str, revision: int | None = None) -> dict:
        """读取当前或指定修订；旧修订沿提交链查找（同一次查找复用目录枚举，返回前统一复核父目录）。"""
        scope = ReadScope()
        for manifest in self.iter_manifests(owner, scope=scope):
            entry = manifest["record_heads"].get(record_id)
            if entry and (revision is None or entry["revision"] == revision):
                record = self._record(owner, record_id, entry, scope)
                scope.recheck()
                return record
        scope.recheck()
        raise BurrowlError("NOT_FOUND", "记录或指定修订不存在", {"record_id": record_id, "revision": revision})

    def read_history(self, owner: Owner, snapshot: Snapshot | None = None) -> list[dict]:
        """全部记录的全部修订（每个唯一修订只读一次）。"""
        snapshot = snapshot or self.read_snapshot(owner)
        found: dict[tuple[str, int], dict] = {}
        for manifest in self.iter_manifests(owner, snapshot):
            for rid, entry in manifest["record_heads"].items():
                key = (rid, entry["revision"])
                if key not in found:
                    latest = snapshot.records.get(rid)
                    found[key] = latest if latest and latest["revision"] == entry["revision"] \
                        else self._record(owner, rid, entry)
        return list(found.values())

    def find_receipt(self, owner: Owner, snapshot: Snapshot, request_id: str, request_hash: str) -> dict | None:
        """幂等：同一 request_id 重试返回原回执；内容不同则拒绝。"""
        entry = (snapshot.manifest or {}).get("request_ledger", {}).get(request_id)
        if entry:
            value = read_json(self.path(owner, f"commits/{safe_segment(entry['commit_id'])}/receipt.json"))
            if canonical_hash(value) != entry["receipt_hash"]:
                raise BurrowlError("INTEGRITY_ERROR", "回执指纹不匹配")
            original_hash = entry["request_hash"]
        else:
            path = self.path(owner, f"request-receipts/{safe_segment(request_id)}.json")
            if not path.exists():
                return None
            envelope = read_json(path)
            if (not isinstance(envelope, dict)
                    or canonical_hash({k: v for k, v in envelope.items() if k != "envelope_hash"}) != envelope.get("envelope_hash")
                    or canonical_hash(envelope.get("receipt")) != envelope.get("receipt_hash")):
                raise BurrowlError("INTEGRITY_ERROR", "无变化回执损坏")
            value, original_hash = envelope["receipt"], envelope["request_hash"]
        if original_hash != request_hash:
            raise BurrowlError("IDEMPOTENCY_CONFLICT", "该请求 ID 已用于不同内容", {"original_receipt": value})
        return value

    # ---------- 写入 ----------
    def commit(self, owner: Owner, *, expected_head: str | None, request_id: str, request_hash: str,
               prepare: Prepare, prepublish: Callable[[], None] = lambda: None,
               source_checks: Callable[[], list] = list) -> dict:
        """持锁后：幂等检查 → HEAD 比较 → prepare → 暂存回读 → 改名 → 原子替换 HEAD。

        prepare 在锁内运行，必须是纯计算（不调用模型、网络或子进程）。
        prepublish 在发布前再核对外部依据（来源、其他对象 HEAD）。
        """
        safe_segment(request_id)
        home = self.path(owner)
        home.mkdir(parents=True, exist_ok=True)
        lock_path = self.path(owner, "write.lock")
        with exclusive_lock(lock_path, f"memory-commit:{owner.owner_id}", clock=self.clock) as lock:
            snapshot = self.read_snapshot(owner)
            if prior := self.find_receipt(owner, snapshot, request_id, request_hash):
                return prior
            if expected_head != snapshot.commit_id:
                raise BurrowlError("VERSION_CONFLICT", "对象已更新，请基于当前版本重试",
                               {"expected_head": expected_head, "current_head": snapshot.head})
            changed, results = prepare(snapshot)
            generation = snapshot.generation + (1 if changed else 0)
            commit_id = self.new_commit_id() if changed else snapshot.commit_id
            receipt = {"request_id": request_id, "owner_id": owner.owner_id, "commit_id": commit_id,
                       "generation": generation, "save_status": "committed" if changed else "no_change",
                       "record_results": [{**r, "status": {"would_create": "created", "would_update": "updated"}
                                           .get(r["status"], r["status"])} for r in results]}
            prepublish()
            if not changed:
                self._write_no_change(owner, request_id, request_hash, receipt)
                return receipt
            self._publish(owner, snapshot, changed, receipt, request_hash, source_checks(),
                          lambda: (prepublish(), self._check_lock(lock_path, lock)))
            return receipt

    @staticmethod
    def _check_lock(path: Path, lock: dict) -> None:
        if read_json(path) != lock:
            raise BurrowlError("LOCKED", "写锁身份变化，拒绝发布")

    def _write_no_change(self, owner: Owner, request_id: str, request_hash: str, receipt: dict) -> None:
        folder = self.path(owner, "request-receipts")
        folder.mkdir(exist_ok=True)
        envelope = {"request_hash": request_hash, "receipt": receipt, "receipt_hash": canonical_hash(receipt)}
        envelope["envelope_hash"] = canonical_hash(envelope)
        write_json_atomic(self.path(owner, f"request-receipts/{request_id}.json"), envelope)

    def _publish(self, owner: Owner, snapshot: Snapshot, changed: list[dict], receipt: dict, request_hash: str,
                 checks: list, prepublish: Callable[[], None]) -> None:
        commit_id, generation = receipt["commit_id"], receipt["generation"]
        safe_segment(commit_id)
        staging = self.path(owner, f"staging/{receipt['request_id']}-{new_id('S')[2:].replace('-', '')}")
        staging.mkdir(parents=True)
        (staging / "records").mkdir()
        heads = deepcopy((snapshot.manifest or {}).get("record_heads", {}))
        pointers = deepcopy((snapshot.manifest or {}).get("pointers", dict.fromkeys(POINTER_KINDS)))
        for record in changed:
            rid = safe_segment(record["record_id"])
            write_new_json(staging / "records" / f"{rid}.json", record, self.fault)
            heads[rid] = {"commit_id": commit_id, "path": f"commits/{commit_id}/records/{rid}.json",
                          "revision": record["revision"], "record_hash": record["record_hash"]}
            if record["kind"] in pointers:
                pointers[record["kind"]] = {"record_id": rid, "revision": record["revision"]}
        ledger = deepcopy((snapshot.manifest or {}).get("request_ledger", {}))
        ledger[receipt["request_id"]] = {"commit_id": commit_id, "request_hash": request_hash,
                                         "receipt_hash": canonical_hash(receipt)}
        manifest = {"schema_version": 1, "commit_id": commit_id, "parent_commit_id": snapshot.commit_id,
                    "parent_manifest_hash": snapshot.head["manifest_hash"] if snapshot.head else None,
                    "generation": generation, "owner_id": owner.owner_id, "record_heads": heads,
                    "pointers": pointers, "request_ledger": ledger,
                    "changed_ids": [r["record_id"] for r in changed], "source_checks": checks}
        write_new_json(staging / "receipt.json", receipt, self.fault)
        write_new_json(staging / "manifest.json", manifest, self.fault)
        # 发布前回读暂存字节：写入"成功"但内容有误的文件绝不能进入 HEAD。
        if canonical_hash(read_json(staging / "manifest.json")) != canonical_hash(manifest) or any(
                read_json(staging / "records" / f"{r['record_id']}.json") != r for r in changed):
            raise BurrowlError("INTEGRITY_ERROR", "暂存内容校验失败")
        prepublish()
        destination = self.path(owner, f"commits/{commit_id}")
        destination.parent.mkdir(exist_ok=True)
        staging.rename(destination)
        self.fault("after_rename")
        if self.read_snapshot(owner).head != snapshot.head:
            raise BurrowlError("VERSION_CONFLICT", "发布前 HEAD 发生变化")
        new_head = {"commit_id": commit_id, "generation": generation, "manifest_hash": canonical_hash(manifest)}
        self.fault("before_head")
        prepublish()
        write_json_atomic(self.path(owner, "HEAD.json"), new_head, fault=self.fault)
        self.fault("after_head")

    # ---------- 迁移/导入专用 ----------
    def snapshot_at(self, owner: Owner, commit_id: str) -> Snapshot:
        """某个历史提交时刻的完整快照（用于"自某提交以来的变化"）。"""
        for manifest in self.iter_manifests(owner):
            if manifest["commit_id"] == commit_id:
                records = {rid: self._record(owner, rid, entry) for rid, entry in manifest["record_heads"].items()}
                head = {"commit_id": commit_id, "generation": manifest["generation"],
                        "manifest_hash": canonical_hash(manifest)}
                return Snapshot(head, manifest, records)
        raise BurrowlError("NOT_FOUND", "提交不在当前历史链上", {"commit_id": commit_id})

