"""操作注册表：每个操作写成一个带类型注解的函数，HTTP/MCP/CLI 都从这里生成。

    @op("memory.inspect", effect="read")
    def inspect(ctx: Ctx, owner_id: Annotated[str, Field(description="对象 ID")]) -> dict: ...

参数模型由函数签名自动生成（pydantic）；结果必须可 JSON 序列化。
外部包可通过入口点 `burrowl.ops` 提供额外的注册模块（例如知识中枢连接器）。

参数只在 call() 这一处校验（R20 D-08）：MCP 适配层不预校验，HTTP/CLI 同样经过这里。
结构化参数的字段由 shapes.Shape 声明并按同一份 schema 校验；所有参数错误一次列全，中文、带完整路径（D-05）。
意外异常转为 INTERNAL_ERROR，堆栈只写日志（D-06）；被拒绝的调用记下错误码与路径用于统计（D-10）。
请求日志（reqlog，默认关闭）也在这里写：一次调用一行，记收到的参数、预处理后的参数与返回。
"""
import inspect
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from importlib.metadata import entry_points
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, ValidationError, create_model

from ...app import App
from ...core.errors import BurrowlError, headline
from ...core.jsonschema import validate
from ...storage.owners import OwnerRegistry
from . import errorlog, reqlog
from .shapes import Shape, shape_of

# user：只给人在工作台/CLI 使用（如批准全文补偿、改判），不暴露为 MCP 工具，agent 不能替用户批准。
Effect = Literal["read", "write", "admin", "user"]


@dataclass
class Ctx:
    """一次调用的上下文：服务、调用者身份与入口渠道。"""

    app: App
    actor: dict
    channel: Literal["http", "mcp", "cli"]
    elicit: Callable[..., Any] | None = None  # MCP 支持时：直接在对话里向用户提问


@dataclass
class OpSpec:
    name: str
    group: str
    effect: Effect
    summary: str
    fn: Callable[..., Any]
    model: type[BaseModel]
    shapes: dict[str, Shape]

    @property
    def tool_name(self) -> str:
        return self.name.replace(".", "_")

    def schema(self) -> dict:
        return self.model.model_json_schema()


REGISTRY: dict[str, OpSpec] = {}


def _model(name: str, fn: Callable) -> type[BaseModel]:
    fields: dict[str, Any] = {}
    for parameter in list(inspect.signature(fn).parameters.values())[1:]:
        annotation = parameter.annotation if parameter.annotation is not inspect.Parameter.empty else Any
        default = ... if parameter.default is inspect.Parameter.empty else parameter.default
        fields[parameter.name] = (annotation, default)
    model = create_model(name.replace(".", "_") + "_input", __config__=ConfigDict(extra="forbid"), **fields)
    model.__doc__ = (fn.__doc__ or "").strip().split("\n\n")[0]
    return model


def op(name: str, *, effect: Effect = "read") -> Callable[[Callable], Callable]:
    def register(fn: Callable) -> Callable:
        if name in REGISTRY:
            raise ValueError(f"duplicate op: {name}")
        summary = " ".join((fn.__doc__ or name).strip().split())
        shapes = {p.name: shape for p in list(inspect.signature(fn).parameters.values())[1:]
                  if (shape := shape_of(p.annotation)) is not None}
        REGISTRY[name] = OpSpec(name=name, group=name.split(".", 1)[0], effect=effect, summary=summary,
                                fn=fn, model=_model(name, fn), shapes=shapes)
        return fn
    return register


