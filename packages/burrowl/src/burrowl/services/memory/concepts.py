"""概念库（R12 D-12、D-13、D-26、§4.6）：全库唯一的知识对象，存放 page_type 为 concept / entity 的 wiki_page。

- 唯一性：概念库中所有 active 页面的标题和别名（规范形）不能重复——"相关不等于相同"由 AI 判断，
  程序只保证同一个名字不会指向两个仍在使用的概念。
- 合并与拆分用记录修订表达：被并页 status=merged + merged_into，原页 status=split + split_into；从不删除。
- 查询扩展：active 概念的名称与别名作为术语条目并入查询计划（与 .burrowl/query-terms.json 并存，D-26）。
"""
from ...core import links as syntax
from ...core.errors import BurrowlError
from ...core.hashing import canonical_hash
from ...index.query_plan import contains

CONCEPT_PAGE_TYPES = {"concept", "entity"}


def is_concept(record: dict) -> bool:
    return record.get("kind") == "wiki_page" and (record.get("payload") or {}).get("page_type") in CONCEPT_PAGE_TYPES


def status(record: dict) -> str:
    return (record.get("payload") or {}).get("status") or "active"


def names(record: dict) -> list[str]:
    return [record["title"], *(record.get("payload") or {}).get("aliases", [])]


def check_library(records: dict[str, dict], changed: set[str]) -> None:
    """records：概念库对象提交后的全部记录（含本批）；只报告与本批改动有关的冲突。"""
    concepts = {rid: r for rid, r in records.items() if is_concept(r)}
    owners: dict[str, str] = {}
    conflicts = []
    for rid in sorted(concepts, key=lambda r: (r in changed, r)):  # 已有概念先占名，冲突报在新改动上
        record = concepts[rid]
        if status(record) != "active":
            continue
        for name in names(record):
            key = syntax.normalize(name)
            other = owners.setdefault(key, rid)
            if other != rid and (rid in changed or other in changed):
                conflicts.append({"name": name, "record_id": rid, "conflicts_with": other,
                                  "title": concepts[other]["title"]})
    if conflicts:
        raise BurrowlError("INVALID_SCHEMA", "概念库中已有同名（或同别名）的有效概念；相同就合并或加别名，"
                           "同名不同义请用限定名（如“收敛（数值）”）", {"conflicts": conflicts},
                           errors=[{"code": "INVALID_SCHEMA", "path": "/title", **c,
                                   "message": f"概念名“{c['name']}”已被有效概念 {c['conflicts_with']}（{c['title']}）使用"}
                                  for c in conflicts])
    for rid in changed & concepts.keys():
        payload = concepts[rid]["payload"]
        targets = [payload["merged_into"]] if "merged_into" in payload else list(payload.get("split_into", []))
        for ref in targets:
            target = records.get(ref["target_id"])
            if ref["target_id"] == rid or target is None or not is_concept(target) or status(target) != "active":
                raise BurrowlError("INVALID_SCHEMA", "合并或拆分必须指向概念库中另一条有效概念",
                                   {"record_id": rid, "target_id": ref["target_id"]})


MAX_CONCEPT_TERMS = 8  # 查询计划最多匹配 16 个术语；概念最多占一半，给用户词库留余量


def term_entries(records: list[dict], question: str) -> tuple[list[dict], str | None]:
    """把问题中出现了名称或别名的 active 概念转成查询术语条目（格式同 query-terms.json 的 entries）；
    返回 (条目, 指纹)。只收出现在问题里的概念，名称越长越优先（越具体）。"""
    matched = [r for r in records if is_concept(r) and status(r) == "active" and r.get("sensitivity") != "restricted"
               and any(contains(question, n) for n in names(r))]
    matched.sort(key=lambda r: (-max(len(n) for n in names(r)), r["record_id"]))
    entries = []
    for record in sorted(matched[:MAX_CONCEPT_TERMS], key=lambda r: r["record_id"]):
        entries.append({"id": "concept:" + record["record_id"], "domain": "general", "zh": [record["title"]],
                        "en": [], "aliases": list(record["payload"].get("aliases", [])), "related": [],
                        "sources": [record["record_id"]], "status": "active"})
    return entries, canonical_hash(entries) if entries else None
