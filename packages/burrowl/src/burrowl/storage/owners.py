"""对象（Owner）的发现、创建与采用。

Owner 是记忆的归属单位：Research、Project、核心算法、Run、数据卡、工具、报告，
以及显式"采用"的已有文档。身份来源始终是原登记文件（业务卡）：

    research/<slug>/research.json    → RES-…   记忆在 research/<slug>/memory/
    projects/<slug>/project.json     → PRJ-…
    core-algorithms/<slug>/module.json → ALG-…
    …/run.json                        → RUN-…
    knowledge/x.md（adopt 后）        → OBJ-…   记忆在 knowledge/x.md.memory/

首次提交记忆时才写入 <memory_home>/owner.json；扫描只解析业务卡，不读其他 JSON。
"""
import json
import os
import re
import uuid
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
         "report.json": ("report", "report_id", "RPT")}
CARD_BY_TYPE = {kind: name for name, (kind, _key, _prefix) in CARDS.items()}
EXCLUDED_DIRS = {".git", ".burrowl", ".run-captures", "node_modules", "__pycache__", "_template", "templates",
                 ".venv", "venv", ".pytest_cache", ".cache"}
OWNER_TYPES = tuple(dict.fromkeys(CONTENT_ROOTS.values()))
SENSITIVITY_RANK = {"public": 0, "internal": 1, "confidential": 2, "restricted": 3}
# 指纹排除复核/封存字段：给对象登记复核不应让所有引用它的固定版本失效。
_FINGERPRINT_EXCLUDED = {"review", "review_history", "finalization", "finalization_history"}


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


def _memory_home(native_path: str, is_card: bool) -> str:
    path = PurePosixPath(native_path)
    return (path.parent / "memory").as_posix() if is_card else native_path + ".memory"


CARD_FIELDS = {"description", "keywords", "question", "source_document", "sensitivity"}


class OwnerIndex:
    """一次扫描得到的对象集合。服务在每个操作开始时扫描一次，操作内复用。"""

    def __init__(self, owners: list[Owner]):
        self._by_id: dict[str, Owner] = {}
        for owner in owners:
            for key in (owner.owner_id, *owner.aliases):
                folded = key.casefold()
                if folded in self._by_id and self._by_id[folded] is not owner:
                    raise BurrowlError("INTEGRITY_ERROR", "重复的对象身份或别名", {"owner_id": key})
                self._by_id[folded] = owner
        self.owners = sorted(owners, key=lambda o: o.owner_id)

    def get(self, owner_id: str) -> Owner:
        owner = self._by_id.get(str(owner_id).casefold())
        if owner is None:
            raise BurrowlError("NOT_FOUND", "对象不存在", {"owner_id": owner_id})
        return owner

    def find(self, owner_id: str) -> Owner | None:
        return self._by_id.get(str(owner_id).casefold())

    def readable(self, owner_type: str | None = None) -> list[Owner]:
        return [o for o in self.owners if o.readable and (owner_type is None or o.owner_type == owner_type)]

    def owner_of(self, relative_path: str) -> list[Owner]:
        """数据目录内某个文件所属的对象（由内到外；嵌套时如 Run 在研究目录下，都会列出）。"""
        target = PurePosixPath(relative_path.replace("\\", "/"))
        found = []
        for owner in self.owners:
            native = PurePosixPath(owner.native_path)
            home = native.parent if native.suffix == ".json" else native  # 业务卡所在目录，或被采用的文档本身
            if target == home or home in target.parents:
                found.append(owner)
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
    def scan(self) -> OwnerIndex:
        scope = ReadScope()
        root = self.ws.root
        cards: dict[str, Owner] = {}
        persisted: list[tuple[str, dict]] = []
        for base in CONTENT_ROOTS:
            start = safe_path(root, base, scope)
            if not start.is_dir():
                continue
            for folder, dirs, files in os.walk(start, followlinks=False):
                rel_folder = Path(folder).relative_to(root).as_posix()
                for name in list(dirs):
                    if name.casefold() in EXCLUDED_DIRS:
                        dirs.remove(name)
                    elif name == "memory" or name.endswith(".memory"):
                        dirs.remove(name)  # 记忆目录只读 owner.json，不当作业务内容
                        descriptor = safe_path(root, f"{rel_folder}/{name}/owner.json", scope)
                        if descriptor.is_file():
                            persisted.append((f"{rel_folder}/{name}", read_json(descriptor)))
                for name in files:
                    if name in CARDS:
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
        return OwnerIndex(owners)

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
        if self.scan().find(card[key]):
            raise BurrowlError("VERSION_CONFLICT", "对象身份已存在", {"owner_id": card[key]})
        target.parent.mkdir(parents=True)
        write_new(target, json.dumps(card, ensure_ascii=False, indent=2).encode("utf-8") + b"\n")
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
        index = self.scan()
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
        return self.scan().get(owner.owner_id)
