"""操作注册表：每个操作写成一个带类型注解的函数，HTTP/MCP/CLI 都从这里生成。

    @op("memory.inspect", effect="read")
    def inspect(ctx: Ctx, owner_id: Annotated[str, Field(description="对象 ID")]) -> dict: ...

参数模型由函数签名自动生成（pydantic）；结果必须可 JSON 序列化。
外部包可通过入口点 `burrowl.ops` 提供额外的注册模块（例如知识中枢连接器）。
"""
import inspect
from collections.abc import Callable
from dataclasses import dataclass
from importlib.metadata import entry_points
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, ValidationError, create_model

from ...app import App
from ...core.errors import BurrowlError
from ...storage.owners import OwnerRegistry

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
        REGISTRY[name] = OpSpec(name=name, group=name.split(".", 1)[0], effect=effect, summary=summary,
                                fn=fn, model=_model(name, fn))
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


def call(ctx: Ctx, name: str, arguments: dict | None) -> Any:
    """校验参数后执行；参数错误统一转换为 INVALID_ARGUMENT。"""
    spec = load().get(name)
    if spec is None:
        raise BurrowlError("NOT_FOUND", "未知操作", {"op": name})
    try:
        parsed = spec.model.model_validate(arguments or {})
    except ValidationError as exc:
        raise BurrowlError("INVALID_ARGUMENT", "参数不符合要求", {"op": name},
                       [{"code": "INVALID_ARGUMENT", "path": "/" + "/".join(map(str, e["loc"])), "message": e["msg"]}
                        for e in exc.errors()]) from exc
    values = {field: getattr(parsed, field) for field in spec.model.model_fields}
    with OwnerRegistry.request_scope():  # 一次操作只扫描一次对象目录
        return jsonable(spec.fn(ctx, **values))


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
