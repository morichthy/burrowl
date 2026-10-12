"""R13 关键操作记录：从权威数据实时汇总的派生视图（D-08），不新增持久状态。

只列会改变资产的操作，每条一行（D-09）：
    owner     建立 / 采用对象           ← 业务卡 created_at；被采用文档的 owner.json
    attribution 归属确认（含放弃的对象） ← 提交回执的 attribution（D-06）
    save      保存（每次提交一行）       ← 提交清单 changed_ids + 对应修订的记录
    review    复核状态变化               ← kind=review 的记录
    run       Run 完成 / 失败 / 放弃     ← Run 业务卡 status 与 ended_at
    publish   发布到知识中枢             ← 中枢执行记录（hub_state.runs）
    remove    删除 / 恢复记录与对象（R22）← 墓碑提交的 removed_ids / restored_ids 与回执；对象回收站日志

检索、阅读、归属判定调用、来源登记、索引补齐都不记。
各来源按时间倒序惰性产出，再归并；读取量随返回条数增长，而不是随全部历史增长
（每个对象至少读一份提交清单和一条记录以取得最新时间）。
"""
import heapq
from collections import Counter
from collections.abc import Iterator
from datetime import datetime
from itertools import islice

from ..core.errors import BurrowlError
from ..storage.hub_state import HubState
from ..storage.owners import Owner, OwnerIndex
from .memory.context import Storage
from .memory.removal import trash_log

KINDS = ("owner", "attribution", "save", "review", "run", "publish", "remove")
RUN_DONE = {"succeeded": "完成", "failed": "失败", "abandoned": "放弃"}
REVIEW_STATE = {"accepted": "确认", "disputed": "标为有争议", "retracted": "撤回", "superseded": "标为被替代",
                "not-reviewed": "恢复为未复核"}
PUBLISH_RESULT = {"created": "新增", "updated": "更新", "deleted": "删除", "marked": "标记", "failed": "失败",
                  "skipped": "跳过", "unchanged": "未变"}


def actor_label(actor: dict | None) -> str:
    kind = (actor or {}).get("kind")
    return {"ai": "AI", "human": "用户"}.get(kind, kind or "未知")


def save_summary(owner_id: str, records: list[dict]) -> str:
    """一次提交的一行摘要：按层级（无层级时按类型）计数，不列标题。"""
    counts = Counter(r["level"] or r["kind"] for r in records)
    # 层级 L4→L0 在前，其余类型按名称
    order = sorted(counts, key=lambda k: (0, -int(k[1:]), k) if k[:1] == "L" and k[1:].isdigit() else (1, 0, k))
    return f"{owner_id} 保存 {len(records)} 条（" + "、".join(f"{k}×{counts[k]}" for k in order) + "）"


def review_summary(record: dict) -> str:
    payload = record["payload"]
    state = REVIEW_STATE.get(payload.get("state"), payload.get("state"))
    extra = f"，由 {payload['replacement_claim_id']} 替代" if payload.get("replacement_claim_id") else ""
    return f"结论 {payload.get('target_claim_id')} {state}{extra}"


def attribution_summary(owner_id: str, attribution: dict) -> str:
    verb = "沿用" if attribution["action"] == "continue" else "新建"
    rejected = attribution.get("rejected") or []
    tail = f"（放弃 {'、'.join(r['owner_id'] for r in rejected)}）" if rejected else ""
    return f"归属：{verb} {owner_id}{tail}：{attribution['reason']}"


def commit_events(owner_id: str, at: str | None, actor: dict | None, records: list[dict], commit_id: str,
                  attribution: dict | None) -> list[dict]:
    """一次提交产生的条目：归属确认（若有）、复核（每条复核一行）、保存（其余记录合为一行）。"""
    ref = {"kind": "commit", "owner_id": owner_id, "commit_id": commit_id}
    base = {"at": at, "actor": actor_label(actor), "owner_id": owner_id, "ref": ref}
    events = []
    if attribution:
        events.append(base | {"kind": "attribution", "summary": attribution_summary(owner_id, attribution)})
    reviews = [r for r in records if r["kind"] == "review"]
    events += [base | {"kind": "review", "summary": review_summary(r)} for r in reviews]
    saved = [r for r in records if r["kind"] != "review"]
    if saved:
        events.append(base | {"kind": "save", "summary": save_summary(owner_id, saved)})
    return sorted(events, key=_sort_key, reverse=True)  # 归并要求每个来源都按同一键倒序


