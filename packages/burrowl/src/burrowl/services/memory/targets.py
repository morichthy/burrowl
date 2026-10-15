"""工作台显示一条记录时，把它引用的身份解析成可读名称与跳转位置（R21 D-11）。只读，不改变任何状态。

记录 → 所在对象、标题、handle；结论 → 所在记录；原件 → 登记名称，以及本对象中登记它的 L0 记录；
Run / 对象 → 自身。读不到（受限、已删除、未登记）的不列出，前端退回显示原 ID。
"""
import re
from typing import Any

from ...core.errors import BurrowlError
from .claims import ClaimGraph, iter_refs
from .context import Storage
from .reader import Reader


def ref_targets(storage: Storage, owner_id: str, record: dict, reader: Reader | None = None) -> dict[str, dict[str, Any]]:
    """reader：同一次操作内连续解析多条记录时共用，避免每条记录重新读取对象状态。"""
    refs = [*iter_refs({"sources": record["sources"], "payload": record["payload"]}),
            *(link["ref"] for link in record.get("links", []) if link.get("ref"))]
    reader = reader or Reader(storage)
    own = reader.state(owner_id).records
    out: dict[str, dict[str, Any]] = {}
    graph: ClaimGraph | None = None
    for ref in refs:
        target = ref.get("target_id")
        if not target or target in out:
            continue
        kind = ref.get("target_kind")
        try:
            if kind == "record":
                owner = reader.locate(target)
                found = reader.state(owner).records[target]
                if found["sensitivity"] == "restricted":
                    continue
                out[target] = {"kind": "record", "owner_id": owner, "record_id": target, "title": found["title"],
                               "handle": found.get("handle"), "level": found.get("level")}
            elif kind == "claim":
                graph = graph or reader.claims()
                claim, holder = graph.claims.get(target, (None, None))
                if holder is not None and holder["sensitivity"] != "restricted":
                    out[target] = {"kind": "claim", "owner_id": holder["owner_id"], "record_id": holder["record_id"],
                                   "title": claim["statement"]}
            elif kind == "file":
                entry = next((e for e in storage.sources.entries() if e.get("source_id") == target), None)
                if entry is None:
                    continue
                l0 = next((r for r in own.values() if r["kind"] == "source"
                           and r["payload"]["source_ref"]["target_id"] == target), None)
                out[target] = {"kind": "file", "title": entry.get("title") or target,
                               **({"owner_id": owner_id, "record_id": l0["record_id"]} if l0 else {})}
            elif kind == "owner":
                out[target] = {"kind": "owner", "owner_id": target, "title": target}
        except BurrowlError:
            continue
    return out


def link_table(record: dict, names: dict[str, dict[str, Any]], text: str | None = None) -> list[dict[str, Any]]:
    """R24 D-08：给 AI 读的引用对照：正文链接 → 目标标题、ID、固定修订与块或图。
    names 是 ref_targets 的结果；text 给出时只列这段文字里出现的链接。读不到名称的退回 ID。"""
    out = []
    for link in record.get("links", []):
        ref = link.get("ref")
        if not ref or (text is not None and not re.search(r"\[\[\s*" + re.escape(link["text"]) + r"\s*(\||\]\])", text)):
            continue
        title = (names.get(ref["target_id"]) or {}).get("title") or ref["target_id"]
        locator = str(ref.get("locator") or "")
        if locator.startswith(("block:", "figure:")):
            title += " › " + locator.split(":", 1)[1]
        out.append({"link": f"[[{link['text']}]]", "title": title, "target_id": ref["target_id"],
                    "revision": ref.get("revision"),
                    "kind": "concept" if ref.get("relation") == "mentions" else ref["target_kind"]})
    return out
