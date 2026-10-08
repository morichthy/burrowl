"""中枢操作经组合根与操作注册表的完整链路：配置连接器 → 健康检查 → 预览 → 执行 → 状态 → Markdown 库落盘。"""
from burrowl.app import App
from burrowl.interfaces.ops.registry import Ctx, call

AI = {"kind": "ai", "id": "test"}


def test_hub_ops_with_markdown_vault(sample_workspace, tmp_path):
    app = App.open(sample_workspace, fake_vectors=True)
    ctx = Ctx(app=app, actor=AI, channel="test")
    current = call(ctx, "config.get", {})
    vault = tmp_path / "vault"
    vault.mkdir()
    connector = {"id": "vault", "type": "mdvault", "options": {"path": str(vault)},
                 "publish": {"review_filter": "all", "levels": ["L3", "L4"], "include_documents": True}}
    call(ctx, "config.update", {"patch": {"connectors": [connector]}, "expected_revision": current["revision"]})
    assert call(ctx, "hub.connectors", {})[0]["available"]
    assert call(ctx, "hub.health", {"connector_id": "vault"})["ok"]
    plan = call(ctx, "hub.plan", {"connector_id": "vault"})
    assert plan["counts"]["create"] > 0
    out = call(ctx, "hub.apply", {"connector_id": "vault", "plan_hash": plan["plan_hash"]})
    assert set(out["summary"]) == {"created"}
    files = list(vault.rglob("*.md"))
    assert len(files) == plan["counts"]["create"]
    assert "burrowl_ref: burrowl://" in files[0].read_text(encoding="utf-8")
    assert call(ctx, "hub.plan", {"connector_id": "vault"})["actions"] == []
    assert call(ctx, "hub.status", {"connector_id": "vault"})["counts"] == {"completed": len(files)}
