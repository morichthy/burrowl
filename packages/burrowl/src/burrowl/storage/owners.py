"""对象（Owner）的发现、创建与采用。

Owner 是记忆的归属单位：Research、Project、核心算法、Run、数据卡、工具、报告，
以及显式"采用"的已有文档。身份来源始终是原登记文件（业务卡）：

    research/<slug>/research.json    → RES-…   记忆在 research/<slug>/memory/
    projects/<slug>/project.json     → PRJ-…
    core-algorithms/<slug>/module.json → ALG-…
    …/run.json                        → RUN-…
    knowledge/<slug>/knowledge.json  → KNW-…   概念库等知识对象（R12 D-12）
    knowledge/x.md（adopt 后）        → OBJ-…   记忆在 knowledge/x.md.memory/

首次提交记忆时才写入 <memory_home>/owner.json；扫描只解析业务卡，不读其他 JSON。
"""
import contextlib
import json
import os
import re
import uuid
from collections.abc import Iterator
from contextvars import ContextVar
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any

from ..core.atomic import read_json, write_new
from ..core.clock import utc_now
from ..core.errors import BurrowlError
from ..core.hashing import canonical_bytes, sha256_bytes, strict_json_loads
from ..core.jsonschema import validate
from ..core.pathsafe import ReadScope, safe_path
from ..core.schema import defs
from .layout import CONTENT_ROOTS, Workspace

# 业务卡文件名 → (对象类型, 身份字段, 新建时的 ID 前缀)
CARDS = {"research.json": ("research", "research_id", "RES"), "project.json": ("project", "project_id", "PRJ"),
         "module.json": ("core-algorithm", "module_id", "ALG"), "run.json": ("run", "run_id", "RUN"),
         "dataset.json": ("data", "dataset_id", "DAT"), "tool.json": ("tool", "tool_id", "TOOL"),
         "report.json": ("report", "report_id", "RPT"), "knowledge.json": ("knowledge", "knowledge_id", "KNW")}
CARD_BY_TYPE = {kind: name for name, (kind, _key, _prefix) in CARDS.items()}
RUN_CARD = "run.json"
RUNS_DIR = "runs"  # R14：Run 目录（顶层 runs/ 与 <对象目录>/runs/）不参与对象扫描，目录名是小写的 Run 编号
EXCLUDED_DIRS = {".git", ".burrowl", ".run-captures", "node_modules", "__pycache__", "_template", "templates",
                 ".venv", "venv", ".pytest_cache", ".cache"}
OWNER_TYPES = tuple(dict.fromkeys(CONTENT_ROOTS.values()))
SENSITIVITY_RANK = {"public": 0, "internal": 1, "confidential": 2, "restricted": 3}
# 指纹排除复核/封存字段：给对象登记复核不应让所有引用它的固定版本失效。
_FINGERPRINT_EXCLUDED = {"review", "review_history", "finalization", "finalization_history"}
# 一次操作内复用的扫描结果（数据目录 → OwnerIndex），见 OwnerRegistry.request_scope；作用域外为 None
_SCAN_MEMO: ContextVar[dict[str, "OwnerIndex"] | None] = ContextVar("burrowl_owner_scan", default=None)


def card_fingerprint(card: Any) -> str:
    """业务卡内容指纹（owner 引用的 sha256）。与旧系统算法一致。"""
    def content(item: Any) -> Any:
        if isinstance(item, dict):
            return {k: content(v) for k, v in item.items() if k not in _FINGERPRINT_EXCLUDED}
        if isinstance(item, list):
            return [content(v) for v in item]
        return item
    return sha256_bytes(json.dumps(content(card), sort_keys=True, ensure_ascii=False,
                                   separators=(",", ":")).encode("utf-8"))


