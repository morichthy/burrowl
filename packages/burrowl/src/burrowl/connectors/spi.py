"""连接器接口（SPI）。同步引擎只通过这些方法与外部系统交互。

约定：
- 每个发布条目带 local_key（本地身份）与 content_hash；连接器应把它们写入外部元数据，
  以便"列出"时能对上号（重复导入时可认领已存在的外部条目，而不是再建一份）。
- 外部处理可以是异步的：upsert 返回 status=pending，之后用 status() 轮询。
- 不支持的能力在 capabilities() 中声明为 False，同步引擎据此降级而不是报错。
"""
from dataclasses import asdict, dataclass, field
from typing import Any, Literal, Protocol, runtime_checkable

ExternalState = Literal["pending", "processing", "completed", "failed", "missing"]


@dataclass(frozen=True)
class Capabilities:
    ingest_markdown: bool = True
    update_in_place: bool = True
    delete: bool = True
    async_ingest: bool = False
    search: bool = True
    read: bool = True
    listing: bool = True
    metadata_max_keys: int = 20
    wiki_read: bool = False
    wiki_write: bool = False
    wiki_auto: bool = False
    graph_import: bool = False     # 接收整理图（PublishItem.graph），如 LightRAG 的图模式

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class Collection:
    id: str
    name: str


@dataclass(frozen=True)
class PublishItem:
    local_key: str                 # rec:<owner>/<record> 或 doc:<owner>/<document> 或 wiki:<record>
    title: str
    markdown: str
    content_hash: str
    fixed_ref: dict                # 回指本地固定版本
    ref_uri: str                   # burrowl://owner/record@rev#sha（写入外部元数据，便于人工回溯）
    tags: tuple[str, ...] = ()
    metadata: dict[str, str] = field(default_factory=dict)
    # 交换格式（R12 T-P3.3）：渲染好的库内相对路径与 YAML 头字段。只有写 Markdown 库的连接器使用，其他连接器忽略。
    path: str | None = None
    front_matter: dict | None = None
    # 整理图（R12 T-P4.1）：本条目声明的实体与关系，只给 capabilities().graph_import 为真的连接器。
    # {"entities": [{id, name, type, description}], "relationships": [{src, dst, type, category, certainty, state}]}
    graph: dict | None = None


@dataclass(frozen=True)
class ExternalRef:
    external_id: str
    state: ExternalState = "completed"
    detail: str = ""


@dataclass(frozen=True)
class ExternalItem:
    external_id: str
    local_key: str | None
    content_hash: str | None
    title: str = ""


@dataclass(frozen=True)
class Hit:
    external_id: str
    title: str
    content: str
    score: float
    local_key: str | None = None
    locator: str = ""


@dataclass(frozen=True)
class WikiPage:
    slug: str
    title: str
    markdown: str
    page_type: str = "synthesis"
    summary: str = ""
    version: int | None = None
    edited_by: str = ""            # pipeline / agent / user（外部系统记录的最后编辑来源）
    content_hash: str = ""


@runtime_checkable
class HubConnector(Protocol):
    id: str
    type: str

    def capabilities(self) -> Capabilities: ...
    def health(self) -> dict[str, Any]: ...
    def ensure_collection(self, name: str) -> Collection: ...
    def upsert(self, collection: Collection, item: PublishItem, existing: ExternalRef | None) -> ExternalRef: ...
    def delete(self, collection: Collection, ref: ExternalRef) -> None: ...
    def status(self, refs: list[ExternalRef]) -> list[ExternalRef]: ...
    def list_items(self, collection: Collection) -> list[ExternalItem]: ...
    def search(self, collections: list[Collection], query: str, limit: int) -> list[Hit]: ...
    def read(self, ref: ExternalRef) -> dict[str, Any]: ...


@runtime_checkable
class SupportsWiki(Protocol):
    def wiki_list(self, collection: Collection, query: str = "", limit: int = 100) -> list[WikiPage]: ...
    def wiki_read(self, collection: Collection, slug: str) -> WikiPage | None: ...
    def wiki_write(self, collection: Collection, page: WikiPage) -> WikiPage: ...
    def wiki_delete(self, collection: Collection, slug: str) -> None: ...
    def wiki_configure(self, collection: Collection, *, auto: bool, synthesis_model_id: str | None) -> dict: ...


@dataclass(frozen=True)
class CandidateLink:
    """外部工具发现的关系线索（R12 T-P4.3）：两端是已发布条目的 local_key。只进入候选，从不直接写成记录。"""
    src_local_key: str
    dst_local_key: str
    kind: str = "related"          # 外部工具自己的关系名或类别
    score: float = 0.0


@runtime_checkable
class SupportsFinalize(Protocol):
    """一次发布执行完所有条目之后调用（例如按全部条目重建外部索引）。返回执行摘要。"""
    def finalize(self, collection: Collection) -> dict[str, Any]: ...


@runtime_checkable
class SupportsCandidates(Protocol):
    def candidates(self, collection: Collection, limit: int = 200) -> list[CandidateLink]: ...


class ConnectorError(Exception):
    """外部系统返回的错误（含 HTTP 状态等定位信息）；同步引擎逐项记录，不中断其他条目。"""

    def __init__(self, message: str, *, status: int | None = None, retryable: bool = False):
        super().__init__(message)
        self.status = status
        self.retryable = retryable
