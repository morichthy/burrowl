"""配置：默认值、合并修改、并发冲突、校验错误与后续动作提示。"""
import pytest

from burrowl.core.errors import BurrowlError
from burrowl.services.config import ConfigService
from burrowl.storage.config_store import ConfigStore
from burrowl.storage.layout import Workspace


@pytest.fixture
def config(tmp_path):
    return ConfigService(ConfigStore(Workspace.open(tmp_path, create=True)))


def test_defaults_and_patch(config):
    current = config.get()
    assert current["revision"] == "default"
    assert current["config"]["reading"]["context"]["max_owners"] == 10
    assert current["config"]["index"]["vector"]["kind"] == "sqlite-vec"
    preview = config.update({"index": {"path": "D:/索引/index.sqlite3"}}, expected_revision="default", dry_run=True)
    assert preview["effects"][0]["action"] == "index.rebuild"
    assert config.get()["revision"] == "default"  # 预览不写
    saved = config.update({"reading": {"strategy": "quick"}, "connectors": [
        {"id": "kb", "type": "weknora", "base_url": "http://localhost:8080", "secret_ref": "env:WK_KEY"}]},
        expected_revision="default")
    assert saved["config"]["reading"]["strategy"] == "quick"
    assert saved["config"]["connectors"][0]["publish"]["review_filter"] == "reviewed_only"
    assert config.get()["secrets"]["kb"] == {"secret_ref": "env:WK_KEY", "set": False}
    # null 恢复默认
    reset = config.update({"reading": {"strategy": None}}, expected_revision=saved["revision"])
    assert reset["config"]["reading"]["strategy"] == "standard"


def test_conflict_and_validation(config):
    first = config.update({"query": {"result_limit": 30}}, expected_revision="default")
    with pytest.raises(BurrowlError) as caught:
        config.update({"query": {"result_limit": 40}}, expected_revision="default")
    assert caught.value.code == "VERSION_CONFLICT"
    with pytest.raises(BurrowlError) as caught:
        config.update({"reading": {"screening": {"regular_windows": 4, "max_windows": 2}}},
                      expected_revision=first["revision"])
    assert caught.value.code == "INVALID_ARGUMENT" and caught.value.errors
    with pytest.raises(BurrowlError):
        config.update({"unknown": 1}, expected_revision=first["revision"])


def test_mcp_error_envelope_is_default_off_and_hot_switchable(config):
    assert config.get()["config"]["mcp"]["error_envelope"] is False
    saved = config.update({"mcp": {"error_envelope": True}}, expected_revision="default")
    assert saved["effects"] == []  # 返回格式即时读取；不能误报必须重启。
    assert config.current().mcp.error_envelope is True
    preview = config.update({"mcp": {"toolsets": ["memory"]}}, expected_revision=saved["revision"], dry_run=True)
    assert preview["effects"][0]["action"] == "mcp.restart"
    assert config.current().mcp.toolsets != ["memory"]