@dataclass
class Owner:
    owner_id: str
    owner_type: str
    native_path: str                 # 数据目录相对路径（业务卡或被采用的文档）
    id_field: str | None             # 业务卡中的身份字段；被采用文档为 None
    memory_home: str
    title: str
    fingerprint: str                 # owner 引用使用的指纹
    native: dict[str, Any] = field(default_factory=dict)
    persisted: bool = False          # owner.json 是否已写入
    aliases: list[str] = field(default_factory=list)
    created_at: str | None = None
    created_by: dict | None = None

    @property
    def sensitivity(self) -> str:
        value = self.native.get("sensitivity", "internal")
        return value if value in SENSITIVITY_RANK else "restricted"

    @property
    def readable(self) -> bool:
        return self.sensitivity != "restricted"

    def require_readable(self) -> "Owner":
        if not self.readable:
            raise BurrowlError("ACCESS_DENIED", "此对象不在当前可读范围", {"owner_id": self.owner_id})
        return self

    def native_ref(self) -> dict[str, Any]:
        return {"path": self.native_path, "id_field": self.id_field,
                "id_value": self.owner_id if self.id_field else None}

    def descriptor(self) -> dict[str, Any]:
        """写入 owner.json 的内容（OwnerDescriptor 契约）。"""
        value = {"schema_version": 1, "owner_id": self.owner_id, "owner_type": self.owner_type,
                 "native_ref": self.native_ref(), "memory_home": self.memory_home, "adapter_version": 1,
                 "created_at": self.created_at, "created_by": self.created_by}
        if self.aliases:
            value["aliases"] = self.aliases
        return value

    def summary(self) -> dict[str, Any]:
        """对外展示的元数据（不含业务卡全文）。"""
        return {"owner_id": self.owner_id, "owner_type": self.owner_type, "title": self.title,
                "native_path": self.native_path, "memory_home": self.memory_home,
                "persisted": self.persisted, "sensitivity": self.sensitivity}


def require_knowledge_scope(owner_ids) -> None:
    """检索与阅读的对象范围不接受 Run（R14 D-07：Run 是执行证据，不进检索；显式查找用 runs.search）。"""
    runs = [o for o in owner_ids or [] if str(o).casefold().startswith("run-")]
    if runs:
        raise BurrowlError("INVALID_ARGUMENT", "Run 不参与检索与阅读；查找 Run 用 runs.search，或从引用它的记录展开",
                           {"run_ids": runs, "use": "runs.search"})


def _memory_home(native_path: str, is_card: bool) -> str:
    path = PurePosixPath(native_path)
    return (path.parent / "memory").as_posix() if is_card else native_path + ".memory"


# short_name：对象的可读短名，供 [[@短名/handle]] 链接使用（R12 D-28）
CARD_FIELDS = {"description", "keywords", "question", "source_document", "sensitivity", "short_name"}


