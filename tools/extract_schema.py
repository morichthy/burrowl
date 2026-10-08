"""从旧仓库 memory-v4.schema.json 抽取"每类记录的现行版本"，生成新项目的唯一记录契约。

只在开发时运行一次；结果提交到 burrowl/core/schema/memory.schema.json。
旧类型（event/map）和 v1/v2 定义不进入新契约。

用法：python tools/extract_schema.py <旧 memory-v4.schema.json> <输出路径>
"""
import copy
import json
import sys
from pathlib import Path

# 每类记录的现行版本（与旧 memory-v4 契约一致）。
CURRENT = {
    "source": 3, "detail": 3, "narrative": 4, "experience": 4, "overview": 4,
    "document": 3, "document_section": 3,
    "question": 3, "goal": 3, "route": 3, "checkpoint": 3, "association": 3,
    "representation": 3, "policy": 3, "consolidation": 3, "feedback": 3, "review": 3,
}
EXTRA_ROOTS = ["CommitRequest", "CommitReceipt", "OwnerDescriptor", "Actor", "Ref", "RecordResult"]
# 新系统新增的记录类型（不在旧契约中）：由宿主 AI 撰写、发布到知识中枢的 Wiki 页面。
NEW_KINDS = {"wiki_page": 1}
WIKI_PAGE_PAYLOAD = {
    "type": "object",
    "required": ["slug", "page_type", "aliases", "source_refs"],
    "additionalProperties": False,
    "properties": {
        "slug": {"type": "string", "minLength": 1},
        "page_type": {"enum": ["summary", "entity", "concept", "synthesis", "comparison", "index"]},
        "aliases": {"type": "array", "items": {"type": "string", "minLength": 1}},
        "source_refs": {"type": "array", "minItems": 1, "items": {"$ref": "#/$defs/Ref"}},
        "missing_refs": {"type": "array", "items": {"$ref": "#/$defs/UnresolvedRef"}},
    },
}


def camel(kind):
    return "".join(part.title() for part in kind.split("_"))


def refs(node):
    if isinstance(node, dict):
        if isinstance(node.get("$ref"), str):
            yield node["$ref"][len("#/$defs/"):]
        for child in node.values():
            yield from refs(child)
    elif isinstance(node, list):
        for child in node:
            yield from refs(child)


def main(source, target):
    old = json.loads(Path(source).read_text(encoding="utf-8"))
    defs = old["$defs"]
    roots = [f"{camel(k)}{part}V{v}" for k, v in CURRENT.items() for part in ("Draft", "Record")] + EXTRA_ROOTS
    keep, queue = set(), list(roots)
    while queue:
        name = queue.pop()
        if name in keep:
            continue
        if name not in defs:
            raise SystemExit(f"missing definition: {name}")
        keep.add(name)
        queue.extend(refs(defs[name]))
    out = {name: defs[name] for name in sorted(keep)}
    for kind, version in NEW_KINDS.items():
        name = camel(kind)
        out[f"{name}Payload"] = WIKI_PAGE_PAYLOAD
        for part in ("Draft", "Record"):
            definition = copy.deepcopy(out[f"Document{part}V3"])
            definition["properties"].update(schema_version={"const": version}, kind={"const": kind},
                                            level={"const": None}, payload={"$ref": f"#/$defs/{name}Payload"},
                                            body_markdown={"type": "string", "minLength": 1})
            out[f"{name}{part}V{version}"] = definition
    versions = {**CURRENT, **NEW_KINDS}
    out["MemoryDraft"] = {"oneOf": [{"$ref": f"#/$defs/{camel(k)}DraftV{v}"} for k, v in versions.items()]}
    out["MemoryRecord"] = {"oneOf": [{"$ref": f"#/$defs/{camel(k)}RecordV{v}"} for k, v in versions.items()]}
    legacy = sorted(n for n in keep if n.startswith(("Event", "Map")))
    if legacy:
        raise SystemExit(f"current definitions still reference legacy kinds: {legacy}")
    schema = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "urn:burrowl:memory:current",
        "title": "burrowl memory records (current version per kind)",
        "x-current-versions": {**CURRENT, **NEW_KINDS},
        "$ref": "#/$defs/MemoryRecord",
        "$defs": out,
    }
    Path(target).write_text(json.dumps(schema, ensure_ascii=False, indent=1) + "\n", encoding="utf-8", newline="\n")
    print(f"kept {len(out)} of {len(defs)} definitions -> {target}")


if __name__ == "__main__":
    main(*sys.argv[1:3])