def receipt_lines(storage: Storage, receipt: dict) -> list[str]:
    """写操作返回值中的 activity 行（D-10）：与 activity.list 的 summary 同一套生成函数。"""
    if receipt.get("save_status") != "committed":
        return []
    owner = storage.owners.scan().get(receipt["owner_id"])
    records = [storage.store.read_record(owner, r["record_id"], r["revision"]) for r in receipt["record_results"]]
    visible = [r for r in records if r["sensitivity"] != "restricted"]
    return [e["summary"] for e in commit_events(owner.owner_id, None, None, visible, receipt["commit_id"],
                                                receipt.get("attribution"))]


def result_lines(storage: Storage, result: object) -> list[str]:
    """从写操作的返回值里找出提交回执（含批量结果、维护计划的逐对象提交），生成 activity 行。"""
    if not isinstance(result, dict):
        return []
    if "save_status" in result and "record_results" in result:
        return receipt_lines(storage, result)
    lines: list[str] = []
    for item in result.get("results") or []:
        lines += result_lines(storage, item.get("result", item) if isinstance(item, dict) else item)
    applied = result.get("applied")
    if isinstance(applied, dict):
        registry = storage.owners.scan()
        for owner_id, outcome in applied.items():
            if isinstance(outcome, dict) and outcome.get("ok") and outcome.get("commit_id"):
                owner = registry.get(owner_id)
                lines += receipt_lines(storage, storage.store.read_receipt(owner, outcome["commit_id"]))
    return lines


def annotate(storage: Storage, result: dict) -> dict:
    return with_activity(result, result_lines(storage, result))


def run_lines(storage: Storage, run_id: str) -> list[str]:
    """Run 状态变为完成、失败或放弃时的一行；其他状态不算关键操作。"""
    line = run_line(storage.owners.scan().get(run_id).native)
    return [line] if line else []


def with_activity(result: dict, lines: list[str | None]) -> dict:
    """把 activity 行附到写操作的返回值上（没有关键操作时不加字段）。"""
    lines = [line for line in lines if line]
    return result | {"activity": lines} if lines and isinstance(result, dict) else result


def removal_summary(owner_id: str, removed: list, restored: list) -> str:
    """一次墓碑提交的一行摘要（R22 D-10）。"""
    parts = ([f"删除 {len(removed)} 条记录"] if removed else []) + ([f"恢复 {len(restored)} 条记录"] if restored else [])
    return f"{owner_id} " + "，".join(parts)


def removal_line(result: dict) -> str | None:
    if result.get("save_status") != "committed":
        return None
    statuses = [r["status"] for r in result["record_results"]]
    return removal_summary(result["owner_id"], [s for s in statuses if s == "removed"],
                           [s for s in statuses if s == "restored"])


TRASH_EVENT = {"discard": "删除对象（移入回收站）", "restore": "恢复对象", "purge": "永久删除对象"}


def trash_summary(event: dict) -> str:
    tail = f"：{event['reason']}" if event.get("reason") else ""
    return f"{TRASH_EVENT.get(event['event'], event['event'])} {event['owner_id']}「{event.get('title', '')}」{tail}"


def owner_line(owner: Owner, adopted: bool = False) -> str:
    return f"{'采用文档为对象' if adopted else '建立对象'} {owner.owner_id}「{owner.title}」"


def run_line(card: dict) -> str | None:
    word = RUN_DONE.get(card.get("status"))
    return f"{card['run_id']} {word}「{card.get('title', '')}」" if word else None