class OwnerIndex:
    """一次扫描得到的对象集合。服务在每个操作开始时扫描一次，操作内复用。

    R14 D-02：`owners` 与 `readable()` 只含知识对象，不含 Run。按编号取 Run（`get` / `find` 传入 RUN-…）
    时经 RunLocator 按位置查找，结果在本索引内复用；枚举 Run 用 `runs_under` / `all_runs`。
    """

    def __init__(self, owners: list[Owner], runs: "RunLocator | None" = None):
        self._by_id: dict[str, Owner] = {}
        for owner in owners:
            for key in (owner.owner_id, *owner.aliases):
                folded = key.casefold()
                if folded in self._by_id and self._by_id[folded] is not owner:
                    raise BurrowlError("INTEGRITY_ERROR", "重复的对象身份或别名", {"owner_id": key})
                self._by_id[folded] = owner
        self.owners = sorted(owners, key=lambda o: o.owner_id)
        self._runs = runs
        self._run_cache: dict[str, Owner | None] = {}
        self._all_runs: list[Owner] | None = None

    def get(self, owner_id: str) -> Owner:
        owner = self.find(owner_id)
        if owner is None:
            raise BurrowlError("NOT_FOUND", "对象不存在", {"owner_id": owner_id})
        return owner

    def find(self, owner_id: str) -> Owner | None:
        folded = str(owner_id).casefold()
        owner = self._by_id.get(folded)
        if owner is None and self._runs is not None and folded.startswith("run-"):
            if folded not in self._run_cache:
                self._run_cache[folded] = self._runs.find(str(owner_id), self)
            owner = self._run_cache[folded]
        return owner

    def readable(self, owner_type: str | None = None) -> list[Owner]:
        return [o for o in self.owners if o.readable and (owner_type is None or o.owner_type == owner_type)]

    # ---------- Run（R14：不在 owners 中）----------
    def runs_under(self, owner: Owner | None) -> list[Owner]:
        """某对象位置下的 Run；None 表示顶层 runs/ 下的独立 Run。只列该位置，不枚举全部 Run。"""
        return self._runs.list_under(owner, self) if self._runs else []

    def all_runs(self) -> list[Owner]:
        """全部 Run（显式全量：runs.search 无对象限定、维护、全局关键操作记录）。成本随 Run 数增长。"""
        if self._all_runs is None:
            self._all_runs = list(self._runs.iter_all(self)) if self._runs else []
        return self._all_runs

    def run_entries(self) -> list[tuple[str, float]]:
        """全部 Run 目录（相对路径, 目录修改时间），只列目录、不读业务卡；用于按时间或编号惰性读取。"""
        return self._runs.entries(self) if self._runs else []

    def recheck_runs(self) -> None:
        """批量读取（run_entries / load_run）结束、返回数据前调用：核对期间目录未被替换。"""
        if self._runs is not None:
            self._runs.recheck()

    def load_run(self, folder: str) -> Owner | None:
        """读取 run_entries 给出的某个 Run 目录的业务卡；读完后调用 recheck_runs()。"""
        run = self._runs.load(folder) if self._runs else None
        if run is not None:
            self._run_cache.setdefault(run.owner_id.casefold(), run)
        return run

    def run_parent(self, run: Owner) -> Owner | None:
        """Run 的上级对象（R14 D-04：按目录位置；None 表示独立 Run）。"""
        return self._runs.parent_of(run, self) if self._runs else None

    def owner_of(self, relative_path: str) -> list[Owner]:
        """数据目录内某个文件所属的对象（由内到外；嵌套时如 Run 在研究目录下，都会列出）。"""
        target = PurePosixPath(relative_path.replace("\\", "/"))
        found = []
        for owner in self.owners:
            native = PurePosixPath(owner.native_path)
            home = native.parent if native.suffix == ".json" else native  # 业务卡所在目录，或被采用的文档本身
            if target == home or home in target.parents:
                found.append(owner)
        if self._runs is not None and (run := self._runs.of_path(target)) is not None:
            found.append(run)  # Run 不在 owners 中，按路径单独找到（独立 Run 的敏感级别只在它自己的卡里）
        return sorted(found, key=lambda o: len(PurePosixPath(o.native_path).parts), reverse=True)

    def require_path_readable(self, relative_path: str) -> None:
        """文件所属的任一对象受限时拒绝（受限对象的材料不能借登记或引用外泄）。"""
        for owner in self.owner_of(relative_path):
            if not owner.readable:
                raise BurrowlError("ACCESS_DENIED", "该文件属于受限对象", {"owner_id": owner.owner_id,
                                                                      "path": relative_path})


