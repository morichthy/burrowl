"""单 Owner 的不可变记忆提交。HEAD.json 的原子替换是唯一可见性边界。

读取时 HEAD 与提交清单每次重读；记录正文按清单中的指纹走进程内已校验缓存（VerifiedRecords）。

目录（<memory_home>/）：
    owner.json                            对象描述
    HEAD.json                             {commit_id, generation, manifest_hash}
    commits/<COM-id>/manifest.json        本次提交后的全部记录指针、幂等账本、父提交
    commits/<COM-id>/records/<MEM-id>.json  本次变化的完整记录（旧修订留在旧提交里）
    commits/<COM-id>/receipt.json         提交回执
    （R22）清单的可选字段 removed_records（累积的墓碑）、removed_ids / restored_ids（本次删除 / 恢复）
    request-receipts/<request_id>.json    "无变化"请求的幂等回执
    staging/                              发布前的暂存（崩溃残留由 recovery 检查）
    write.lock                            写锁
"""
import json
import threading
from collections import OrderedDict
from collections.abc import Callable
from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path

from ..core.atomic import Fault, exclusive_lock, no_fault, read_json, write_json_atomic, write_new_json
from ..core.clock import Clock, utc_now
from ..core.errors import BurrowlError
from ..core.hashing import canonical_bytes, canonical_hash
from ..core.ids import new_id, safe_segment
from ..core.pathsafe import ReadScope, safe_path
from ..core.records import CONTENT_FIELDS, OPTIONAL_CONTENT_FIELDS, content_hash, record_hash
from .owners import Owner

MANIFEST_FIELDS = {"schema_version", "commit_id", "generation", "owner_id", "record_heads", "request_ledger",
                   "parent_commit_id", "parent_manifest_hash"}
POINTER_KINDS = ("policy", "goal", "checkpoint")


def tombstones(manifest: dict | None) -> dict[str, dict]:
    """R22 D-01：已删除记录的墓碑（记录 ID → 原指针、理由、时间、执行者）。旧清单没有这个字段，视为空。"""
    return (manifest or {}).get("removed_records") or {}


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


RECORD_CACHE_SIZE = 20_000  # 已校验记录缓存的条目上限；单条通常几 KB，满载约几十 MB


class VerifiedRecords:
    """进程内的已校验记录缓存（派生状态，丢了只是变慢）。

    键是 (memory_home, 记录文件相对路径, record_hash)。记录文件不可变，路径里含提交号；命中前提是本次
    重新读取（并做过路径检查）的提交清单仍指向同一指纹，所以返回的内容与重新读盘逐字节等价，其他进程
    的新提交会换掉清单里的指针，自然不命中。
    不覆盖的情形：首次读取之后磁盘上的记录文件被篡改或损坏，在进程重启前不会被发现。完整性检查
    （recovery、system.validate）用 fresh=True 绕过缓存。
    存规范化 JSON 文本、命中时重新解析：调用方拿到的是独立对象，修改它不会污染缓存。
    """

    def __init__(self, max_entries: int):
        self.max_entries = max_entries
        self._items: OrderedDict[tuple[str, str, str], str] = OrderedDict()
        self._lock = threading.Lock()  # 工作台用线程池处理请求

    def get(self, key: tuple[str, str, str]) -> dict | None:
        with self._lock:
            text = self._items.get(key)
            if text is None:
                return None
            self._items.move_to_end(key)
        return json.loads(text)

    def put(self, key: tuple[str, str, str], value: dict) -> None:
        if self.max_entries <= 0:
            return
        text = canonical_bytes(value).decode("utf-8")
        with self._lock:
            self._items[key] = text
            self._items.move_to_end(key)
            while len(self._items) > self.max_entries:
                self._items.popitem(last=False)

    def __len__(self) -> int:
        return len(self._items)


# prepare(snapshot) → (变化的完整记录列表, 逐条结果)
Prepare = Callable[[Snapshot], tuple[list[dict], list[dict]]]
# R22：plan(snapshot) → (要删除的记录 ID → {reason, actor}, 要恢复的记录 ID)；在锁内运行，必须是纯计算
RemovalPlan = Callable[[Snapshot], tuple[dict[str, dict], list[str]]]


