"""阅读会话的持久状态（`.burrowl/sessions/reading/RS-*/`，HEAD + 不可变修订 + CAS）。

会话是用户的工作数据，不是可随手清理的缓存；也不是知识——笔记要成为知识须另行写入记忆。
"""
import re
import uuid
from pathlib import Path
from typing import Any

from ...core.clock import utc_now
from ...core.errors import BurrowlError
from ...core.revdoc import RevisionedDoc
from ...storage.layout import Workspace

STRATEGIES = ("standard", "associative", "quick")
VERDICTS = ("relevant", "uncertain", "irrelevant")
STATUSES = ("active", "waiting_user", "finished", "aborted", "archived")
_ID = re.compile(r"RS-[0-9a-f-]{36}")
_CJK = re.compile(r"[㐀-鿿豈-﫿]")


def estimate_tokens(text: str) -> int:
    """保守估算：每个汉字约 1 token，其他字符约 4 个 1 token（旧系统按 UTF-8 字节估，中文被高估 3 倍）。"""
    cjk = len(_CJK.findall(text))
    return cjk + (len(text) - cjk + 3) // 4


def new_session(*, question: str, conditions: list[str], strategy: str, review_filter: str, owner_id: str | None,
                association: dict, context: dict, screening: dict, plan: dict, actor: dict) -> dict:
    now = utc_now()
    return {"schema_version": 1, "session_id": f"RS-{uuid.uuid4()}", "question": question, "conditions": conditions,
            "strategy": strategy, "review_filter": review_filter, "owner_id": owner_id,
            "association": {"text": association.get("text", ""), "enabled": association["enabled"],
                            "max_rounds": association["max_rounds"], "rounds_used": 0},
            "context": context, "screening": screening, "plan": plan, "status": "active", "phase": "screening",
            "rounds": [], "queue": [], "owners": {}, "synthesis": None, "requests": [], "inbox": [], "timeline": [],
            "created_by": actor, "created_at": now, "updated_at": now}


class SessionStore:
    def __init__(self, workspace: Workspace):
        self.ws = workspace

    def _doc(self, session_id: str) -> RevisionedDoc:
        if not isinstance(session_id, str) or not _ID.fullmatch(session_id):
            raise BurrowlError("INVALID_ARGUMENT", "会话 ID 格式无效", {"session_id": session_id})
        return RevisionedDoc(self.ws.state(f"sessions/reading/{session_id}"))

    @property
    def root(self) -> Path:
        return self.ws.state("sessions/reading")

    def create(self, session: dict) -> int:
        return self._doc(session["session_id"]).create(session)

    def read(self, session_id: str) -> tuple[dict, int]:
        doc = self._doc(session_id)
        if not doc.exists:
            raise BurrowlError("NOT_FOUND", "阅读会话不存在", {"session_id": session_id})
        return doc.read()

    def update(self, session_id: str, change, *, expected_revision: int | None = None) -> tuple[dict, int]:
        def apply(current: dict) -> dict:
            updated = change(current)
            if updated is not None:
                updated["updated_at"] = utc_now()
            return updated
        return self._doc(session_id).update(expected_revision, apply)

    def list(self) -> list[dict[str, Any]]:
        if not self.root.is_dir():
            return []
        out = []
        for folder in self.root.iterdir():
            if _ID.fullmatch(folder.name) and (folder / "HEAD.json").is_file():
                try:
                    session, revision = self.read(folder.name)
                except BurrowlError:
                    continue
                out.append({"session_id": session["session_id"], "question": session["question"],
                            "status": session["status"], "phase": session["phase"], "strategy": session["strategy"],
                            "owner_id": session["owner_id"], "updated_at": session["updated_at"], "revision": revision,
                            "pending_requests": sum(r["status"] == "pending" for r in session["requests"]),
                            "notes": sum(1 for o in session["owners"].values() if o.get("note")),
                            "relevant": sum(1 for o in session["owners"].values() if o.get("verdict") == "relevant")})
        return sorted(out, key=lambda s: s["updated_at"], reverse=True)