class OwnerRegistry:
    def __init__(self, workspace: Workspace):
        self.ws = workspace

    # ---------- 发现 ----------
    @staticmethod
    @contextlib.contextmanager
    def request_scope() -> Iterator[None]:
        """一次操作（接口层的一次 call）内只扫描一次内容树，之后的 scan() 复用同一结果。

        扫描要遍历全部业务卡并逐段做路径检查，成本随对象数线性增长；同一请求里重复扫描得到的是同一份
        对象列表。写业务卡或 owner.json 的代码调用 invalidate()；发布前的依据复核用 scan(fresh=True)
        重新扫描，所以请求开头之后别的进程改了对象，仍会在发布前被拦下。新线程不继承作用域。
        """
        if _SCAN_MEMO.get() is not None:  # 嵌套调用沿用外层作用域
            yield
            return
        token = _SCAN_MEMO.set({})
        try:
            yield
        finally:
            _SCAN_MEMO.reset(token)

    def invalidate(self) -> None:
        """本进程刚写过业务卡或 owner.json：丢弃本次操作内复用的扫描结果。"""
        if (memo := _SCAN_MEMO.get()) is not None:
            memo.pop(str(self.ws.root), None)

    def scan(self, *, fresh: bool = False) -> OwnerIndex:
        """当前全部对象。在 request_scope 内复用本次操作已有的结果；fresh=True 总是重新扫描并更新复用结果。"""
        memo, key = _SCAN_MEMO.get(), str(self.ws.root)
        if memo is not None and not fresh and key in memo:
            return memo[key]
        index = self._scan()
        if memo is not None:
            memo[key] = index
        return index

    def _scan(self) -> OwnerIndex:
        scope = ReadScope()
        root = self.ws.root
        cards: dict[str, Owner] = {}
        persisted: list[tuple[str, dict]] = []
        for base in CONTENT_ROOTS:
            if base == RUNS_DIR:  # R14 D-02：顶层独立 Run 不参与对象扫描
                continue
            start = safe_path(root, base, scope)
            if not start.is_dir():
                continue
            for folder, dirs, files in os.walk(start, followlinks=False):
                rel_folder = Path(folder).relative_to(root).as_posix()
                for name in list(dirs):
                    if name.casefold() in EXCLUDED_DIRS or name == RUNS_DIR:  # R14 D-02：对象内 Run 也不扫描
                        dirs.remove(name)
                    elif name == "memory" or name.endswith(".memory"):
                        dirs.remove(name)  # 记忆目录只读 owner.json，不当作业务内容
                        descriptor = safe_path(root, f"{rel_folder}/{name}/owner.json", scope)
                        if descriptor.is_file():
                            persisted.append((f"{rel_folder}/{name}", read_json(descriptor)))
                for name in files:
                    if name in CARDS and name != RUN_CARD:  # Run 只经 RunLocator 按位置查找
                        rel = f"{rel_folder}/{name}"
                        owner = self._card_owner(rel, safe_path(root, rel, scope).read_bytes())
                        if owner.owner_id.casefold() in cards:
                            raise BurrowlError("INTEGRITY_ERROR", "重复的业务身份", {"owner_id": owner.owner_id,
                                                                                "path": rel})
                        cards[owner.owner_id.casefold()] = owner
        owners = list(cards.values())
        claimed: set[str] = set()
        for home, descriptor in persisted:
            owner = self._attach_descriptor(home, descriptor, cards, scope)
            if owner.native_path in claimed:
                raise BurrowlError("INTEGRITY_ERROR", "同一原对象被重复认领", {"path": owner.native_path})
            claimed.add(owner.native_path)
            if owner.id_field is None:
                owners.append(owner)
        scope.recheck()
        return OwnerIndex(owners, RunLocator(self))

    def _card_owner(self, rel: str, raw: bytes) -> Owner:
        kind, key, _prefix = CARDS[PurePosixPath(rel).name]
        try:
            card = strict_json_loads(raw.decode("utf-8-sig"))
        except (ValueError, UnicodeError) as exc:
            raise BurrowlError("INTEGRITY_ERROR", "业务卡不是合法 JSON", {"path": rel}) from exc
        owner_id = card.get(key) if isinstance(card, dict) else None
        if not isinstance(owner_id, str) or not owner_id.strip():
            raise BurrowlError("INTEGRITY_ERROR", "业务卡缺少身份字段", {"path": rel, "field": key})
        return Owner(owner_id=owner_id, owner_type=kind, native_path=rel, id_field=key,
                     memory_home=_memory_home(rel, True), title=card.get("title") or card.get("name") or owner_id,
                     fingerprint=card_fingerprint(card), native=card)

    def _attach_descriptor(self, home: str, descriptor: dict, cards: dict[str, Owner], scope: ReadScope) -> Owner:
        all_defs = defs()
        if errors := validate(descriptor, all_defs["OwnerDescriptor"], all_defs):
            raise BurrowlError("INTEGRITY_ERROR", "owner.json 不符合契约", {"memory_home": home}, errors)
        ref = descriptor["native_ref"]
        if descriptor["memory_home"] != home:
            raise BurrowlError("INTEGRITY_ERROR", "owner.json 记录的位置与实际位置不一致", {"memory_home": home})
        if ref.get("id_field"):
            owner = cards.get(descriptor["owner_id"].casefold())
            if owner is None or owner.native_path != ref["path"] or owner.memory_home != home:
                raise BurrowlError("INTEGRITY_ERROR", "记忆入口找不到对应的业务卡；移动对象需显式处理",
                               {"memory_home": home})
        else:
            native = safe_path(self.ws.root, ref["path"], scope)
            if not native.is_file() or _memory_home(ref["path"], False) != home \
                    or not re.fullmatch(r"OBJ-[0-9a-f-]{36}", descriptor["owner_id"]):
                raise BurrowlError("INTEGRITY_ERROR", "被采用文档缺失或入口不匹配", {"memory_home": home})
            owner = Owner(owner_id=descriptor["owner_id"], owner_type=descriptor["owner_type"],
                          native_path=ref["path"], id_field=None, memory_home=home,
                          title=PurePosixPath(ref["path"]).stem, fingerprint=sha256_bytes(native.read_bytes()))
        owner.persisted = True
        owner.aliases = list(descriptor.get("aliases", []))
        owner.created_at, owner.created_by = descriptor["created_at"], descriptor["created_by"]
        return owner

    # ---------- 创建与采用 ----------
    def create(self, owner_type: str, slug: str, title: str, fields: dict[str, Any] | None = None) -> Owner:
        """新建带业务卡的对象（Run 由 runs 服务创建）。slug 只允许小写字母、数字和连字符。"""
        if owner_type not in CARD_BY_TYPE or owner_type == "run":
            raise BurrowlError("INVALID_ARGUMENT", "不支持在此创建该类型对象", {"owner_type": owner_type})
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,63}", slug):
            raise BurrowlError("INVALID_ARGUMENT", "slug 只能包含小写字母、数字和连字符")
        if slug == RUNS_DIR:  # runs/ 目录不参与对象扫描（R14 D-02）
            raise BurrowlError("INVALID_ARGUMENT", "slug 不能是 runs")
        fields = dict(fields or {})
        if set(fields) - CARD_FIELDS:
            raise BurrowlError("INVALID_ARGUMENT", "业务卡只能补充：" + "、".join(sorted(CARD_FIELDS)))
        if owner_type == "core-algorithm" and not str(fields.get("source_document") or "").strip():
            # 核心算法只收录公司算法/模块文档定义的关键算法；普通脚本、软件模块和通用工具不建算法卡。
            raise BurrowlError("INVALID_ARGUMENT", "核心算法必须给出定义它的公司文档（source_document）；"
                           "没有文档依据的候选请记在 research 对象中")
        base = next(root for root, kind in CONTENT_ROOTS.items() if kind == owner_type)
        name = CARD_BY_TYPE[owner_type]
        _kind, key, prefix = CARDS[name]
        rel = f"{base}/{slug}/{name}"
        card = {"schema_version": 1, key: f"{prefix}-{slug.upper()}", "slug": slug, "title": title,
                "status": "active", "sensitivity": "internal", "created_at": utc_now(), **fields}
        target = self.ws.path(rel)
        if target.parent.exists():
            raise BurrowlError("VERSION_CONFLICT", "同名对象目录已存在", {"path": f"{base}/{slug}"})
        if self.scan(fresh=True).find(card[key]):  # 身份唯一性检查不用复用结果
            raise BurrowlError("VERSION_CONFLICT", "对象身份已存在", {"owner_id": card[key]})
        target.parent.mkdir(parents=True)
        write_new(target, json.dumps(card, ensure_ascii=False, indent=2).encode("utf-8") + b"\n")
        self.invalidate()
        return self.scan().get(card[key])

    def adopt(self, path: str, expected_sha256: str, actor: dict) -> Owner:
        """把一份已有文档登记为对象（原件不修改）；调用者须给出它看到的字节指纹。"""
        rel = PurePosixPath(path.replace("\\", "/")).as_posix()
        native = self.ws.path(rel)
        top = rel.split("/", 1)[0]
        if top not in CONTENT_ROOTS or not native.is_file() or native.name in CARDS:
            raise BurrowlError("INVALID_ARGUMENT", "只能采用内容树中的普通文件", {"path": rel})
        if sha256_bytes(native.read_bytes()) != expected_sha256:
            raise BurrowlError("STALE_BASIS", "采用目标已变化", {"path": rel})
        index = self.scan(fresh=True)  # 判断是否已被采用，不用复用结果
        for owner in index.owners:
            if owner.native_path == rel:
                return owner
        owner = Owner(owner_id=f"OBJ-{uuid.uuid4()}", owner_type=CONTENT_ROOTS[top], native_path=rel, id_field=None,
                      memory_home=_memory_home(rel, False), title=native.stem, fingerprint=expected_sha256)
        return self.persist(owner, actor)

    def persist(self, owner: Owner, actor: dict) -> Owner:
        """首次提交前写入 owner.json。并发时先到者获胜，后到者读取已发布的入口。"""
        if owner.persisted:
            return owner
        owner.require_readable()
        owner.created_at, owner.created_by = utc_now(), actor
        descriptor = self.ws.path(owner.memory_home + "/owner.json")
        descriptor.parent.mkdir(parents=True, exist_ok=True)
        try:
            write_new(descriptor, canonical_bytes(owner.descriptor()))
        except FileExistsError:
            pass
        self.invalidate()
        return self.scan().get(owner.owner_id)


