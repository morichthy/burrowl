"""记录策略：工作区 < 对象类型 < 对象 < 本次请求，逐字段返回最终值及来源。

策略只是保存建议（保留哪些层、是否自动整理），不授予任何权限，也不是"必须补齐五层"。
"""
from copy import deepcopy
from typing import Any

from ...core.errors import BurrowlError
from ...core.jsonschema import validate
from ...core.schema import defs

WORKSPACE_DEFAULTS = {"mode": "basic", "retain": ["L0", "L1", "L2", "L3", "L4"], "granularity": "normal",
                      "auto_summary": False, "discovery": "owner_only", "auto_deepen": False, "checkpoint": True}
_RESEARCH = {"mode": "explore", "granularity": "normal", "retain": ["L0", "L1", "L2", "L3", "L4"],
             "auto_summary": False}
TYPE_DEFAULTS = {"research": _RESEARCH, "project": _RESEARCH, "run": {"mode": "basic"}}
# R19 D-01：知识类记录默认全库可见（用途就是被复用）；目标、检查点、路线、问题等工作状态与其余记录沿用工作区默认
# owner_only——它们由判定和续接按位置读取，进入召回会把工作状态混进知识检索。
KNOWLEDGE_KINDS = frozenset({"detail", "narrative", "experience", "overview", "association", "wiki_page"})


def _flatten(value: dict) -> dict:
    """接受已保存的 policy payload（mode+overrides）或直接字段映射。"""
    all_defs = defs()
    schema = {"type": "object", "required": [], "additionalProperties": False,
              "properties": {**all_defs["PolicyOverrides"]["properties"], **all_defs["PolicyPayload"]["properties"]}}
    if errors := validate(value, schema, all_defs):
        raise BurrowlError("INVALID_SCHEMA", "策略不符合记忆契约", errors=errors)
    out = {k: deepcopy(v) for k, v in value.items() if k not in {"overrides", "missing_refs"}}
    out.update(deepcopy(value.get("overrides", {})))
    return out


def discovery_for(kind: str | None, resolved: dict) -> str:
    """新记录在草案未写 discovery 时的取值（R19 D-01）：对象策略或请求显式设置 > 记录类型默认 > 工作区默认。"""
    if resolved["sources"].get("discovery") in {"owner", "request"}:
        return resolved["values"]["discovery"]
    return "workspace_summary" if kind in KNOWLEDGE_KINDS else resolved["values"]["discovery"]


def resolve(owner_type: str, owner_policy: dict | None = None, request: dict | None = None) -> dict[str, Any]:
    values: dict[str, Any] = {}
    sources: dict[str, str] = {}
    layers = [("workspace", WORKSPACE_DEFAULTS), ("type", TYPE_DEFAULTS.get(owner_type, {})),
              ("owner", owner_policy or {}), ("request", request or {})]
    for source, policy in layers:
        for key, value in _flatten(policy).items():
            values[key], sources[key] = value, source
    return {"values": values, "sources": sources}
