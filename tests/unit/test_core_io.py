"""路径安全、原子写、排他锁与版本化文档。"""
import json
import os
import subprocess
import sys

import pytest

from burrowl.core.atomic import _clear_dead_lock, exclusive_lock, write_json_atomic, write_new_json
from burrowl.core.errors import BurrowlError
from burrowl.core.jsonschema import check_schema, validate
from burrowl.core.pathsafe import ReadScope, safe_path
from burrowl.core.revdoc import RevisionedDoc


@pytest.mark.parametrize("bad", ["../x", "C:/x", "a/../b", "CON", "aux.txt", "a/b. ", "a:b", "", "a//b"])
def test_safe_path_rejects_unsafe_literals(tmp_path, bad):
    with pytest.raises(BurrowlError) as caught:
        safe_path(tmp_path, bad)
    assert caught.value.code == "UNSAFE_PATH"


def test_safe_path_rejects_case_conflict_and_allows_normal(tmp_path):
    (tmp_path / "Research").mkdir()
    with pytest.raises(BurrowlError):
        safe_path(tmp_path, "research/x.json")
    assert safe_path(tmp_path, "Research/x.json") == tmp_path.resolve() / "Research" / "x.json"


@pytest.mark.skipif(os.name != "nt", reason="junction 仅 Windows")
def test_safe_path_rejects_junction(tmp_path):
    target = tmp_path / "target"
    target.mkdir()
    link = tmp_path / "data" / "link"
    link.parent.mkdir()
    subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)], check=True, capture_output=True)
    for scope in (None, ReadScope()):
        with pytest.raises(BurrowlError) as caught:
            safe_path(tmp_path / "data", "link/file.json", scope)
        assert caught.value.code == "UNSAFE_PATH"


def test_write_new_refuses_overwrite_and_atomic_replace(tmp_path):
    path = tmp_path / "a.json"
    write_new_json(path, {"b": 1, "a": "中"})
    assert path.read_bytes() == '{"a":"中","b":1}'.encode()
    with pytest.raises(FileExistsError):
        write_new_json(path, {})
    write_json_atomic(path, {"x": 1}, pretty=True)
    assert json.loads(path.read_text(encoding="utf-8")) == {"x": 1}
    assert not list(tmp_path.glob("*.tmp"))


def test_lock_blocks_live_holder_and_clears_dead_holder(tmp_path):
    lock = tmp_path / "write.lock"
    with exclusive_lock(lock, "test"):
        with pytest.raises(BurrowlError) as caught, exclusive_lock(lock, "other"):
            pass
        assert caught.value.code == "LOCKED"
    assert not lock.exists()
    # 用一个已退出子进程的身份伪造残留锁：应被安全清除。
    child = subprocess.run([sys.executable, "-c",
                            "import os,json;from burrowl.core.atomic import process_identity as p;"
                            "print(json.dumps({'pid':os.getpid(),'start_identity':p(os.getpid())}))"],
                           check=True, capture_output=True, text=True)
    holder = json.loads(child.stdout)
    lock.write_text(json.dumps({**holder, "token": "x"}), encoding="utf-8")
    with exclusive_lock(lock, "after-crash") as me:
        assert me["pid"] == os.getpid()
    assert len(list((tmp_path / "lock-recovery").iterdir())) == 1  # 清理掉的锁留有回执


def test_dead_lock_cleanup_never_removes_a_newer_lock(tmp_path):
    """并发清理：我们看到的是旧持有者，但文件已被别人换成新锁——必须原样放回并报占用。"""
    lock = tmp_path / "write.lock"
    newer = {"pid": os.getpid(), "token": "newer"}
    lock.write_text(json.dumps(newer), encoding="utf-8")
    assert _clear_dead_lock(lock, {"pid": 1, "token": "old"}) is False
    assert json.loads(lock.read_text(encoding="utf-8")) == newer
    assert [p.name for p in tmp_path.iterdir()] == ["write.lock"]


def test_revisioned_doc_cas(tmp_path):
    doc = RevisionedDoc(tmp_path / "RS-1")
    assert doc.create({"n": 0}) == 1
    value, revision = doc.update(1, lambda v: {**v, "n": v["n"] + 1})
    assert (value, revision) == ({"n": 1}, 2)
    assert doc.update(None, lambda v: v) == ({"n": 1}, 2)  # 无变化不产生新修订
    with pytest.raises(BurrowlError) as caught:
        doc.update(1, lambda v: {**v, "n": 9})
    assert caught.value.code == "VERSION_CONFLICT"
    assert doc.read(1) == ({"n": 0}, 1)


def test_jsonschema_subset():
    schema = {"type": "object", "properties": {"a": {"enum": [1, "x"]}}, "required": ["a"],
              "additionalProperties": False}
    check_schema(schema)
    assert validate({"a": 1}, schema) == []
    assert validate({"a": True}, schema)  # True 不等于 1
    assert {e["path"] for e in validate({"b": 1}, schema)} == {"/a", "/b"}
    with pytest.raises(ValueError):
        check_schema({"pattern": "x"})