class RunLocator:
    """R14 D-03 / D-04：按编号与位置定位 Run。

    Run 目录是 `runs/<编号小写>/`（独立 Run）或 `<上级对象目录>/runs/<编号小写>/`（对象内 Run）。按编号查找时只
    检查这些候选位置（每个知识对象一次），成本与 Run 总数无关；目录名不规范的旧数据才走全量遍历。
    Run 的上级按目录位置判断；同一目录有多个被采用文档时，才参考业务卡的 owner_id。
    """

    def __init__(self, registry: OwnerRegistry):
        self.registry = registry
        # 批量读取（entries + load）共用的目录枚举：runs/ 目录可能有上千项，逐张读卡时各自列一遍会变成平方级。
        # 用完必须 recheck()，核对读取期间父目录没有被替换。按编号的单个查找不用它，每次独立做路径检查。
        self._scope: ReadScope | None = None

    def _bulk(self) -> ReadScope:
        if self._scope is None:
            self._scope = ReadScope()
        return self._scope

    def load(self, folder: str) -> Owner | None:
        """批量读取中的一张 Run 卡（复用 entries 的目录枚举）。调用方读完后调用 recheck()。"""
        return self._load(folder, self._bulk())

    def recheck(self) -> None:
        """核对批量读取期间用过的目录未被替换成链接或联接，然后丢弃枚举缓存。"""
        if self._scope is not None:
            scope, self._scope = self._scope, None
            scope.recheck()

    def entries(self, owners: OwnerIndex) -> list[tuple[str, float]]:
        """全部 Run 目录与其修改时间（os.scandir 自带的元数据，不额外调用、不读业务卡）。

        目录修改时间是 Run 最后一次改写 run.json 的上界：run.json 经临时文件替换写入，目录项随之变化。"""
        found = []
        for base in self._bases(owners):
            rel = self._runs_dir(base)
            directory = safe_path(self.registry.ws.root, rel, self._bulk())
            if not directory.is_dir():
                continue
            with os.scandir(directory) as items:
                for item in items:
                    if item.is_dir(follow_symlinks=False) and not item.is_junction():
                        found.append((f"{rel}/{item.name}", item.stat(follow_symlinks=False).st_mtime))
        return found

    def _load(self, folder: str, scope: ReadScope | None = None) -> Owner | None:
        path = safe_path(self.registry.ws.root, f"{folder}/{RUN_CARD}", scope)
        if not path.is_file():
            return None
        return self.registry._card_owner(f"{folder}/{RUN_CARD}", path.read_bytes())

    @staticmethod
    def _bases(owners: OwnerIndex) -> list[str]:
        """可能放 Run 的位置：顶层，以及每个知识对象业务卡（或被采用文档）所在目录。"""
        bases = {PurePosixPath(o.native_path).parent.as_posix() for o in owners.owners}
        return [".", *sorted(b for b in bases if b != ".")]

    @staticmethod
    def _runs_dir(base: str) -> str:
        return RUNS_DIR if base == "." else f"{base}/{RUNS_DIR}"

    def find(self, run_id: str, owners: OwnerIndex) -> Owner | None:
        name = run_id.casefold()
        if not re.fullmatch(r"run-[0-9a-z-]{1,80}", name):
            return None
        root = self.registry.ws.root
        for base in self._bases(owners):
            folder = f"{self._runs_dir(base)}/{name}"
            if (root / folder).is_dir() and (run := self._load(folder)) and run.owner_id.casefold() == name:
                return run
        # 慢路径：目录名不是编号小写的旧数据
        return next((r for r in self.iter_all(owners) if r.owner_id.casefold() == name), None)

    def _list(self, base: str) -> list[Owner]:
        scope = ReadScope()  # 本次列出共用一份目录枚举，返回前复核
        directory = safe_path(self.registry.ws.root, self._runs_dir(base), scope)
        if not directory.is_dir():
            return []
        found = []
        for child in sorted(directory.iterdir()):
            if child.is_dir() and not child.is_symlink() and (
                    run := self._load(f"{self._runs_dir(base)}/{child.name}", scope)):
                found.append(run)
        scope.recheck()
        return found

    def iter_all(self, owners: OwnerIndex) -> Iterator[Owner]:
        seen: dict[str, str] = {}
        for base in self._bases(owners):
            for run in self._list(base):
                key = run.owner_id.casefold()
                if key in seen:
                    raise BurrowlError("INTEGRITY_ERROR", "重复的 Run 身份", {"owner_id": run.owner_id,
                                                                       "paths": [seen[key], run.native_path]})
                seen[key] = run.native_path
                yield run

    def parent_of(self, run: Owner, owners: OwnerIndex) -> Owner | None:
        base = PurePosixPath(run.native_path).parent.parent.parent.as_posix()  # <base>/runs/<id>/run.json
        if base == ".":
            return None
        here = [o for o in owners.owners if PurePosixPath(o.native_path).parent.as_posix() == base]
        cards = [o for o in here if o.id_field is not None]
        if len(cards) == 1:
            return cards[0]
        declared = run.native.get("owner_id")
        return next((o for o in here if o.owner_id == declared), None)

    def list_under(self, owner: Owner | None, owners: OwnerIndex) -> list[Owner]:
        if owner is None:
            return self._list(".")
        base = PurePosixPath(owner.native_path).parent.as_posix()
        return [r for r in self._list(base) if (p := self.parent_of(r, owners)) is not None
                and p.owner_id == owner.owner_id]

    def of_path(self, target: PurePosixPath) -> Owner | None:
        """路径位于某个 Run 目录内时返回该 Run（权限检查用）。"""
        parts = target.parts
        for i, part in enumerate(parts[:-1]):
            if part == RUNS_DIR:
                # 路径不安全时照常报错，不能当作"不属于任何 Run"而放过受限检查
                if (run := self._load("/".join(parts[:i + 2]))) is not None:
                    return run
        return None
