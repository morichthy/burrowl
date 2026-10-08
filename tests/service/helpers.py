"""服务层测试的共用构造：数据目录、记忆服务、草案。"""
import uuid
from pathlib import Path

from burrowl.core.hashing import sha256_file
from burrowl.services.memory.context import Storage
from burrowl.services.memory.service import MemoryService
from burrowl.services.review import ReviewService, claim_lookup_factory

AI = {"kind": "ai", "id": "test-assistant"}
HUMAN = {"kind": "human", "id": "reviewer"}


def services(root: Path, **store_options):
    storage = Storage.open(root, **store_options)
    memory = MemoryService(storage, claim_lookup_factory=claim_lookup_factory(storage))
    return storage, memory, ReviewService(storage, memory)


def head_of(memory: MemoryService, owner_id: str) -> str | None:
    owner = memory.owners().get(owner_id)
    return memory.snapshot(owner).commit_id


def request(owner_id: str, expected_head: str | None, *operations, dry_run: bool = False) -> dict:
    return {"schema_version": 3, "request_id": str(uuid.uuid4()), "actor": AI, "owner_id": owner_id,
            "expected_head": expected_head, "operations": list(operations), "dry_run": dry_run}


def goal_draft(owner_id: str, objective: str = "比较求和顺序误差") -> dict:
    return {"owner_id": owner_id, "kind": "goal", "title": "研究目标", "body_markdown": "",
            "record_reason": "建立目标", "sensitivity": "internal", "sources": [],
            "provenance_gap": "目标由用户口头给出",
            "payload": {"objective": objective, "constraints": [], "success_criteria": ["给出误差界"],
                        "previous_goal_ref": None, "change_impact": "首版目标"}}


def register_source(storage: Storage, source_id: str, relative: str, text: str) -> dict:
    path = storage.ws.root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    storage.sources.register(source_id, relative, title=path.name)
    return {"target_kind": "file", "target_id": source_id, "revision": None,
            "sha256": sha256_file(path), "locator": "", "relation": "input"}


def narrative_draft(owner_id: str, evidence: dict, claim_id: str = "CLM-ORDER-1",
                    statement: str = "排序后求和误差更小") -> dict:
    return {"owner_id": owner_id, "kind": "narrative", "title": "顺序实验经过", "body_markdown": "实验经过正文",
            "record_reason": "记录实验", "sensitivity": "internal", "sources": [evidence],
            "payload": {"question": "顺序是否影响误差", "limitations": ["仅整数输入"],
                        "stages": [{"situation": "大数加小数", "action": "比较两种顺序", "reason": "检验抵消",
                                    "outcome": "升序误差更小", "evidence_refs": [evidence]}],
                        "claims": [{"claim_id": claim_id, "statement": statement, "kind": "fact",
                                    "scope": "整数输入", "evidence_refs": [evidence]}],
                        "experience_refs": [], "process_refs": [], "technical_refs": []}}
