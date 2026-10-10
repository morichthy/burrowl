"""工作台笔记（R15）：用户交给 AI 的待办。AI 只能读和回写；写入记忆前必须由用户在工作台执行预览过的方案。

笔记不是研究记录：不进 memory/、检索、复核与知识中枢。文件 `.burrowl/workbench/notes.json`，随备份带走。
状态与转换见 docs/design/R15_WORKBENCH_NOTES.md §3。
"""
import uuid
from collections.abc import Callable
from copy import deepcopy
from typing import Any

from ..core.atomic import exclusive_lock, read_json, write_json_atomic
from ..core.clock import utc_now
from ..core.errors import BurrowlError
from .indexing import IndexService
from .memory import associations
from .memory.context import Storage
from .memory.service import MemoryService

NOTES_FILE = "workbench/notes.json"
NOTE_KINDS = {"cluster-name", "bridge-question", "relation-idea"}
OPEN_STATES = {"open", "queued", "proposed", "needs_user"}
CLOSED_STATES = {"handled", "done", "dismissed"}
# R15 §3：用户通过 note_resolve 能做的转换（起点 → 允许的终点）
USER_MOVES = {
    "open": {"queued", "done", "dismissed"},
    "queued": {"open", "done", "dismissed"},
    "needs_user": {"done", "dismissed"},
    "proposed": {"done", "dismissed"},
    "handled": {"open"},
    "done": {"open"},
    "dismissed": {"open"},
}
STEP_OPS = {"memory.commit", "relations.decide"}
MAX_STEPS = 10  # R15 D-05
EXCERPT = 200


