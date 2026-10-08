"""WeKnora 连接器。经入口点 `burrowl.connectors: weknora` 注册。

WeKnora 每个 minor 版本都可能有破坏性变更，所以接口路径与字段集中在 api_v08.py；
新版本只需新增一个 api_vXX 模块并在 health 中按服务版本选择。
"""
from .connector import WeKnoraConnector


def create(connector_id: str, **settings) -> WeKnoraConnector:
    return WeKnoraConnector(connector_id, **settings)


__all__ = ["WeKnoraConnector", "create"]
