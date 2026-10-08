"""记录契约：每类记录只有一个现行版本（由 tools/extract_schema.py 从 memory-v4 抽取）。"""
import json
from functools import cache
from pathlib import Path
from typing import Any

from ..jsonschema import check_schema

_PATH = Path(__file__).with_name("memory.schema.json")


@cache
def memory_schema() -> dict[str, Any]:
    schema = json.loads(_PATH.read_text(encoding="utf-8"))
    # 元数据键（$schema/$id/title/x-*）不参与校验；只检查实际约束部分。
    check_schema({"$ref": schema["$ref"], "$defs": schema["$defs"]})
    for definition in schema["$defs"].values():
        check_schema(definition, schema["$defs"])
    return schema


def defs() -> dict[str, Any]:
    return memory_schema()["$defs"]


def current_versions() -> dict[str, int]:
    return dict(memory_schema()["x-current-versions"])
