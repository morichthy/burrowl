"""受限 JSON Schema 校验器：只支持记录契约实际用到的关键字子集。

刻意不做通用实现——未知关键字直接报错，避免"看似通过实则未校验"。
"""
import math
from typing import Any

from .errors import schema_error
from .hashing import canonical_hash

SUPPORTED = {"type", "const", "enum", "required", "properties", "additionalProperties",
             "items", "minLength", "minItems", "oneOf", "$defs", "$ref"}
TYPES = {"object", "array", "string", "integer", "number", "boolean", "null"}


def pointer(path: str, key: object) -> str:
    return path + "/" + str(key).replace("~", "~0").replace("/", "~1")


def check_schema(schema: dict, defs: dict | None = None, _visiting: frozenset = frozenset()) -> None:
    """在注册扩展 schema 前校验其语法；不支持的写法抛 ValueError。"""
    defs = schema.get("$defs", {}) if defs is None else defs
    if not isinstance(schema, dict) or set(schema) - SUPPORTED:
        raise ValueError("Unsupported schema keywords or non-object schema")
    if "$ref" in schema:
        target = schema["$ref"]
        if not isinstance(target, str) or not target.startswith("#/$defs/") or target[8:] not in defs:
            raise ValueError("Only existing local $defs references are supported")
        if target in _visiting:
            raise ValueError("Recursive schema references are not supported")
        check_schema(defs[target[8:]], defs, _visiting | {target})
    if "required" in schema:
        required = schema["required"]
        if not isinstance(required, list) or len(set(required)) != len(required) \
                or any(not isinstance(n, str) for n in required):
            raise ValueError("required must be an array of unique strings")
    for key in ("minLength", "minItems"):
        if key in schema and (type(schema[key]) is not int or schema[key] < 0):
            raise ValueError(key + " must be a non-negative integer")
    if "enum" in schema:
        values = schema["enum"]
        if not isinstance(values, list) or not values or len({canonical_hash(v) for v in values}) != len(values):
            raise ValueError("enum must be a non-empty array of unique values")
    if "oneOf" in schema and (not isinstance(schema["oneOf"], list) or not schema["oneOf"]):
        raise ValueError("oneOf must be a non-empty schema array")
    types = schema.get("type", [])
    types = [types] if isinstance(types, str) else types
    if not isinstance(types, list) or ("type" in schema and not types) or set(types) - TYPES:
        raise ValueError("Unsupported schema type")
    for key in ("properties", "$defs"):
        for child in schema.get(key, {}).values():
            check_schema(child, defs, _visiting)
    for child in schema.get("oneOf", []):
        check_schema(child, defs, _visiting)
    for key in ("items", "additionalProperties"):
        if isinstance(schema.get(key), dict):
            check_schema(schema[key], defs, _visiting)


def _type_ok(value: Any, name: str) -> bool:
    match name:
        case "object":
            return isinstance(value, dict)
        case "array":
            return isinstance(value, list)
        case "string":
            return isinstance(value, str)
        case "integer":
            return type(value) is int
        case "number":
            return type(value) in (int, float) and math.isfinite(value)
        case "boolean":
            return type(value) is bool
        case "null":
            return value is None
    return False


def _same(a: Any, b: Any) -> bool:
    # Python 中 True == 1；JSON 的 enum/const 必须同时比较类型。
    return type(a) is type(b) and a == b


def _visit(value: Any, node: dict, defs: dict, path: str) -> list[dict]:
    errors: list[dict] = []
    if "$ref" in node:
        errors.extend(_visit(value, defs[node["$ref"][8:]], defs, path))
    if "oneOf" in node:
        branches = [_visit(value, branch, defs, path) for branch in node["oneOf"]]
        if sum(not e for e in branches) != 1:
            errors.append(schema_error(path, "Exactly one schema alternative must match"))
            if all(branches):
                errors.extend(min(branches, key=len))
    types = node.get("type", [])
    types = [types] if isinstance(types, str) else types
    if types and not any(_type_ok(value, t) for t in types):
        return [schema_error(path, "Unexpected value type")]
    if "const" in node and not _same(value, node["const"]):
        errors.append(schema_error(path, "Value does not match const"))
    if "enum" in node and not any(_same(value, e) for e in node["enum"]):
        errors.append(schema_error(path, "Value is outside enum"))
    if isinstance(value, str) and len(value) < node.get("minLength", 0):
        errors.append(schema_error(path, "String is too short"))
    if isinstance(value, list):
        if len(value) < node.get("minItems", 0):
            errors.append(schema_error(path, "Array is too short"))
        if "items" in node:
            for index, item in enumerate(value):
                errors.extend(_visit(item, node["items"], defs, pointer(path, index)))
    if isinstance(value, dict):
        for key in node.get("required", []):
            if key not in value:
                errors.append(schema_error(pointer(path, key), "Required field is missing"))
        properties = node.get("properties", {})
        extra = node.get("additionalProperties")
        for key, item in value.items():
            if key in properties:
                errors.extend(_visit(item, properties[key], defs, pointer(path, key)))
            elif isinstance(extra, dict):
                errors.extend(_visit(item, extra, defs, pointer(path, key)))
            elif extra is False:
                errors.append(schema_error(pointer(path, key), "Unknown field"))
    return errors


def validate(value: Any, schema: dict, defs: dict | None = None) -> list[dict]:
    """返回 JSON Pointer 定位的错误列表；空列表表示通过。schema 必须事先 check_schema。"""
    defs = schema.get("$defs", {}) if defs is None else defs
    try:
        canonical_hash(value)
    except (ValueError, TypeError, UnicodeError):
        return [schema_error("", "Value must be finite, Unicode JSON")]
    return _visit(value, schema, defs, "")