def publish_line(connector_id: str, summary: dict | None) -> str:
    parts = [f"{PUBLISH_RESULT.get(k, k)} {v}" for k, v in sorted((summary or {}).items()) if v]
    return f"发布到 {connector_id}：" + ("、".join(parts) if parts else "无变化")


class ActivityLog:
    def __init__(self, storage: Storage, hub_state: HubState | None = None):
        self.storage = storage
        self.hub_state = hub_state

    def list(self, *, owner_id: str | None = None, kinds: list[str] | None = None, limit: int = 50,
             before: str | None = None) -> dict:
        """按时间倒序分页。before：上一页最后一条的 cursor；结果中 next_before 为空表示没有更多。"""
        if not 1 <= limit <= 200:
            raise BurrowlError("INVALID_ARGUMENT", "limit 范围 1..200", {"limit": limit})
        unknown = set(kinds or []) - set(KINDS)
        if unknown:
            raise BurrowlError("INVALID_ARGUMENT", "未知的操作类型", {"kinds": sorted(unknown), "allowed": list(KINDS)})
        wanted = set(kinds or KINDS)
        registry = self.storage.owners.scan()
        owners = [o for o in registry.readable() if owner_id is None or o.owner_id == owner_id]
        if owner_id and not owners:
            registry.get(owner_id).require_readable()
        streams = [self._owner_stream(o, wanted) for o in owners]
        # R14：Run 不在对象列表中。指定对象时只列它位置下的 Run；全局时按目录修改时间惰性读取，页满即停
        if "run" in wanted and owner_id:
            streams += [self._run_stream(r, registry.run_parent(r)) for o in owners for r in registry.runs_under(o)
                        if r.readable]
        elif "run" in wanted:
            streams.append(self._all_runs_stream(registry))
        if self.hub_state is not None and "publish" in wanted and owner_id is None:
            streams.append(self._publish_stream())
        if "remove" in wanted:  # 回收站里的对象不在对象列表中，按日志单独列出
            streams.append(self._trash_stream(owner_id))
        merged = heapq.merge(*streams, key=_sort_key, reverse=True)
        if before:
            merged = (e for e in merged if _cursor(e) < before)
        page = list(islice(merged, limit + 1))
        registry.recheck_runs()  # 惰性读 Run 卡共用了目录枚举，返回前复核
        items = [e | {"cursor": _cursor(e)} for e in page[:limit]]
        return {"items": items, "next_before": items[-1]["cursor"] if len(page) > limit else None}

    # ---------- 各来源（每个都按时间倒序产出）----------
    def _owner_stream(self, owner: Owner, wanted: set[str]) -> Iterator[dict]:
        events: list[dict] = []
        if "owner" in wanted:
            adopted = owner.id_field is None
            at = owner.created_at if adopted else owner.native.get("created_at")
            if at:
                events.append({"at": at, "kind": "owner", "actor": actor_label(owner.created_by) if adopted else "—",
                               "owner_id": owner.owner_id, "summary": owner_line(owner, adopted),
                               "ref": {"kind": "owner", "owner_id": owner.owner_id}})
        commits = self._commit_stream(owner, wanted) if owner.persisted and wanted & {"attribution", "save",
                                                                                         "review", "remove"} else iter(())
        return heapq.merge(commits, iter(events), key=_sort_key, reverse=True)

    @staticmethod
    def _run_stream(run: Owner, parent: Owner | None) -> Iterator[dict]:
        """Run 结束事件；归到上级对象（R14 D-04 按位置），独立 Run 归到它自己。"""
        line = run_line(run.native)
        if line and run.native.get("ended_at"):
            yield {"at": run.native["ended_at"], "kind": "run", "actor": "—",
                   "owner_id": parent.owner_id if parent else run.owner_id, "summary": line,
                   "ref": {"kind": "run", "run_id": run.owner_id}}

    @classmethod
    def _all_runs_stream(cls, registry: OwnerIndex) -> Iterator[dict]:
        """全部 Run 的结束事件，按 _sort_key 倒序惰性产出，不读全部 Run 卡。

        Run 目录的修改时间不早于它最后一次改写 run.json，因而不早于 ended_at。按修改时间从新到旧读卡，
        堆顶事件的时间不早于"下一个未读目录的修改时间 + 容差"时，剩下的 Run 都不可能排在它前面，即可产出。"""
        pending = sorted(registry.run_entries(), key=lambda entry: entry[1], reverse=True)
        heap: list[tuple[_Desc, int, dict]] = []
        for position in range(len(pending) + 1):
            bound = pending[position][1] + MTIME_SLACK if position < len(pending) else None
            while heap and (bound is None or _timestamp(heap[0][2]["at"]) >= bound):
                yield heapq.heappop(heap)[2]
            if position == len(pending):
                return
            run = registry.load_run(pending[position][0])
            if run is None or not run.readable:
                continue
            for event in cls._run_stream(run, registry.run_parent(run)):
                heapq.heappush(heap, (_Desc(_sort_key(event)), position, event))

    def _commit_stream(self, owner: Owner, wanted: set[str]) -> Iterator[dict]:
        store = self.storage.store
        for manifest in store.iter_manifests(owner):
            if manifest.get("removed_ids") or manifest.get("restored_ids"):  # R22 墓碑提交：不写记录
                receipt = store.read_receipt(owner, manifest["commit_id"])
                if "remove" in wanted:
                    yield {"at": receipt.get("at"), "kind": "remove", "actor": actor_label(receipt.get("actor")),
                           "owner_id": owner.owner_id, "ref": {"kind": "commit", "owner_id": owner.owner_id,
                                                               "commit_id": manifest["commit_id"]},
                           "summary": removal_summary(owner.owner_id, manifest.get("removed_ids") or [],
                                                      manifest.get("restored_ids") or [])}
                continue
            records = store.commit_records(owner, manifest)
            if not records:
                continue
            visible = [r for r in records if r["sensitivity"] != "restricted"]
            receipt = store.read_receipt(owner, manifest["commit_id"])
            at, actor = records[0]["updated_at"], records[0].get("updated_by")
            for event in commit_events(owner.owner_id, at, actor, visible, manifest["commit_id"],
                                       receipt.get("attribution")):
                if event["kind"] in wanted:
                    yield event

    def _trash_stream(self, owner_id: str | None) -> Iterator[dict]:
        rows = [{"at": e["at"], "kind": "remove", "actor": actor_label(e.get("actor")), "owner_id": e["owner_id"],
                 "summary": trash_summary(e), "ref": {"kind": "owner_trash", "owner_id": e["owner_id"]}}
                for e in trash_log(self.storage) if owner_id is None or e["owner_id"] == owner_id]
        yield from sorted(rows, key=_sort_key, reverse=True)

    def _publish_stream(self) -> Iterator[dict]:
        rows = [{"at": run["finished_at"], "kind": "publish", "actor": "用户", "owner_id": None,
                 "summary": publish_line(run["connector_id"], run["summary"]),
                 "ref": {"kind": "hub_run", "connector_id": run["connector_id"], "run_id": run["run_id"]}}
                for run in self.hub_state.finished_runs()]
        yield from sorted(rows, key=_sort_key, reverse=True)


MTIME_SLACK = 2.0  # 秒：目录修改时间的精度与时钟误差容差


class _Desc:
    """让 heapq（最小堆）按 _sort_key 倒序弹出。"""
    __slots__ = ("key",)

    def __init__(self, key: tuple):
        self.key = key

    def __lt__(self, other: "_Desc") -> bool:
        return self.key > other.key


def _timestamp(at: str | None) -> float:
    try:
        return datetime.fromisoformat(str(at).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return float("-inf")  # 无法解析的时间排在最后


def _sort_key(event: dict) -> tuple:
    """倒序时：时间新的在前；同一提交里归属确认排第一，其余按摘要。"""
    return (event["at"] or "", event["kind"] == "attribution", event["summary"])


def _cursor(event: dict) -> str:
    """分页游标，与 _sort_key 同序（字符串比较）。"""
    return f"{event['at'] or ''}|{int(event['kind'] == 'attribution')}|{event['summary']}"
