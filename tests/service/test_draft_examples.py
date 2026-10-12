"""memory.contract 返回给 AI 的草案示例必须能通过真实校验（示例与契约不同步时这里失败）。"""
import json
import uuid
from importlib.resources import files

import pytest

from burrowl.core.schema import current_versions
from tests.service.helpers import AI, services

OWNER = "RES-FLOATING-POINT-SUMMATION"  # 示例草案所在的样例对象（tools/gen_draft_examples.py）

EXAMPLES = files("burrowl.core.schema") / "examples"
KINDS = sorted(p.name.removesuffix(".json") for p in EXAMPLES.iterdir() if p.name.endswith(".json"))


def test_every_writable_kind_has_example():
    internal = {"review", "representation", "policy", "consolidation", "feedback"}  # 由专用操作生成
    assert set(KINDS) == set(current_versions()) - internal


@pytest.mark.parametrize("kind", KINDS)
def test_example_validates(sample_workspace, kind):
    _storage, memory, _review = services(sample_workspace)
    example = json.loads((EXAMPLES / f"{kind}.json").read_text(encoding="utf-8"))
    # R20 D-01：示例不写 owner_id，照抄到别的对象也不会触发归属不一致
    assert "owner_id" not in example["operation"]["draft"]
    head = memory.inspect(OWNER)["head"]["commit_id"]
    request = {"schema_version": 3, "request_id": str(uuid.uuid4()), "actor": AI,
               "owner_id": OWNER, "expected_head": head,
               "operations": [example["operation"]], "dry_run": True}
    result = memory.validate_draft(request)
    assert result["valid"], result