class WorkbenchNotes:
    def __init__(self, storage: Storage, memory: MemoryService, index: IndexService | None = None):
        self.storage = storage
        self.memory = memory
        self.index = index
        self.path = storage.ws.state(NOTES_FILE)

    # ---------- 存储（D-11、D-12） ----------
    def _load(self) -> dict:
        if not self.path.exists():
            return {"next_seq": 1, "notes": []}
        data = read_json(self.path)
        if isinstance(data, list):  # 旧格式：笔记列表，编号按已有最大值续上
            seqs = [int(n["id"].split("-", 1)[1]) for n in data if str(n.get("id", "")).startswith("WN-")
                    and n["id"].split("-", 1)[1].isdigit()]
            data = {"next_seq": max(seqs, default=0) + 1, "notes": data}
        for note in data["notes"]:
            note.setdefault("thread", [])
        return data

    def _update(self, change: Callable[[dict], Any]) -> Any:
        """读改写在同一把锁内完成：AI 回写与用户点击可能同时发生。"""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with exclusive_lock(self.path.with_suffix(".lock"), "workbench-notes"):
            data = self._load()
            result = change(data)
            write_json_atomic(self.path, data, pretty=True)
            return deepcopy(result)

    @staticmethod
    def _find(data: dict, note_id: str) -> dict:
        for note in data["notes"]:
            if note["id"] == note_id:
                return note
        raise BurrowlError("NOT_FOUND", "笔记不存在", {"id": note_id})

    @staticmethod
    def _event(note: dict, by: str, kind: str, text: str = "", **extra: Any) -> None:
        note["thread"].append({"at": utc_now(), "by": by, "kind": kind, "text": text, **extra})
        note["updated_at"] = note["thread"][-1]["at"]

    # ---------- 读取 ----------
    def items(self, *, status: list[str] | None = None, owner_id: str | None = None) -> list[dict]:
        notes = self._load()["notes"]
        if status:
            notes = [n for n in notes if n["status"] in status]
        if owner_id:
            notes = self._touching(notes, owner_id)
        return notes

    def _touching(self, notes: list[dict], owner_id: str) -> list[dict]:
        """引用了该对象本身或其记录的笔记（R15 D-09）。"""
        owner = self.memory.owners().get(owner_id)
        record_ids = set(self.memory.snapshot(owner).records) if owner.persisted else set()
        return [n for n in notes if any(ref == owner_id or ref in record_ids for ref in n.get("refs", []))]

    # ---------- 用户操作（effect=user） ----------
    def add(self, kind: str, title: str, text: str, refs: list[str]) -> dict:
        if kind not in NOTE_KINDS or not title.strip():
            raise BurrowlError("INVALID_ARGUMENT", "笔记类型无效或缺标题", {"kinds": sorted(NOTE_KINDS)})

        def change(data: dict) -> dict:
            item = {"id": f"WN-{data['next_seq']}", "kind": kind, "title": title, "text": text, "refs": refs,
                    "status": "open", "created_at": utc_now(), "thread": []}
            item["updated_at"] = item["created_at"]
            data["next_seq"] += 1
            data["notes"].append(item)
            return item
        return self._update(change)

    def resolve(self, note_id: str, status: str) -> dict:
        def change(data: dict) -> dict:
            note = self._find(data, note_id)
            if status not in USER_MOVES.get(note["status"], set()):
                raise BurrowlError("INVALID_STATE", f"笔记当前为 {note['status']}，不能改为 {status}",
                                   {"allowed": sorted(USER_MOVES.get(note["status"], set()))})
            labels = {"queued": "交给 AI", "open": "收回或重新打开", "done": "完成（无需 AI）", "dismissed": "放弃"}
            note["status"] = status
            if status == "open":
                note.pop("proposal", None)
            self._event(note, "user", status, labels[status])
            return note
        return self._update(change)

    def answer(self, note_id: str, text: str) -> dict:
        if not text.strip():
            raise BurrowlError("INVALID_ARGUMENT", "回答不能为空")

        def change(data: dict) -> dict:
            note = self._require(data, note_id, "needs_user")
            note["status"] = "queued"
            self._event(note, "user", "answer", text)
            return note
        return self._update(change)

    def reject(self, note_id: str, proposal_id: str, comment: str) -> dict:
        def change(data: dict) -> dict:
            note = self._require(data, note_id, "proposed")
            self._check_proposal(note, proposal_id)
            note["status"] = "queued"
            note.pop("proposal")
            self._event(note, "user", "rejected", comment or "退回方案", proposal_id=proposal_id)
            return note
        return self._update(change)

    def clear(self) -> dict:
        """删除已处理的笔记（用户手动、二次确认后调用；R15 D-10）。编号不复用（D-11）。"""
        def change(data: dict) -> dict:
            removed = [n["id"] for n in data["notes"] if n["status"] in CLOSED_STATES]
            data["notes"] = [n for n in data["notes"] if n["status"] not in CLOSED_STATES]
            return {"removed": removed}
        return self._update(change)

    # ---------- AI 回写（R15 D-04、D-05、D-08） ----------
    def reply(self, note_id: str, outcome: str, message: str, steps: list[dict] | None, *, actor: dict) -> dict:
        if not message.strip():
            raise BurrowlError("INVALID_ARGUMENT", "回写需要一句给用户看的说明")
        proposal = None
        if outcome == "proposal":
            note = self._require(self._load(), note_id, "queued")  # 锁外预演前先确认状态，避免白做
            proposal = self._prepare(note, steps or [], message, actor)
        elif steps:
            raise BurrowlError("INVALID_ARGUMENT", "只有 outcome=proposal 时才能附写入步骤")

        def change(data: dict) -> dict:
            note = self._require(data, note_id, "queued")
            if outcome == "proposal":
                note["status"] = "proposed"
                note["proposal"] = proposal
                self._event(note, "ai", "proposal", message, proposal_id=proposal["id"])
            elif outcome == "question":
                note["status"] = "needs_user"
                self._event(note, "ai", "question", message)
            else:
                note["status"] = "handled"
                note["result"] = {"at": utc_now(), "declined": True, "message": message, "writes": []}
                self._event(note, "ai", "declined", message)
            return note
        return self._update(change)

    def _prepare(self, note: dict, steps: list[dict], message: str, actor: dict) -> dict:
        if not steps:
            raise BurrowlError("INVALID_ARGUMENT", "方案至少要有一步写入；不需要写入请用 outcome=declined")
        if len(steps) > MAX_STEPS:
            raise BurrowlError("INVALID_ARGUMENT", f"方案最多 {MAX_STEPS} 步")
        reason_tail = f"（依据工作台笔记 {note['id']} 起草，经用户在工作台批准后写入）"
        prepared, commit_owners = [], set()
        for number, raw in enumerate(steps, 1):
            step = deepcopy(raw)
            if step.get("op") not in STEP_OPS or not step.get("owner_id"):
                raise BurrowlError("INVALID_ARGUMENT", f"第 {number} 步：op 只能是 memory.commit 或 relations.decide，且要有 owner_id")
            if step["op"] == "memory.commit":
                if step["owner_id"] in commit_owners:
                    raise BurrowlError("INVALID_ARGUMENT", f"第 {number} 步：同一对象只能提交一次，把多条记录放进同一步的 operations")
                commit_owners.add(step["owner_id"])
                if not isinstance(step.get("operations"), list) or not step["operations"]:
                    raise BurrowlError("INVALID_ARGUMENT", f"第 {number} 步：memory.commit 需要 operations")
                for operation in step["operations"]:
                    draft = operation.get("draft") or {}
                    draft["record_reason"] = (draft.get("record_reason") or "") + reason_tail
                    if "change_reason" in draft:
                        draft["change_reason"] = (draft.get("change_reason") or "") + reason_tail
                step = {"op": "memory.commit", "owner_id": step["owner_id"], "operations": step["operations"],
                        "expected_head": step.get("expected_head")}
            else:
                if not isinstance(step.get("payload"), dict):
                    raise BurrowlError("INVALID_ARGUMENT", f"第 {number} 步：relations.decide 需要 payload")
                step = {"op": "relations.decide", "owner_id": step["owner_id"], "payload": step["payload"],
                        "reason": (step.get("reason") or message) + reason_tail}
            step["request_id"] = str(uuid.uuid4())
            prepared.append(step)
        # 提交排在关联之前：关联写入会推进对象 HEAD，让同对象提交的 expected_head 失效（D-05）
        prepared.sort(key=lambda s: s["op"] != "memory.commit")
        basis = {}
        for number, step in enumerate(prepared, 1):
            owner = self.memory.owners().get(step["owner_id"])
            head = self.memory.snapshot(owner).commit_id if owner.persisted else None
            if step["op"] == "memory.commit" and step["expected_head"] != head:
                raise BurrowlError("STALE_BASIS", f"第 {number} 步：expected_head 不是对象当前的 HEAD，请重新读取",
                                   {"owner_id": step["owner_id"], "head": head})
            basis.setdefault(step["owner_id"], head)
            try:
                self._run(step, actor, dry_run=True)
            except BurrowlError as exc:
                raise BurrowlError(exc.code, f"第 {number} 步预检未通过：{exc.message}", exc.details) from exc
        return {"id": f"P-{uuid.uuid4().hex[:8]}", "created_at": utc_now(), "actor": actor, "message": message,
                "steps": prepared, "basis_heads": basis, "preview": [self._preview(s) for s in prepared]}

    def _run(self, step: dict, actor: dict, *, dry_run: bool) -> dict:
        if step["op"] == "memory.commit":
            request = {"schema_version": 3, "request_id": step["request_id"], "actor": actor, "owner_id": step["owner_id"],
                       "expected_head": step["expected_head"], "operations": deepcopy(step["operations"]), "dry_run": dry_run}
            return self.memory.commit(request)
        return associations.decide(self.memory, step["owner_id"], deepcopy(step["payload"]), actor=actor,
                                   reason=step["reason"], request_id=step["request_id"], dry_run=dry_run)

    def _preview(self, step: dict) -> dict:
        """预览由服务端从将要执行的操作生成，不依赖 AI 的自述（D-08）。"""
        if step["op"] == "relations.decide":
            payload = step["payload"]
            ends = [payload.get("from", {}).get("target_id"), payload.get("to", {}).get("target_id")]
            titles = self._titles([e for e in ends if e])
            return {"action": "association", "owner_id": step["owner_id"],
                    "from": {"id": ends[0], "title": titles.get(ends[0])}, "to": {"id": ends[1], "title": titles.get(ends[1])},
                    "relation": payload.get("relation", "related_to"), "status": payload.get("status", "accepted"),
                    "explanation": payload.get("explanation", "")}
        items = []
        for operation in step["operations"]:
            draft = operation.get("draft") or {}
            payload = draft.get("payload") or {}
            items.append({"record_id": operation.get("record_id"), "expected_revision": operation.get("expected_revision"),
                          "kind": draft.get("kind"), "title": draft.get("title"),
                          "excerpt": (draft.get("body_markdown") or "")[:EXCERPT],
                          "claims": [c.get("statement", "") for c in payload.get("claims", [])]})
        return {"action": "records", "owner_id": step["owner_id"], "records": items}

    def _titles(self, ids: list[str]) -> dict[str, str]:
        if not self.index or not ids:
            return {}
        try:
            return {n["node_id"]: n["title"] for n in self.index.db.graph_nodes(ids)}
        except Exception:  # noqa: BLE001 - 标题只用于预览展示，索引不可用时显示 ID
            return {}

    # ---------- 执行（R15 D-06、D-07） ----------
    def apply(self, note_id: str, proposal_id: str) -> dict:
        note = self._require(self._load(), note_id, "proposed")
        proposal = self._check_proposal(note, proposal_id)
        changed = []
        for owner_id, expected in proposal["basis_heads"].items():
            owner = self.memory.owners().get(owner_id)
            head = self.memory.snapshot(owner).commit_id if owner.persisted else None
            if head != expected:
                changed.append(owner_id)
        if changed:
            text = f"方案生成后这些对象又有了新提交，没有写入：{'、'.join(changed)}。请 AI 重新出方案"
            return self._finish(note_id, proposal_id, "queued", "stale", text, writes=[], changed_owners=changed)
        writes = []
        for number, step in enumerate(proposal["steps"], 1):
            try:
                receipt = self._run(step, proposal["actor"], dry_run=False)
            except BurrowlError as exc:
                text = f"第 {number} 步失败：{exc.message}。前 {number - 1} 步已写入（不会回滚），请 AI 对剩余部分重新出方案"
                return self._finish(note_id, proposal_id, "queued", "failed", text, writes=writes, error=exc.code)
            writes.append(self._written(step, receipt))
        return self._finish(note_id, proposal_id, "handled", "applied", "已按方案写入", writes=writes)

    @staticmethod
    def _written(step: dict, receipt: dict) -> dict:
        ids = [r["record_id"] for r in receipt.get("record_results", []) if r.get("record_id")]
        return {"op": step["op"], "owner_id": step["owner_id"], "commit_id": receipt.get("commit_id"), "record_ids": ids}

    def _finish(self, note_id: str, proposal_id: str, status: str, kind: str, text: str, **extra: Any) -> dict:
        def change(data: dict) -> dict:
            note = self._require(data, note_id, "proposed")
            note["status"] = status
            if status == "handled":
                note["result"] = {"at": utc_now(), "proposal_id": proposal_id, "message": note["proposal"]["message"],
                                  "writes": extra["writes"]}
            else:
                note.pop("proposal", None)
            self._event(note, "user" if kind == "applied" else "system", kind, text, proposal_id=proposal_id, **extra)
            return note
        return self._update(change)

    # ---------- 校验 ----------
    def _require(self, data: dict, note_id: str, state: str) -> dict:
        note = self._find(data, note_id)
        if note["status"] != state:
            raise BurrowlError("INVALID_STATE", f"笔记当前为 {note['status']}，此操作要求 {state}", {"id": note_id})
        return note

    @staticmethod
    def _check_proposal(note: dict, proposal_id: str) -> dict:
        proposal = note.get("proposal")
        if not proposal or proposal["id"] != proposal_id:
            raise BurrowlError("STALE_BASIS", "方案已被替换或撤回，请刷新后再看", {"id": note["id"]})
        return proposal