def jsonable(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, dict):
        return {k: jsonable(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [jsonable(v) for v in value]
    return value


MAX_WARNINGS = 10  # R20 D-06：写操作结果里的 warnings 最多列这么多条，其余只计数


def _cap_warnings(result: Any) -> Any:
    if isinstance(result, dict) and isinstance(result.get("warnings"), list) and len(result["warnings"]) > MAX_WARNINGS:
        result = {**result, "warnings": result["warnings"][:MAX_WARNINGS],
                  "warnings_truncated": len(result["warnings"]) - MAX_WARNINGS}
    return result


_PY_TYPES = {"string": "字符串", "int": "整数", "float": "数值", "bool": "布尔值", "dict": "对象", "list": "列表",
             "model": "对象"}


def _argument_error(error: dict, spec: OpSpec) -> dict:
    """pydantic 的参数错误译成中文；能给出允许值时放进 expected。"""
    path = "/" + "/".join(map(str, error["loc"]))
    kind, ctx_info, where = error["type"], error.get("ctx") or {}, ".".join(map(str, error["loc"]))
    extra: dict[str, Any] = {}
    if kind == "missing":
        message = f"缺少必填参数 {where}"
    elif kind == "extra_forbidden":
        message = f"未知参数 {where}（可能拼错）"
        extra["expected"] = sorted(spec.model.model_fields)
    elif kind in {"literal_error", "enum"}:
        message = f"取值 {error.get('input')!r} 不在允许范围：{ctx_info.get('expected', '')}"
    elif kind.endswith("_type") or kind.endswith("_parsing"):
        expected = _PY_TYPES.get(kind.split("_")[0], kind.split("_")[0])
        message = f"类型不对：应为{expected}，实际是 {type(error.get('input')).__name__}"
    elif kind == "string_pattern_mismatch":
        message = f"格式不对：应匹配 {ctx_info.get('pattern')}"
    else:
        message = f"取值不符合要求（{error['msg']}）"
    return {"code": "INVALID_ARGUMENT", "path": path, "message": message, **extra}


def check_arguments(spec: OpSpec, arguments: dict) -> BaseModel:
    """按参数模型与结构化参数的形状校验；所有错误收集后一次抛出。"""
    errors: list[dict] = []
    parsed = None
    try:
        parsed = spec.model.model_validate(arguments)
    except ValidationError as exc:
        errors = [_argument_error(e, spec) for e in exc.errors()]
    flagged = {e["path"].split("/")[1] for e in errors}
    for field, shape in spec.shapes.items():
        value = arguments.get(field)
        if value is None or field in flagged:
            continue
        errors += [{**e, "code": "INVALID_ARGUMENT", "path": f"/{field}{e['path']}"}
                   for e in validate(value, shape.schema, shape.defs)]
    if errors:
        raise BurrowlError("INVALID_ARGUMENT", headline("参数不符合要求", errors), {"op": spec.name}, errors)
    return parsed


def call(ctx: Ctx, name: str, arguments: dict | None, *, raw: dict | None = None) -> Any:
    """校验参数后执行。业务错误原样抛出（被拒绝的调用记入错误日志）；意外异常转为 INTERNAL_ERROR。

    raw：入口收到的原样参数（MCP 层把 JSON 字符串解析回列表或对象之前），只用于请求日志。"""
    mode = reqlog.level(ctx.app) if name not in reqlog.SKIP else "off"
    if mode == "off":
        return _call(ctx, name, arguments)
    request_id, started = f"REQ-{uuid.uuid4().hex[:12]}", time.perf_counter()
    common = {"request_id": request_id, "op": name, "channel": ctx.channel, "actor": ctx.actor,
              "raw": raw, "arguments": arguments}
    root = getattr(ctx.app, "root", None)
    try:
        result = _call(ctx, name, arguments)
    except BurrowlError as exc:
        exc.details = {**(exc.details or {}), "request_id": request_id}  # 凭它在 requests.jsonl 找到这次调用
        reqlog.write(root, reqlog.event(mode, ms=(time.perf_counter() - started) * 1000, problem=exc.problem(),
                                        **common))
        raise
    reqlog.write(root, reqlog.event(mode, ms=(time.perf_counter() - started) * 1000, result=result, **common))
    return result


def _call(ctx: Ctx, name: str, arguments: dict | None) -> Any:
    spec = load().get(name)
    if spec is None:
        raise BurrowlError("NOT_FOUND", "未知操作", {"op": name})
    root = getattr(ctx.app, "root", None)
    try:
        parsed = check_arguments(spec, arguments or {})
        values = {field: getattr(parsed, field) for field in spec.model.model_fields}
        with OwnerRegistry.request_scope():  # 一次操作只扫描一次对象目录
            result = jsonable(spec.fn(ctx, **values))
        return _cap_warnings(result) if spec.effect == "write" else result  # 诊断类读操作（validate）完整列出
    except BurrowlError as exc:
        errorlog.rejected(root, name, ctx.channel, exc)
        raise
    except Exception as exc:  # noqa: BLE001 - R20 D-06：意外异常不能带着堆栈或空消息到调用方
        raise errorlog.internal(root, name, ctx.channel, exc) from exc


_LOADED = False


def load() -> dict[str, OpSpec]:
    """导入内置操作模块与外部入口点提供的模块（导入即注册，只做一次）。"""
    global _LOADED
    if not _LOADED:
        from . import builtin  # noqa: F401, PLC0415 - 导入即注册

        for entry in entry_points(group="burrowl.ops"):
            entry.load()
        _LOADED = True
    return REGISTRY


def catalog(groups: list[str] | None = None) -> list[dict]:
    return [{"name": s.name, "group": s.group, "effect": s.effect, "summary": s.summary, "input": s.schema()}
            for s in load().values() if groups is None or s.group in groups]
