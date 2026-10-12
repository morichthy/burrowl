"""工具错误日志（R20 D-06、D-10）：数据目录下 .burrowl/logs/errors.jsonl，一行一个事件。

两类事件：
- internal：意外异常。返回给调用方的只有类型、一句摘要和 incident_id；完整堆栈只写在这里，凭 incident_id 查。
- rejected：被拒绝的调用（业务错误码）。只记操作名、渠道、错误码和出错的字段路径，不记参数内容，
  用来统计哪些工具的参数最容易写错（参数错误多说明说明或示例要改）。

状态归属：派生的诊断数据，可随时删除；写失败不影响操作本身。文件超过 MAX_BYTES 时轮转为 errors.1.jsonl（只留一份）。
"""
import json
import traceback
import uuid
from collections import Counter
from pathlib import Path

from ...core.clock import utc_now
from ...core.errors import INPUT_CODES, BurrowlError

MAX_BYTES = 2_000_000
SUMMARY_CHARS = 300


def _path(root: Path) -> Path:
    return root / ".burrowl" / "logs" / "errors.jsonl"


def _append(root: Path | None, event: dict) -> None:
    if root is None:
        return
    try:
        path = _path(root)
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.is_file() and path.stat().st_size > MAX_BYTES:
            path.replace(path.with_name("errors.1.jsonl"))
        with path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(event, ensure_ascii=False) + "\n")
    except OSError:
        pass  # 诊断日志写不进去不能让操作失败


def rejected(root: Path | None, op: str, channel: str, error: BurrowlError) -> None:
    _append(root, {"at": utc_now(), "kind": "rejected", "op": op, "channel": channel, "code": error.code,
                   "paths": sorted({e.get("path", "") for e in error.errors})[:20]})


def internal(root: Path | None, op: str, channel: str, exc: BaseException) -> BurrowlError:
    """记下堆栈，返回给调用方的业务错误（不含堆栈和内部路径之外的细节）。"""
    incident = "INC-" + uuid.uuid4().hex[:12]
    summary = (str(exc).strip().splitlines() or [""])[0][:SUMMARY_CHARS]
    _append(root, {"at": utc_now(), "kind": "internal", "op": op, "channel": channel, "incident_id": incident,
                   "exception": type(exc).__name__, "summary": summary,
                   "traceback": "".join(traceback.format_exception(exc))[-20_000:]})
    return BurrowlError("INTERNAL_ERROR", "服务内部错误，不是参数问题：不要改参数重试，请把 incident_id 告诉用户",
                        {"op": op, "exception": type(exc).__name__, "summary": summary, "incident_id": incident,
                         "log": ".burrowl/logs/errors.jsonl"})


def summary(root: Path, *, limit: int = 2000) -> dict:
    """最近的错误按操作汇总：参数错误（可照着报错改正的）、其他拒绝、内部错误。"""
    events = []
    for path in (_path(root).with_name("errors.1.jsonl"), _path(root)):
        if path.is_file():
            for line in path.read_text(encoding="utf-8").splitlines():
                try:
                    events.append(json.loads(line))
                except ValueError:
                    continue
    events = events[-limit:]
    by_op: dict[str, Counter] = {}
    paths: dict[str, Counter] = {}
    for event in events:
        kind = event.get("kind")
        group = "internal" if kind == "internal" else "input" if event.get("code") in INPUT_CODES else "other"
        by_op.setdefault(event.get("op", "?"), Counter())[group] += 1
        for path in event.get("paths", []):
            paths.setdefault(event.get("op", "?"), Counter())[path] += 1
    ops = [{"op": op, **counts, "top_paths": [p for p, _ in paths.get(op, Counter()).most_common(5)]}
           for op, counts in sorted(by_op.items(), key=lambda x: -sum(x[1].values()))]
    incidents = [{k: e.get(k) for k in ("at", "op", "incident_id", "exception", "summary")}
                 for e in events if e.get("kind") == "internal"][-10:]
    return {"events": len(events), "since": events[0]["at"] if events else None, "by_op": ops,
            "recent_incidents": incidents, "log": ".burrowl/logs/errors.jsonl"}
