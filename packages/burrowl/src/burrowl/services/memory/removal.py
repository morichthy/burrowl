"""R22 删除记录与对象（回收站与墓碑提交）。

记录：墓碑提交（MemoryStore.remove），旧修订留在提交链里，固定引用照常可读，可恢复。
对象：整个对象目录（或被采用文档的记忆目录）移到 `.burrowl/trash/owners/<编号小写>/item/`，可恢复；
      清空回收站是用户专属的永久删除。
两者都有引用护栏：其他记录的当前修订还引用要删除的内容时拒绝，并列出引用者（D-03、D-07）。
"""
import json
import os
import shutil
import uuid
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING

from ...core.atomic import read_json, write_new
from ...core.clock import utc_now
from ...core.errors import BurrowlError
from ...core.hashing import canonical_hash
from ...storage.memory_store import POINTER_KINDS, tombstones
from ...storage.owners import Owner, OwnerIndex
from .claims import ClaimGraph, iter_refs

if TYPE_CHECKING:
    from .service import MemoryService

TRASH = "trash/owners"            # .burrowl/trash/owners/<编号小写>/{item/, discard.json}
TRASH_LOG = "trash/owners.log.jsonl"  # discard / restore / purge 事件，只追加（D-10）
NOTE = "discard.json"


def _record_refs(record: dict):
    """记录当前修订声明的全部引用：sources、payload、links（D-03）。"""
    yield from iter_refs({"sources": record["sources"], "payload": record["payload"]})
    for link in record.get("links") or []:
        if link.get("ref"):
            yield link["ref"]


def _brief(record: dict) -> dict:
    return {"record_id": record["record_id"], "owner_id": record["owner_id"], "kind": record["kind"],
            "level": record.get("level"), "title": record["title"]}


def references(graph: ClaimGraph, targets: set[str]) -> dict[str, list[dict]]:
    """全库当前记录对 targets（记录、结论、对象、Run、来源 ID）的引用：目标 → 引用者摘要。

    复核记录经 payload.target_claim_id 指向结论（不是引用结构），单独计入。"""
    found: dict[str, dict[str, dict]] = {}
    for record in graph.records.values():
        hits = {ref.get("target_id") for ref in _record_refs(record)} & targets
        if record["kind"] == "review" and record["payload"].get("target_claim_id") in targets:
            hits.add(record["payload"]["target_claim_id"])
        for target in hits:
            found.setdefault(target, {})[record["record_id"]] = _brief(record)
    return {target: list(citers.values()) for target, citers in sorted(found.items())}


def _claim_ids(records: list[dict]) -> set[str]:
    return {c["claim_id"] for r in records for c in (r.get("payload") or {}).get("claims", []) or []
            if isinstance(c, dict) and c.get("claim_id")}


def trash_log(storage) -> list[dict]:
    """对象回收站日志（D-10）：discard / restore / purge 事件，按写入顺序。"""
    path = storage.ws.state(TRASH_LOG)
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


