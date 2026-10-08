"""Markdown 库连接器（Obsidian 风格）：把发布条目写成带 YAML 头的 .md 文件，可被 Obsidian、
WeKnora 的文件夹导入等其他工具直接使用。也是连接器 SPI 的第二个参考实现。

    <vault>/<collection>/<文件名>.md        知识条目（front matter 含 burrowl_local_key / burrowl_hash / burrowl_ref）
    <vault>/<collection>/records/<对象>/<handle>.md、documents/…   条目带交换格式路径时（R12 T-P3.3）
    <vault>/<collection>/wiki/<slug>.md     Wiki 页面

条目带 path 时按交换格式写：正文已由渲染器生成（[[链接]] 指向库内相对路径、自动生成区块），
YAML 头合并交换格式字段与 burrowl_local_key / burrowl_hash；漂移规则不变。

options: {"path": "D:/Vault"}；只在该目录内读写，文件名由 local_key 派生（不接受外部路径）。
"""
import json
import re
from pathlib import Path
from typing import Any

from ..core.hashing import sha256_bytes
from .spi import (
    Capabilities,
    Collection,
    ConnectorError,
    ExternalItem,
    ExternalRef,
    Hit,
    PublishItem,
    WikiPage,
)

_FRONT = re.compile(r"\A---\n(.*?)\n---\n", re.S)
_SAFE = re.compile(r"[^0-9A-Za-z\u4e00-\u9fff._-]+")


def _front_matter(fields: dict[str, Any]) -> str:
    lines = ["---"]
    for key, value in fields.items():
        if isinstance(value, list | tuple) and all(isinstance(v, str | int | float) for v in value):
            lines.append(f"{key}: [{', '.join(str(v) for v in value)}]")
        elif isinstance(value, dict | list | tuple | bool) or value is None:
            lines.append(f"{key}: {json.dumps(value, ensure_ascii=False)}")  # JSON 是 YAML 的子集
        else:
            lines.append(f"{key}: {str(value).replace(chr(10), ' ')}")
    return "\n".join([*lines, "---", ""])


def _plain(value: str | None) -> str | None:
    """YAML 头的值可能是 JSON 写法（交换格式用 JSON 写字符串）；读回时去掉引号。"""
    if value and len(value) >= 2 and value[0] == value[-1] == '"':
        try:
            return json.loads(value)
        except ValueError:
            return value
    return value


def _parse(text: str) -> tuple[dict[str, str], str]:
    match = _FRONT.match(text)
    if not match:
        return {}, text
    meta = dict(line.split(": ", 1) for line in match[1].splitlines() if ": " in line)
    return meta, text[match.end():]


