"""配置的查看与修改（MCP 工具与工作台设置页共用）。

修改采用 JSON Merge Patch：只写要改的字段，null 表示恢复该字段的默认值。
返回 effects 说明这次修改需要的后续动作（如重建向量索引）；
耗时动作从不在保存配置时自动执行，由调用者确认后单独发起。
"""
from copy import deepcopy
from typing import Any

from pydantic import ValidationError

from ..core.config import BurrowlConfig
from ..core.errors import BurrowlError
from ..storage import secrets
from ..storage.config_store import ConfigStore, validation_errors


def merge_patch(target: Any, patch: Any) -> Any:
    """RFC 7386。列表整体替换。"""
    if not isinstance(patch, dict):
        return deepcopy(patch)
    result = deepcopy(target) if isinstance(target, dict) else {}
    for key, value in patch.items():
        if value is None:
            result.pop(key, None)
        else:
            result[key] = merge_patch(result.get(key), value)
    return result


def effects(before: BurrowlConfig, after: BurrowlConfig) -> list[dict[str, str]]:
    out = []
    if before.index.path != after.index.path:
        out.append({"action": "index.rebuild", "reason": "索引位置改变；新位置需要重建（旧文件可手动删除）"})
    if before.index.embedding != after.index.embedding:
        out.append({"action": "index.rebuild", "reason": "嵌入模型改变；向量按模型指纹隔离，需要重建向量投影"})
    if before.index.vector != after.index.vector:
        out.append({"action": "index.rebuild", "reason": "向量后端改变；需要在新后端重建向量投影"})
    if (before.mcp.toolsets, before.mcp.mode) != (after.mcp.toolsets, after.mcp.mode):
        out.append({"action": "mcp.restart", "reason": "MCP 工具组或运行模式改变；宿主重启 MCP 服务（或新开对话）后生效"})
    before_hubs = {c.id: c for c in before.connectors}
    for connector in after.connectors:
        old = before_hubs.get(connector.id)
        if old is None or (old.base_url, old.tenant_id, old.secret_ref) != (
                connector.base_url, connector.tenant_id, connector.secret_ref):
            out.append({"action": "hub.test_connection", "reason": f"连接器 {connector.id} 的连接参数改变"})
        if old is not None and old.publish != connector.publish:
            out.append({"action": "hub.plan", "reason": f"连接器 {connector.id} 的发布策略改变；预览同步变化"})
    return out


class ConfigService:
    def __init__(self, store: ConfigStore):
        self.store = store

    def get(self) -> dict[str, Any]:
        config, revision = self.store.read()
        return {"config": config.model_dump(mode="json"), "revision": revision,
                "secrets": {c.id: secrets.status(c.secret_ref) for c in config.connectors}}

    @staticmethod
    def schema() -> dict[str, Any]:
        return BurrowlConfig.model_json_schema()

    def current(self) -> BurrowlConfig:
        return self.store.read()[0]

    def update(self, patch: dict, *, expected_revision: str, dry_run: bool = False) -> dict[str, Any]:
        if not isinstance(patch, dict):
            raise BurrowlError("INVALID_ARGUMENT", "patch 必须是对象")
        before, revision = self.store.read()
        if expected_revision != revision:
            raise BurrowlError("VERSION_CONFLICT", "配置已被其他操作修改，请刷新后重试",
                           {"current_revision": revision})
        merged = merge_patch(before.model_dump(mode="json"), patch)
        try:
            after = BurrowlConfig.model_validate(merged)
        except ValidationError as exc:
            raise BurrowlError("INVALID_ARGUMENT", "配置不符合契约", errors=validation_errors(exc)) from exc
        changes = effects(before, after)
        if dry_run:
            return {"dry_run": True, "config": after.model_dump(mode="json"), "revision": revision, "effects": changes}
        new_revision = self.store.write(after, revision)
        return {"dry_run": False, "config": after.model_dump(mode="json"), "revision": new_revision,
                "effects": changes}
