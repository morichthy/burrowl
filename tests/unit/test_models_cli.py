"""模型目录（重排模型按清单逐文件校验后复制）与 burrowl mcp-install（合并写入宿主 .mcp.json）。"""
import hashlib
import json

import pytest
from typer.testing import CliRunner

from burrowl.core.errors import BurrowlError
from burrowl.index import model_store
from burrowl.interfaces.cli import cli


def _fake_reranker(directory):
    directory.mkdir()
    files = {"model_quint8_avx2.onnx": b"onnx", "tokenizer.json": b"{}", "config.json": b"{}"}
    for name, data in files.items():
        (directory / name).write_bytes(data)
    manifest = {"schema_version": 1, "onnx_file": "model_quint8_avx2.onnx",
                "files": {n: hashlib.sha256(d).hexdigest() for n, d in files.items()}}
    (directory / "model-manifest.json").write_text(json.dumps(manifest), encoding="utf-8")


def test_reranker_copy_verifies_manifest(tmp_path):
    source, models = tmp_path / "src", tmp_path / "models"
    _fake_reranker(source)
    assert not model_store.status(models)["reranker"]["installed"]
    assert model_store.install_reranker(models, source)["installed"]
    assert (models / "cross-encoder" / "tokenizer.json").read_bytes() == b"{}"
    (source / "config.json").write_bytes(b"tampered")
    with pytest.raises(BurrowlError) as caught:
        model_store.install_reranker(tmp_path / "other", source)
    assert caught.value.code == "INTEGRITY_ERROR"


def test_default_models_dir_respects_env(tmp_path, monkeypatch):
    monkeypatch.setenv("BURROWL_MODELS_DIR", str(tmp_path / "m"))
    assert model_store.default_models_dir() == tmp_path / "m"


def test_mcp_config_prints_standard_entry(tmp_path, monkeypatch):
    monkeypatch.delenv("BURROWL_HOME", raising=False)
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    data = tmp_path / "数据 目录"
    result = CliRunner().invoke(cli, ["mcp-config", "--data-dir", str(data), "--mode", "manual"])
    assert result.exit_code == 0, result.output
    entry = json.loads(result.stdout)["mcpServers"]["burrowl"]          # 标准输出只有 JSON，可直接解析
    assert entry["args"][-5:] == ["mcp", "--data-dir", str(data.resolve()), "--mode", "manual"]
    assert entry["env"]["PYTHONUTF8"] == "1" and (data / ".burrowl").is_dir()


def test_models_dir_follows_install_root_through_current_junction(tmp_path, monkeypatch):
    """经 bin -> current 联接启动时，模型目录仍是 <根>/models，而不是联接位置往上数两级。"""
    import _winapi

    root = tmp_path / "安装 根"
    version = root / "versions" / "0.1.0"
    version.mkdir(parents=True)
    _winapi.CreateJunction(str(version), str(root / "current"))
    monkeypatch.delenv("BURROWL_MODELS_DIR", raising=False)
    for home in (version, root / "current"):
        monkeypatch.setenv("BURROWL_HOME", str(home) + "\\")
        assert model_store.default_models_dir() == (root / "models").resolve()