class MemoryStore:
    def __init__(self, root: Path, *, clock: Clock = utc_now, id_factory: Callable[[], str] | None = None,
                 fault: Fault | None = None, record_cache_size: int = RECORD_CACHE_SIZE):
        self.root = Path(root).resolve()
        self.clock = clock
        self.new_commit_id = id_factory or (lambda: new_id("COM"))
        self.fault = fault or no_fault
        self.verified = VerifiedRecords(record_cache_size)

    def path(self, owner: Owner, relative: str = "", scope: ReadScope | None = None) -> Path:
        locator = owner.memory_home + ("/" + relative if relative else "")
        return safe_path(self.root, locator, scope)

    # ---------- 读取 ----------
    def read_snapshot(self, owner: Owner, *, fresh: bool = False) -> Snapshot:
        """HEAD、提交清单与全部当前记录。HEAD 与清单每次重新读取并做路径检查；记录正文命中已校验缓存时
        不再读盘（见 VerifiedRecords）。fresh=True 跳过缓存、逐条重读校验，供完整性检查使用。"""
        scope = ReadScope()
        head, manifest = self._head(owner, scope)
        records = ({rid: self._record(owner, rid, entry, scope, fresh=fresh)
                    for rid, entry in manifest["record_heads"].items()}
                   if manifest else {})
        scope.recheck()
        return Snapshot(head, manifest, records)

    def read_head(self, owner: Owner, pointers: tuple[str, ...] = ()) -> Snapshot:
        """只读 HEAD 与提交清单，外加 pointers 指定的指针记录（如 goal、checkpoint）。

        R13 D-07：判断水位新鲜度、列出续接线索时不需要全部记录；read_snapshot 会读入并校验每一条记录，
        成本随记录总数增长。返回的 Snapshot.records 只含请求的指针记录，不能当作完整快照使用。"""
        scope = ReadScope()
        head, manifest = self._head(owner, scope)
        records: dict[str, dict] = {}
        for kind in pointers if manifest else ():
            ref = manifest.get("pointers", {}).get(kind)
            entry = manifest["record_heads"].get(ref["record_id"]) if ref else None
            if entry is not None:
                records[ref["record_id"]] = self._record(owner, ref["record_id"], entry, scope)
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

    def _record(self, owner: Owner, rid: str, entry: dict, scope: ReadScope | None = None, *,
                fresh: bool = False) -> dict:
        rid = safe_segment(rid)
        if not isinstance(entry, dict) or not {"commit_id", "path", "revision", "record_hash"} <= entry.keys():
            raise BurrowlError("INTEGRITY_ERROR", "记录指针结构损坏", {"record_id": rid})
        expected = f"commits/{safe_segment(entry['commit_id'])}/records/{rid}.json"
        if entry["path"] != expected:
            raise BurrowlError("INTEGRITY_ERROR", "记录定位与身份不一致", {"record_id": rid})
        key = (owner.memory_home, expected, entry["record_hash"])
        if not fresh and (cached := self.verified.get(key)) is not None:
            return cached
        value = read_json(self.path(owner, expected, scope))
        if (not isinstance(value, dict) or not set(CONTENT_FIELDS) - OPTIONAL_CONTENT_FIELDS <= value.keys()
                or value.get("record_id") != rid or value.get("owner_id") != owner.owner_id
                or value.get("revision") != entry["revision"] or value.get("record_hash") != entry["record_hash"]
                or record_hash(value) != entry["record_hash"] or content_hash(value) != value.get("content_hash")):
            raise BurrowlError("INTEGRITY_ERROR", "记录身份、版本或指纹不匹配", {"record_id": rid})
        self.verified.put(key, value)
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

    def commit_records(self, owner: Owner, manifest: dict) -> list[dict]:
        """某次提交写入的记录（manifest.changed_ids 在该提交中的修订）。供历史类派生视图逐提交读取。"""
        scope = ReadScope()
        records = [self._record(owner, rid, manifest["record_heads"][rid], scope)
                   for rid in manifest.get("changed_ids", []) if rid in manifest["record_heads"]]
        scope.recheck()
        return records

    def read_receipt(self, owner: Owner, commit_id: str) -> dict:
        return read_json(self.path(owner, f"commits/{safe_segment(commit_id)}/receipt.json"))

    def read_record(self, owner: Owner, record_id: str, revision: int | None = None) -> dict:
        """读取当前或指定修订；旧修订沿提交链查找（同一次查找复用目录枚举，返回前统一复核父目录）。"""
        scope = ReadScope()
        for manifest in self.iter_manifests(owner, scope=scope):
            if revision is None and record_id in tombstones(manifest):
                # R22 D-02：已删除的记录没有"当前修订"；按修订号的固定引用照常沿提交链读取
                scope.recheck()
                stone = tombstones(manifest)[record_id]
                raise BurrowlError("NOT_FOUND", "记录已删除（可恢复）", {
                    "record_id": record_id, "removed": True, "removed_at": stone.get("removed_at"),
                    "revision": stone["entry"]["revision"], "action": "按修订号读取旧内容，或用 records_restore 恢复"})
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
               source_checks: Callable[[], list] = list, attribution: dict | None = None) -> dict:
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
            if attribution is not None:  # R13 D-06：归属确认点的说明随本次提交的回执保存
                receipt["attribution"] = deepcopy(attribution)
            prepublish()
            if not changed:
                self._write_no_change(owner, request_id, request_hash, receipt)
                return receipt
            self._publish(owner, snapshot, changed, receipt, request_hash, source_checks(),
                          lambda: (prepublish(), self._check_lock(lock_path, lock)))
            return receipt

    def remove(self, owner: Owner, *, expected_head: str | None, request_id: str, request_hash: str,
               plan: RemovalPlan, actor: dict, prepublish: Callable[[], None] = lambda: None) -> dict:
        """R22 墓碑提交：删除或恢复记录。锁、幂等、HEAD 比较、暂存回读与发布都与 commit 相同。

        删除把记录从 record_heads 移到 removed_records（原指针原样保存），恢复把原指针放回；
        不写任何记录文件，旧修订留在原来的提交里。"""
        safe_segment(request_id)
        lock_path = self.path(owner, "write.lock")
        with exclusive_lock(lock_path, f"memory-commit:{owner.owner_id}", clock=self.clock) as lock:
            snapshot = self.read_snapshot(owner)
            if prior := self.find_receipt(owner, snapshot, request_id, request_hash):
                return prior
            if expected_head != snapshot.commit_id:
                raise BurrowlError("VERSION_CONFLICT", "对象已更新，请基于当前版本重试",
                               {"expected_head": expected_head, "current_head": snapshot.head})
            removing, restoring = plan(snapshot)
            stones = tombstones(snapshot.manifest)
            unknown = [rid for rid in removing if rid not in snapshot.records] +                       [rid for rid in restoring if rid not in stones]
            if unknown or set(removing) & set(restoring):
                raise BurrowlError("INVALID_ARGUMENT", "要删除的记录必须是当前记录，要恢复的必须是已删除的记录",
                                   {"record_ids": unknown})
            changed = bool(removing or restoring)
            generation = snapshot.generation + (1 if changed else 0)
            commit_id = self.new_commit_id() if changed else snapshot.commit_id
            results = ([{"record_id": rid, "status": "removed"} for rid in removing]
                       + [{"record_id": rid, "status": "restored"} for rid in restoring])
            now = self.clock()
            # 删除或恢复不写记录文件，时间与执行者记在回执里（关键操作记录从这里读）
            receipt = {"request_id": request_id, "owner_id": owner.owner_id, "commit_id": commit_id,
                       "generation": generation, "save_status": "committed" if changed else "no_change",
                       "record_results": results, "at": now, "actor": deepcopy(actor)}
            prepublish()
            if not changed:
                self._write_no_change(owner, request_id, request_hash, receipt)
                return receipt
            removed = {rid: {"entry": deepcopy(snapshot.manifest["record_heads"][rid]), "removed_at": now,
                             "removed_in": commit_id, "reason": note["reason"], "actor": deepcopy(note["actor"])}
                       for rid, note in removing.items()}
            self._publish(owner, snapshot, [], receipt, request_hash, [],
                          lambda: (prepublish(), self._check_lock(lock_path, lock)),
                          removed=removed, restored=list(restoring))
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
                 checks: list, prepublish: Callable[[], None], *, removed: dict[str, dict] | None = None,
                 restored: list[str] | None = None) -> None:
        commit_id, generation = receipt["commit_id"], receipt["generation"]
        safe_segment(commit_id)
        staging = self.path(owner, f"staging/{receipt['request_id']}-{new_id('S')[2:].replace('-', '')}")
        staging.mkdir(parents=True)
        (staging / "records").mkdir()
        heads = deepcopy((snapshot.manifest or {}).get("record_heads", {}))
        pointers = deepcopy((snapshot.manifest or {}).get("pointers", dict.fromkeys(POINTER_KINDS)))
        stones = deepcopy(tombstones(snapshot.manifest))
        for rid, stone in (removed or {}).items():  # R22：删除 = 指针移入墓碑；恢复 = 原指针放回
            heads.pop(rid)
            stones[rid] = stone
        for rid in restored or []:
            heads[rid] = stones.pop(rid)["entry"]
        for record in changed:
            rid = safe_segment(record["record_id"])
            if rid in stones:  # 已删除的记录只能先恢复，不能被普通提交改写（R22 D-05）
                raise BurrowlError("INVALID_ARGUMENT", "该记录已删除，先用 records_restore 恢复再修改", {"record_id": rid})
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
        if stones or removed or restored:  # 只在用到时写入，旧数据目录的清单格式不变
            manifest |= {"removed_records": stones, "removed_ids": sorted(removed or {}),
                         "restored_ids": sorted(restored or [])}
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

