"""burrowl-bundle 打包/校验/恢复，以及全工作区校验。"""
import zipfile

import pytest

from burrowl.app import App
from burrowl.core.errors import BurrowlError
from burrowl.portable import bundle
from burrowl.services import health


def test_pack_verify_unpack_roundtrip(sample_workspace, tmp_path):
    app = App.open(sample_workspace, fake_vectors=True)
    app.index.reconcile()
    packed = bundle.pack(sample_workspace, tmp_path / "备份 1.zip")
    assert packed["files"] > 10
    with zipfile.ZipFile(tmp_path / "备份 1.zip") as archive:
        assert not any(n.startswith("data/.burrowl/index/") for n in archive.namelist())  # 索引可重建，不打包
    with pytest.raises(BurrowlError):
        bundle.pack(sample_workspace, tmp_path / "备份 1.zip")                       # 不覆盖
    restored = bundle.unpack(tmp_path / "备份 1.zip", tmp_path / "恢复 目录")
    assert restored["ok"]
    copy = App.open(tmp_path / "恢复 目录", fake_vectors=True)
    original = app.memory.inspect("RES-FLOATING-POINT-SUMMATION")
    again = copy.memory.inspect("RES-FLOATING-POINT-SUMMATION")
    assert again["head"] == original["head"] and again["records"].keys() == original["records"].keys()
    with pytest.raises(BurrowlError) as caught:
        bundle.unpack(tmp_path / "备份 1.zip", tmp_path / "恢复 目录")                # 目标非空
    assert caught.value.code == "CONFLICT"


def test_tampered_bundle_is_rejected(sample_workspace, tmp_path):
    bundle.pack(sample_workspace, tmp_path / "b.zip")
    with zipfile.ZipFile(tmp_path / "b.zip") as source, zipfile.ZipFile(tmp_path / "t.zip", "w") as target:
        for item in source.infolist():
            data = source.read(item)
            if item.filename.endswith("sources.json"):
                data = data.replace(b"SRC-", b"SRX-", 1)
            target.writestr(item, data)
    with pytest.raises(BurrowlError) as caught:
        bundle.verify(tmp_path / "t.zip")
    assert caught.value.code == "INTEGRITY_ERROR"
    assert not (tmp_path / "out").exists()


def test_validate_reports_changed_source(sample_workspace):
    app = App.open(sample_workspace, fake_vectors=True)
    app.index.reconcile()
    before = health.validate(app.storage, app.index)
    # 样例数据本身有 3 个在登记后被改过的 reproduce.py，校验应如实报告
    paths = {e["source_id"]: e["path"] for e in app.storage.sources.entries()}
    assert len(before["problems"]) == 3
    assert all(p["code"] == "STALE_BASIS" and paths[p["source_id"]].endswith("reproduce.py")
               for p in before["problems"])
    assert before["warnings"] and before["warnings"][0]["code"] == "RESIDUE"   # 孤立提交只是提示
    source = app.storage.sources.resolve("SRC-SUMMATION-R01")
    source.path.write_text(source.path.read_text(encoding="utf-8") + " ", encoding="utf-8")
    after = health.validate(app.storage, app.index)
    new = [p for p in after["problems"] if p not in before["problems"]]
    assert [p["source_id"] for p in new] == ["SRC-SUMMATION-R01"]
