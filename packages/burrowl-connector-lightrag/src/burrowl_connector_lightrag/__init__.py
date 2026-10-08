"""burrowl 知识中枢连接器：LightRAG 图模式（导入整理图，按需重建，嵌入缓存）。见 connector.py。"""
from .connector import LightRagConnector


def create(connector_id: str, **settings) -> LightRagConnector:
    return LightRagConnector(connector_id, **settings)


__all__ = ["LightRagConnector", "create"]