class MarkdownVault:
    type = "mdvault"

    def __init__(self, connector_id: str, *, base_url: str | None = None, api_key: str | None = None,
                 tenant_id: str | None = None, options: dict | None = None):
        self.id = connector_id
        path = (options or {}).get("path")
        if not path:
            raise ConnectorError("mdvault 需要 options.path（Markdown 库目录）")
        self.root = Path(path).resolve()

    def _dir(self, collection: Collection, *parts: str) -> Path:
        target = self.root.joinpath(collection.id, *parts).resolve()
        if not target.is_relative_to(self.root):
            raise ConnectorError("路径越出 Markdown 库")
        return target

    @staticmethod
    def _name(local_key: str) -> str:
        return _SAFE.sub("_", local_key.replace(":", "-").replace("/", "-"))[:150] + ".md"

    @staticmethod
    def wiki_stem(slug: str) -> str:
        """Wiki 页面在库内的路径（不带 .md），渲染器据此改写指向 Wiki 页的链接。"""
        return "wiki/" + _SAFE.sub("_", slug.replace("/", "__"))

    def capabilities(self) -> Capabilities:
        return Capabilities(metadata_max_keys=50, wiki_read=True, wiki_write=True)

    def health(self) -> dict[str, Any]:
        return {"ok": self.root.is_dir(), "server_version": "mdvault-1", "compat": "ok", "path": str(self.root)}

    def ensure_collection(self, name: str) -> Collection:
        safe = _SAFE.sub("_", name)[:80] or "default"
        self.root.joinpath(safe).mkdir(parents=True, exist_ok=True)
        return Collection(safe, name)

    def upsert(self, collection: Collection, item: PublishItem, existing: ExternalRef | None) -> ExternalRef:
        name = item.path if item.path else (existing.external_id if existing else self._name(item.local_key))
        path = self._dir(collection, *name.split("/"))
        if existing is None and path.exists():
            raise ConnectorError("duplicate", status=409)
        meta = {"title": item.title, "burrowl_local_key": item.local_key, "burrowl_hash": item.content_hash,
                "burrowl_ref": item.ref_uri, "tags": list(item.tags), **item.metadata, **(item.front_matter or {})}
        if existing is not None and existing.external_id != name:  # 旧格式文件名 → 交换格式路径：搬家
            self._dir(collection, *existing.external_id.split("/")).unlink(missing_ok=True)
        path.parent.mkdir(parents=True, exist_ok=True)
        body = item.markdown if item.path else f"# {item.title}\n\n{item.markdown}"
        path.write_text(_front_matter(meta) + body + "\n", encoding="utf-8")
        return ExternalRef(name, "completed")

    def _find(self, external_id: str) -> Path | None:
        found = [p for p in self.root.glob(f"*/{external_id}") if p.is_file()]
        return found[0] if found else None

    def delete(self, collection: Collection, ref: ExternalRef) -> None:
        self._dir(collection, *ref.external_id.split("/")).unlink(missing_ok=True)

    def status(self, refs: list[ExternalRef]) -> list[ExternalRef]:
        return [ExternalRef(ref.external_id, "completed" if self._find(ref.external_id) else "missing") for ref in refs]

    def _entries(self, collection: Collection):
        """库中的知识条目：集合根目录下的旧格式文件，以及交换格式的 records/、documents/ 子目录（wiki/ 另算）。"""
        base = self._dir(collection)
        for path in sorted(base.rglob("*.md")):
            relative = path.relative_to(base).as_posix()
            if not relative.startswith("wiki/"):
                yield relative, path

    def list_items(self, collection: Collection) -> list[ExternalItem]:
        items = []
        for relative, path in self._entries(collection):
            meta, _ = _parse(path.read_text(encoding="utf-8"))
            if meta.get("burrowl_local_key"):
                items.append(ExternalItem(relative, _plain(meta.get("burrowl_local_key")), _plain(meta.get("burrowl_hash")),
                                          _plain(meta.get("title", ""))))
        return items

    def search(self, collections: list[Collection], query: str, limit: int) -> list[Hit]:
        terms = [t for t in query.casefold().split() if t]
        hits = []
        for collection in collections:
            for relative, path in self._entries(collection):
                meta, body = _parse(path.read_text(encoding="utf-8"))
                score = sum(body.casefold().count(t) for t in terms)
                if score:
                    hits.append(Hit(relative, _plain(meta.get("title", path.stem)), body[:300], float(score),
                                    _plain(meta.get("burrowl_local_key"))))
        return sorted(hits, key=lambda h: -h.score)[:limit]

    def read(self, ref: ExternalRef) -> dict[str, Any]:
        path = self._find(ref.external_id)
        if path is None:
            raise ConnectorError("not found", status=404)
        meta, body = _parse(path.read_text(encoding="utf-8"))
        return {"title": _plain(meta.get("title", "")), "content": body, "metadata": meta}

    # ---------- Wiki ----------
    def _page_path(self, collection: Collection, slug: str) -> Path:
        return self._dir(collection, "wiki", _SAFE.sub("_", slug.replace("/", "__")) + ".md")

    def _load_page(self, path: Path) -> WikiPage:
        meta, body = _parse(path.read_text(encoding="utf-8"))
        current = sha256_bytes(body.encode("utf-8"))
        edited = "agent" if meta.get("burrowl_hash") == current else "user"  # 文件被手工改过即视为外部编辑
        return WikiPage(slug=meta.get("slug", path.stem), title=meta.get("title", path.stem), markdown=body,
                        page_type=meta.get("page_type", "synthesis"), version=int(meta.get("version", 1)),
                        edited_by=edited, content_hash=meta.get("burrowl_hash", ""))

    def wiki_list(self, collection: Collection, query: str = "", limit: int = 100) -> list[WikiPage]:
        pages = [self._load_page(p) for p in sorted(self._dir(collection, "wiki").glob("*.md"))]
        return [p for p in pages if query.casefold() in (p.title + p.markdown).casefold()][:limit]

    def wiki_read(self, collection: Collection, slug: str) -> WikiPage | None:
        path = self._page_path(collection, slug)
        return self._load_page(path) if path.exists() else None

    def wiki_write(self, collection: Collection, page: WikiPage) -> WikiPage:
        path = self._page_path(collection, page.slug)
        previous = self._load_page(path).version if path.exists() else 0
        body = page.markdown
        meta = {"slug": page.slug, "title": page.title, "page_type": page.page_type, "version": (previous or 0) + 1,
                "burrowl_hash": sha256_bytes(body.encode("utf-8"))}
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(_front_matter(meta) + body, encoding="utf-8")
        return self._load_page(path)

    def wiki_delete(self, collection: Collection, slug: str) -> None:
        self._page_path(collection, slug).unlink(missing_ok=True)

    def wiki_configure(self, collection: Collection, *, auto: bool, synthesis_model_id: str | None) -> dict:
        return {"auto": False, "note": "Markdown 库不支持自动生成 Wiki"}
