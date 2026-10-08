import json
import shutil
from pathlib import Path

import pytest

FIXTURE_WORKSPACE = Path(__file__).parent / "fixtures" / "workspace"
SAMPLE_OWNER = "research/floating-point-summation"


@pytest.fixture
def sample_workspace(tmp_path: Path) -> Path:
    """可写的样例数据目录副本（含浮点求和研究的完整记忆提交链）。"""
    target = tmp_path / "data"
    shutil.copytree(FIXTURE_WORKSPACE, target)
    return target


def sample_memory_home() -> Path:
    return FIXTURE_WORKSPACE / SAMPLE_OWNER / "memory"


def load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))
