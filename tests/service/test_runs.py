"""Run：创建、登记、执行（含失败）、回滚。"""
import pytest

from burrowl.core.errors import BurrowlError
from burrowl.services.runs import RunService
from tests.service.helpers import services


@pytest.fixture
def runs(tmp_path):
    storage, _memory, _review = services(tmp_path)
    storage.owners.create("research", "order", "顺序")
    return storage, RunService(storage)


def test_create_register_rollback(runs):
    storage, service = runs
    created = service.create("求和实验", owner_id="RES-ORDER", question="顺序影响误差吗")
    assert created["path"].startswith("research/order/runs/run-")
    data = storage.ws.root / "research/order/data.txt"
    data.write_text("1 2 3", encoding="utf-8")
    assert service.register(created["run_id"], inputs=["research/order/data.txt"], preview=True)["writes"] == 0
    done = service.register(created["run_id"], inputs=["research/order/data.txt"])
    assert done["status"] == "updated"
    data.write_text("changed", encoding="utf-8")
    with pytest.raises(BurrowlError) as caught:
        service.register(created["run_id"], inputs=["research/order/data.txt"])
    assert caught.value.code == "STALE_BASIS"
    assert service.rollback(done["receipt"])["status"] == "restored"
    assert service.search(owner_id="RES-ORDER")[0]["run_id"] == created["run_id"]


def test_execute_success_and_failure(runs):
    storage, service = runs
    run_id = service.create("执行")["run_id"]
    script = storage.ws.root / "runs/work.py"
    script.write_text("import sys,pathlib\npathlib.Path(sys.argv[1],'out.txt').write_text('ok')\n"
                      "sys.exit(int(sys.argv[2]))\n", encoding="utf-8")
    ok = service.execute(run_id, script="runs/work.py", args=["{output_dir}", "0"])
    assert ok["execution_status"] == "succeeded"
    card = storage.owners.scan().get(run_id).native
    assert any(a["path"].endswith("out.txt") for a in card["artifacts"]) and card["status"] == "succeeded"
    bad = service.execute(run_id, script="runs/work.py", args=["{output_dir}", "3"])
    assert bad["execution_status"] == "failed" and bad["exit_code"] == 3
