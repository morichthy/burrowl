"""工作目录（工作根）与带外部原件的迁移：登记 → 搬家 → 打包 → 恢复时自动重定向。"""
import shutil

import pytest

from burrowl.app import App
from burrowl.core.errors import BurrowlError
from burrowl.interfaces.ops.registry import Ctx, call
from burrowl.portable import bundle


@pytest.fixture
def setup(tmp_path):
    """一个数据目录 + 一个工作目录（含中文与空格），工作目录里有原始数据和代码。"""
    work = tmp_path / "工作 目录" / "proj"
    (work / "data").mkdir(parents=True)
    (work / "data" / "实验.csv").write_text("x,y\n1,2\n", encoding="utf-8")
    (work / "run.py").write_text("print('ok')\n", encoding="utf-8")
    elsewhere = tmp_path / "其他" / "说明.txt"
    elsewhere.parent.mkdir()
    elsewhere.write_text("外部说明", encoding="utf-8")
    app = App.open(tmp_path / "data", create=True, fake_vectors=True)
    app.work_dir = work
    ctx = Ctx(app=app, actor={"kind": "ai", "id": "t"}, channel="mcp")
    return app, ctx, work, elsewhere


def test_register_under_work_dir_creates_root(setup):
    app, ctx, work, elsewhere = setup
    entry = call(ctx, "sources.register", {"source_id": "SRC-DATA", "path": str(work / "data" / "实验.csv")})
    assert entry["root"].startswith("ROOT-PROJ") and entry["path"] == "data/实验.csv"
    again = call(ctx, "sources.register", {"source_id": "SRC-CODE", "path": str(work / "run.py")})
    assert again["root"] == entry["root"]                                  # 同一工作目录只建一个根
    outside = call(ctx, "sources.register", {"source_id": "SRC-NOTE", "path": str(elsewhere)})
    assert "root" not in outside and outside["path"] == elsewhere.as_posix()  # 工作目录外：绝对路径
    assert app.storage.sources.resolve("SRC-DATA").path == work / "data" / "实验.csv"
    # 数据目录内的绝对路径改写为相对路径（按对象检查访问权限）
    inner = app.root / "research" / "x.txt"
    inner.parent.mkdir(parents=True)
    inner.write_text("内", encoding="utf-8")
    assert call(ctx, "sources.register", {"source_id": "SRC-IN", "path": str(inner)})["path"] == "research/x.txt"
    listed = {e["source_id"]: e["location"] for e in call(ctx, "sources.list", {})}
    assert listed["SRC-DATA"].endswith("实验.csv")


def test_relocate_root_checks_fingerprints(setup, tmp_path):
    app, ctx, work, _ = setup
    root = call(ctx, "sources.register", {"source_id": "SRC-DATA", "path": str(work / "data" / "实验.csv")})["root"]
    moved = tmp_path / "新位置" / "proj"
    shutil.copytree(work, moved)
    shutil.rmtree(work)
    with pytest.raises(BurrowlError):
        app.storage.sources.resolve("SRC-DATA").path.read_bytes()
    result = call(ctx, "sources.relocate_root", {"root_id": root, "new_path": str(moved)})
    assert result["verified"] == 1 and result["relocated_from"]
    assert app.storage.sources.resolve("SRC-DATA").path == moved / "data" / "实验.csv"
    other = tmp_path / "另一份"
    shutil.copytree(moved, other)
    (other / "data" / "实验.csv").write_text("被改过", encoding="utf-8")
    with pytest.raises(BurrowlError) as caught:                                # 内容不同：不是搬家
        call(ctx, "sources.relocate_root", {"root_id": root, "new_path": str(other)})
    assert caught.value.code == "STALE_BASIS"


def test_bundle_carries_external_files_and_redirects(setup, tmp_path):
    app, ctx, work, elsewhere = setup
    root = call(ctx, "sources.register", {"source_id": "SRC-DATA", "path": str(work / "data" / "实验.csv")})["root"]
    call(ctx, "sources.register", {"source_id": "SRC-CODE", "path": str(work / "run.py")})
    call(ctx, "sources.register", {"source_id": "SRC-NOTE", "path": str(elsewhere)})
    packed = bundle.pack(app.root, tmp_path / "迁移.zip")
    assert packed["external_packed"] == 3 and not packed["external_skipped"]

    # 模拟换机器：原工作目录与外部文件都不在了
    shutil.rmtree(work)
    elsewhere.unlink()
    new_proj = tmp_path / "新机器" / "proj"
    restored = bundle.unpack(tmp_path / "迁移.zip", tmp_path / "新数据",
                             mapping={root: str(new_proj), str(elsewhere.parent): str(tmp_path / "新机器" / "资料")})
    assert restored["external_written"] == 3 and not restored["external_unresolved"]
    copy = App.open(tmp_path / "新数据", fake_vectors=True)
    assert copy.storage.sources.resolve("SRC-DATA").path == new_proj / "data" / "实验.csv"
    assert copy.storage.sources.resolve("SRC-NOTE").path.read_text(encoding="utf-8") == "外部说明"
    roots = {r["root_id"]: r for r in copy.storage.sources.roots()}
    assert roots[root]["relocated_from"] == [work.as_posix()]


def test_restore_defaults_keep_original_or_use_sibling_dir(setup, tmp_path):
    app, ctx, work, _ = setup
    call(ctx, "sources.register", {"source_id": "SRC-DATA", "path": str(work / "data" / "实验.csv")})
    bundle.pack(app.root, tmp_path / "b.zip")
    same_machine = bundle.unpack(tmp_path / "b.zip", tmp_path / "副本")      # 原位置仍在：不动、不复制
    assert same_machine["roots"][0]["action"] == "kept" and same_machine["external_written"] == 0
    shutil.rmtree(work)
    moved = bundle.unpack(tmp_path / "b.zip", tmp_path / "副本2")             # 原位置不在：放到并列目录
    assert moved["roots"][0]["action"] == "default"
    copy = App.open(tmp_path / "副本2", fake_vectors=True)
    path = copy.storage.sources.resolve("SRC-DATA").path
    assert path.is_relative_to(tmp_path / "副本2-work")


def test_restore_refuses_conflicting_destination(setup, tmp_path):
    app, ctx, work, _ = setup
    root = call(ctx, "sources.register", {"source_id": "SRC-DATA", "path": str(work / "data" / "实验.csv")})["root"]
    bundle.pack(app.root, tmp_path / "b.zip")
    clash = tmp_path / "冲突" / "data"
    clash.mkdir(parents=True)
    (clash / "实验.csv").write_text("别的内容", encoding="utf-8")
    with pytest.raises(BurrowlError) as caught:
        bundle.unpack(tmp_path / "b.zip", tmp_path / "新", mapping={root: str(tmp_path / "冲突")})
    assert caught.value.code == "CONFLICT" and not (tmp_path / "新").exists()  # 什么都没写


def test_changed_external_file_is_not_packed(setup, tmp_path):
    app, ctx, work, _ = setup
    call(ctx, "sources.register", {"source_id": "SRC-DATA", "path": str(work / "data" / "实验.csv")})
    (work / "data" / "实验.csv").write_text("登记后被改", encoding="utf-8")
    packed = bundle.pack(app.root, tmp_path / "b.zip")
    assert packed["external_packed"] == 0 and packed["external_skipped"][0]["source_id"] == "SRC-DATA"
    assert bundle.verify(tmp_path / "b.zip")["ok"]