class Removal:
    def __init__(self, memory: "MemoryService"):
        self.memory = memory
        self.storage = memory.storage

    # ---------- 记录（D-01–D-05）----------
    def _owner(self, index: OwnerIndex, owner_id: str) -> Owner:
        return self.memory._target(index, owner_id)

    @staticmethod
    def _discard_blockers(graph: ClaimGraph, records: list[dict], pointers: dict | None) -> list[dict]:
        ids = {r["record_id"] for r in records}
        blockers = [{"target_id": ref["record_id"], "reason": f"{kind} 指针记录不能删除；用新修订或替代表达变化"}
                    for kind, ref in (pointers or {}).items()
                    if kind in POINTER_KINDS and ref and ref["record_id"] in ids]
        for target, citers in references(graph, ids | _claim_ids(records)).items():
            outside = [c for c in citers if c["record_id"] not in ids]
            if outside:
                blockers.append({"target_id": target, "reason": "仍被其他记录的当前修订引用", "cited_by": outside})
        return blockers

    def discard_records(self, owner_id: str, record_ids: list[str], *, reason: str, actor: dict,
                        expected_head: str | None = None, request_id: str | None = None,
                        dry_run: bool = False) -> dict:
        ids = list(dict.fromkeys(record_ids))
        if not ids:
            raise BurrowlError("INVALID_ARGUMENT", "record_ids 不能为空")
        if not str(reason).strip():
            raise BurrowlError("INVALID_ARGUMENT", "删除记录要说明理由（例如：测试数据、重复记录、写错对象）")
        index = self.memory.owners()
        owner = self._owner(index, owner_id)
        snapshot = self.memory.snapshot(owner)
        stones = tombstones(snapshot.manifest)
        missing = [rid for rid in ids if rid not in snapshot.records]
        if missing:
            raise BurrowlError("NOT_FOUND", "对象中没有这些当前记录", {
                "record_ids": missing, "already_removed": [rid for rid in missing if rid in stones]})
        records = [snapshot.records[rid] for rid in ids]
        blockers = self._discard_blockers(ClaimGraph(self.storage, index), records,
                                          (snapshot.manifest or {}).get("pointers"))
        preview = {"owner_id": owner.owner_id, "head": snapshot.commit_id, "records": [_brief(r) for r in records],
                   "blockers": blockers, "allowed": not blockers}
        if dry_run:
            return preview
        if blockers:
            raise BurrowlError("INVALID_TRANSITION", "这些记录不能删除", {
                "blockers": blockers, "action": "先修订引用者去掉引用，或把引用者一起删除；结论错了用复核撤回"})

        def recheck() -> None:  # 发布前用新扫描再核一遍护栏：预检之后别处可能新写了引用
            fresh = self.storage.owners.scan(fresh=True)
            current = self.storage.store.read_snapshot(fresh.get(owner.owner_id))
            if self._discard_blockers(ClaimGraph(self.storage, fresh),
                                      [current.records[rid] for rid in ids if rid in current.records],
                                      (current.manifest or {}).get("pointers")):
                raise BurrowlError("STALE_BASIS", "提交期间出现了新的引用，请重新预检")

        request = {"op": "records_discard", "owner_id": owner.owner_id, "record_ids": ids, "reason": reason}
        receipt = self.storage.store.remove(
            owner, expected_head=expected_head if expected_head is not None else snapshot.commit_id,
            request_id=request_id or str(uuid.uuid4()), request_hash=canonical_hash(request),
            plan=lambda _s: ({rid: {"reason": reason, "actor": actor} for rid in ids}, []), actor=actor,
            prepublish=recheck)
        return self.memory._with_index(receipt, notify=receipt["save_status"] == "committed")

    def restore_records(self, owner_id: str, record_ids: list[str], *, actor: dict,
                        expected_head: str | None = None, request_id: str | None = None,
                        dry_run: bool = False) -> dict:
        ids = list(dict.fromkeys(record_ids))
        if not ids:
            raise BurrowlError("INVALID_ARGUMENT", "record_ids 不能为空")
        index = self.memory.owners()
        owner = self._owner(index, owner_id)
        snapshot = self.memory.snapshot(owner)
        stones = tombstones(snapshot.manifest)
        missing = [rid for rid in ids if rid not in stones]
        if missing:
            raise BurrowlError("NOT_FOUND", "这些记录不在已删除列表中", {"record_ids": missing})
        records = [self.storage.store.read_record(owner, rid, stones[rid]["entry"]["revision"]) for rid in ids]
        graph = ClaimGraph(self.storage, index)
        present = set(graph.records) | set(graph.claims) | set(ids) | _claim_ids(records)
        blockers = []
        for record in records:  # D-05：它引用的记录、结论、对象仍要存在（一起恢复的视为存在）
            lost = sorted({ref["target_id"] for ref in _record_refs(record)
                           if (ref["target_kind"] in ("record", "claim") and ref["target_id"] not in present)
                           or (ref["target_kind"] == "owner" and index.find(ref["target_id"]) is None)})
            if lost:
                blockers.append({"target_id": record["record_id"], "reason": "引用的内容已不存在", "missing": lost})
        preview = {"owner_id": owner.owner_id, "head": snapshot.commit_id, "records": [_brief(r) for r in records],
                   "blockers": blockers, "allowed": not blockers}
        if dry_run:
            return preview
        if blockers:
            raise BurrowlError("INVALID_TRANSITION", "这些记录不能恢复", {
                "blockers": blockers, "action": "先恢复它引用的记录或对象，或一起恢复"})
        request = {"op": "records_restore", "owner_id": owner.owner_id, "record_ids": ids}
        receipt = self.storage.store.remove(
            owner, expected_head=expected_head if expected_head is not None else snapshot.commit_id,
            request_id=request_id or str(uuid.uuid4()), request_hash=canonical_hash(request),
            plan=lambda _s: ({}, ids), actor=actor)
        return self.memory._with_index(receipt, notify=receipt["save_status"] == "committed")

    def discarded_records(self, owner_id: str) -> list[dict]:
        """对象内已删除的记录（删除前最后一个修订的标题、层级，以及理由、时间、执行者）。"""
        owner = self.memory.owners().get(owner_id).require_readable()
        if not owner.persisted:
            return []
        snapshot = self.storage.store.read_head(owner)
        out = []
        for rid, stone in sorted(tombstones(snapshot.manifest).items(), key=lambda kv: kv[1]["removed_at"], reverse=True):
            record = self.storage.store.read_record(owner, rid, stone["entry"]["revision"])
            if record["sensitivity"] == "restricted":
                continue
            out.append({**_brief(record), "revision": record["revision"], "reason": stone["reason"],
                        "removed_at": stone["removed_at"], "actor": stone["actor"]})
        return out

    # ---------- 对象回收站（D-06–D-09）----------
    def _trash(self) -> Path:
        return self.storage.ws.state(TRASH)

    def _log(self, event: dict) -> None:
        path = self.storage.ws.state(TRASH_LOG)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as out:  # 一行一个事件，只追加
            out.write(json.dumps({**event, "at": utc_now()}, ensure_ascii=False) + "\n")

    @staticmethod
    def _moved(owner: Owner) -> tuple[str, str]:
        """要移走的目录与方式：业务卡对象移整个对象目录；被采用文档只移记忆目录，原件留在原处。"""
        if owner.id_field:
            return PurePosixPath(owner.native_path).parent.as_posix(), "folder"
        return owner.memory_home, "memory_home"

    def _owner_preview(self, owner: Owner, index: OwnerIndex) -> dict:
        moved, mode = self._moved(owner)
        folder = PurePosixPath(moved)
        blockers: list[dict] = []
        if (self.memory.concept_owner() or "").casefold() == owner.owner_id.casefold():
            blockers.append({"target_id": owner.owner_id,
                             "reason": "这是配置的概念库（graph.concept_owner_id），全库的概念链接依赖它"})
        nested = [o.owner_id for o in index.owners if o is not owner and mode == "folder"
                  and folder in PurePosixPath(o.native_path).parents]
        if nested:
            blockers.append({"target_id": owner.owner_id, "reason": "对象目录里还有其他对象，会被一起移走",
                             "nested": nested})
        snapshot = self.storage.store.read_snapshot(owner) if owner.persisted else None
        records = list(snapshot.records.values()) if snapshot else []
        runs = [r.owner_id for r in index.runs_under(owner)] if mode == "folder" else []
        root = self.storage.ws.root
        sources = []
        if mode == "folder":
            base = (root / moved).resolve()
            for entry in self.storage.sources.describe():
                location = entry.get("location") or ""
                if not location.startswith("<") and base in Path(location).resolve().parents:
                    sources.append(entry["source_id"])
        targets = {owner.owner_id, *(r["record_id"] for r in records), *_claim_ids(records), *runs, *sources}
        for target, citers in references(ClaimGraph(self.storage, index), targets).items():
            outside = [c for c in citers if c["owner_id"] != owner.owner_id]
            if outside:
                blockers.append({"target_id": target, "reason": "仍被其他对象的当前记录引用", "cited_by": outside})
        return {"owner": owner.summary(), "mode": mode, "path": moved, "head": snapshot.commit_id if snapshot else None,
                "record_count": len(records), "runs": runs, "sources": sources,
                "kept_original": owner.native_path if mode == "memory_home" else None,
                "blockers": blockers, "allowed": not blockers}

    def discard_owner(self, owner_id: str, *, reason: str, actor: dict, expected_head: str | None = None,
                      dry_run: bool = False) -> dict:
        if not str(reason).strip():
            raise BurrowlError("INVALID_ARGUMENT", "删除对象要说明理由（例如：误建、测试对象、与另一对象重复）")
        index = self.memory.owners()
        owner = index.get(owner_id).require_readable()
        if owner.owner_type == "run":
            raise BurrowlError("INVALID_ARGUMENT", "Run 用 runs_discard 丢弃", {"run_id": owner.owner_id})
        preview = self._owner_preview(owner, index)
        if dry_run:
            return preview
        if preview["blockers"]:
            raise BurrowlError("INVALID_TRANSITION", "这个对象不能删除", {
                "blockers": preview["blockers"], "action": "先修订引用者去掉引用；嵌套对象先单独处理"})
        if expected_head is not None and expected_head != preview["head"]:
            raise BurrowlError("VERSION_CONFLICT", "对象已更新，请重新预检", {"expected_head": expected_head,
                                                                          "current_head": preview["head"]})
        if owner.persisted and self.storage.store.path(owner, "write.lock").exists():
            raise BurrowlError("LOCKED", "对象正在写入，稍后再试", {"owner_id": owner.owner_id})
        source = self.storage.ws.path(preview["path"])
        slot = self._trash() / owner.owner_id.casefold()
        if slot.exists():
            raise BurrowlError("VERSION_CONFLICT", "回收站中已有同编号的对象，请先恢复或由用户清空回收站",
                               {"owner_id": owner.owner_id})
        slot.mkdir(parents=True)
        note = {"owner_id": owner.owner_id, "owner_type": owner.owner_type, "title": owner.title,
                "original_path": preview["path"], "mode": preview["mode"], "native_path": owner.native_path,
                "record_count": preview["record_count"], "runs": preview["runs"], "reason": reason,
                "discarded_at": utc_now(), "actor": actor}
        if source.exists():  # 从未保存过记录的被采用文档没有记忆目录（它其实只有 owner.json 才算对象）
            os.replace(source, slot / "item")  # 同一卷内整体改名
        write_new(slot / NOTE, json.dumps(note, ensure_ascii=False, indent=2).encode("utf-8"))
        self._log({"event": "discard", **{k: note[k] for k in ("owner_id", "title", "original_path", "mode", "reason")},
                   "actor": actor})
        self.storage.owners.invalidate()
        try:  # D-08：派生投影立即删除；失败时 index.reconcile 会补删
            self.memory.index.on_owner_removed(owner.owner_id)
            index_status = "removed"
        except Exception as exc:  # noqa: BLE001 - 文件已移走，派生数据失败不能说成删除失败
            index_status = f"pending:{type(exc).__name__}"
        return {"owner_id": owner.owner_id, "status": "discarded", "trash_path": self.storage.ws.relative(slot),
                "files_deleted": 0, "index": index_status}

    def trash(self) -> list[dict]:
        root = self._trash()
        if not root.is_dir():
            return []
        return sorted((read_json(note) for child in root.iterdir()
                       if child.is_dir() and (note := child / NOTE).is_file()),
                      key=lambda n: n["discarded_at"], reverse=True)

    def restore_owner(self, owner_id: str, *, actor: dict) -> dict:
        slot = self._trash() / owner_id.casefold()
        if not (slot / NOTE).is_file():
            raise BurrowlError("NOT_FOUND", "回收站中没有这个对象", {"owner_id": owner_id})
        note = read_json(slot / NOTE)
        target = self.storage.ws.path(note["original_path"])
        if target.exists():
            raise BurrowlError("VERSION_CONFLICT", "原位置已被占用", {"path": note["original_path"]})
        if self.storage.owners.scan(fresh=True).find(note["owner_id"]) is not None:
            raise BurrowlError("VERSION_CONFLICT", "已有同编号的对象", {"owner_id": note["owner_id"]})
        if note["mode"] == "memory_home" and not self.storage.ws.path(note["native_path"]).is_file():
            raise BurrowlError("NOT_FOUND", "被采用的原件已不在原处，无法恢复", {"path": note["native_path"]})
        if (slot / "item").exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            os.replace(slot / "item", target)
        self._log({"event": "restore", "owner_id": note["owner_id"], "title": note["title"],
                   "original_path": note["original_path"], "discarded_at": note["discarded_at"], "actor": actor})
        shutil.rmtree(slot)  # 只剩 discard.json，内容已写进日志
        self.storage.owners.invalidate()
        owner = self.storage.owners.scan(fresh=True).get(note["owner_id"])
        try:
            head = self.storage.store.read_head(owner).head if owner.persisted else None
            self.memory.index.on_commit(owner.owner_id, head)
            index_status = "indexed"
        except Exception as exc:  # noqa: BLE001 - 文件已恢复；投影可用 index.reconcile 补齐
            index_status = f"pending:{type(exc).__name__}"
        return {"owner_id": note["owner_id"], "status": "restored", "path": note["original_path"],
                "index": index_status}

    def empty_trash(self, *, actor: dict) -> dict:
        """永久删除回收站中的全部对象。用户专属（effect=user），不暴露给 MCP。"""
        removed = []
        for note in self.trash():
            slot = self._trash() / note["owner_id"].casefold()
            if slot.is_dir() and not slot.is_symlink():
                shutil.rmtree(slot)
                removed.append(note["owner_id"])
                self._log({"event": "purge", "owner_id": note["owner_id"], "title": note["title"],
                           "original_path": note["original_path"], "actor": actor})
        return {"removed": removed}
