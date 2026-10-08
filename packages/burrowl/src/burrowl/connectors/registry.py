"""连接器工厂登记：内置 fake / mdvault，外部包经入口点 `burrowl.connectors` 提供（如 weknora）。

工厂签名：factory(connector_id, *, base_url, api_key, tenant_id, options) -> HubConnector
"""
from collections.abc import Callable
from importlib.metadata import entry_points
from typing import Any

from ..core.errors import BurrowlError
from .fake import FakeHub
from .mdvault import MarkdownVault

Factory = Callable[..., Any]
_BUILTIN: dict[str, Factory] = {
    "fake": lambda connector_id, **kw: FakeHub(connector_id, **kw),
    "mdvault": lambda connector_id, **kw: MarkdownVault(connector_id, **kw),
}


def factories() -> dict[str, Factory]:
    found = dict(_BUILTIN)
    for entry in entry_points(group="burrowl.connectors"):
        found.setdefault(entry.name, entry.load())
    return found


def create(connector_type: str, connector_id: str, **settings: Any):
    factory = factories().get(connector_type)
    if factory is None:
        raise BurrowlError("CAPABILITY_UNAVAILABLE", "未安装该类型的连接器", {"type": connector_type,
                       "available": sorted(factories())})
    return factory(connector_id, **settings)
