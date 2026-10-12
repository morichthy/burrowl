"""请求日志：数据目录下 .burrowl/logs/requests.jsonl，每次工具调用一行（默认关闭）。

用来排查"调用方实际传来了什么、MCP 层预处理成了什么、返回了什么"——错误日志（errorlog）刻意不记参数内容，
看不到这些。三档：
    off      不写（默认）
    summary  工具名、渠道、请求编号、耗时、结果（ok 或错误码）；参数与返回只记结构（键名、类型、长度），不含内容
    full     另记参数原文（收到的原样与预处理后的）和返回内容；单个字符串超过 FIELD_CHARS 截断并注明原长度
开关：配置 diagnostics.request_log（工作台"设置"）；启动参数 --request-log 或环境变量 BURROWL_REQUEST_LOG 优先。

状态归属：派生的诊断数据，可随时删除，不进备份包；写失败不影响操作本身。超过 MAX_BYTES 轮转为 requests.1.jsonl（只留一份）。
记录开启时，报错的 details 带 request_id，凭它在这里找到这次调用。
"""
import json
import os
from collections import deque
from pathlib import Path
from typing import Any

from ...core.clock import utc_now

LEVELS = ("off", "summary", "full")
ENV = "BURROWL_REQUEST_LOG"
MAX_BYTES = 5_000_000
FIELD_CHARS = 4000
SKIP = {"system.request_log"}  # 读日志本身不记，否则每读一次日志就把读到的内容再写一遍


def path(root: Path) -> Path:
    return root / ".burrowl" / "logs" / "requests.jsonl"


def level(app: Any) -> str:
    """生效的级别：启动参数 / 环境变量优先，其次是配置；读配置失败按关闭处理（日志不能拖垮调用）。"""
    override = getattr(app, "request_log", None) or os.environ.get(ENV)
    if override in LEVELS:
        return override
    try:
        return app.config.current().diagnostics.request_log
    except Exception:  # noqa: BLE001 - 诊断开关读取失败时不记
        return "off"


def shape(value: Any) -> Any:
    """只保留结构：对象列出键与各值的结构，列表给长度与第一个元素的结构，字符串给长度。"""
    if isinstance(value, dict):
        return {k: shape(v) for k, v in value.items()}
    if isinstance(value, list):
        return {"list": len(value), **({"first": shape(value[0])} if value else {})}
    if isinstance(value, str):
        return f"str[{len(value)}]"
    return type(value).__name__ if value is not None else None


def clip(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: clip(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [clip(v) for v in value]
    if isinstance(value, str) and len(value) > FIELD_CHARS:
        return value[:FIELD_CHARS] + f"…（截断，原长 {len(value)} 字符）"
    if value is None or isinstance(value, bool | int | float | str):
        return value
    return repr(value)[:FIELD_CHARS]


def event(mode: str, *, request_id: str, op: str, channel: str, actor: dict | None, ms: float,
          raw: dict | None, arguments: dict | None, result: Any = None, problem: dict | None = None) -> dict:
    received = raw if raw is not None else arguments or {}
    # MCP 层把 JSON 字符串解析回列表或对象的参数（有的客户端这样传）；看它们最容易发现传递问题
    decoded = sorted(k for k, v in received.items()
                     if isinstance(v, str) and not isinstance((arguments or {}).get(k), str) and k in (arguments or {}))
    row = {"at": utc_now(), "request_id": request_id, "op": op, "channel": channel,
           "actor": (actor or {}).get("kind"), "ms": round(ms, 1),
           "status": problem["code"] if problem else "ok", "received": shape(received)}
    if decoded:
        row["json_decoded"] = decoded
    if mode == "full":
        row["received"] = clip(received)
        if decoded:
            row["after_decode"] = {k: clip((arguments or {})[k]) for k in decoded}
        row["response"] = clip(problem if problem else result)
    else:
        row["response"] = shape(problem if problem else result)
    return row


def write(root: Path | None, row: dict) -> None:
    if root is None:
        return
    try:
        target = path(root)
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.is_file() and target.stat().st_size > MAX_BYTES:
            target.replace(target.with_name("requests.1.jsonl"))
        with target.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(row, ensure_ascii=False, default=repr) + "\n")
    except OSError:
        pass  # 诊断日志写不进去不能让操作失败


def read(root: Path, *, limit: int = 50, op: str | None = None, errors_only: bool = False,
         request_id: str | None = None) -> list[dict]:
    """最近的请求（新的在前）。只扫描当前文件；轮转出去的旧文件不读。"""
    target = path(root)
    if not target.is_file():
        return []
    kept: deque[dict] = deque(maxlen=limit)
    with target.open(encoding="utf-8") as handle:
        for line in handle:
            try:
                row = json.loads(line)
            except ValueError:
                continue
            name = str(row.get("op", ""))
            if (op and op not in (name, name.replace(".", "_"))) or (errors_only and row.get("status") == "ok") \
                    or (request_id and row.get("request_id") != request_id):
                continue
            kept.append(row)
    return list(reversed(kept))
