"""数据目录布局。代码仓库与数据目录完全分离，数据目录路径由用户配置。

    <data>/research/ projects/ core-algorithms/ knowledge/ runs/ reports/ data/ tools/   内容树
    <data>/.burrowl/                                                                      程序状态
"""
from dataclasses import dataclass
from pathlib import Path

from ..core.errors import BurrowlError
from ..core.pathsafe import safe_path

# 内容树根目录 → 默认对象类型
CONTENT_ROOTS = {"research": "research", "projects": "project", "core-algorithms": "core-algorithm",
                 "knowledge": "knowledge", "runs": "run", "reports": "report", "data": "data", "tools": "tool"}
STATE_DIR = ".burrowl"


@dataclass(frozen=True)
class Workspace:
    root: Path

    @classmethod
    def open(cls, root: str | Path, *, create: bool = False) -> "Workspace":
        path = Path(root).resolve()
        if create:
            path.mkdir(parents=True, exist_ok=True)
            (path / STATE_DIR).mkdir(exist_ok=True)
        if not path.is_dir():
            raise BurrowlError("NOT_FOUND", "数据目录不存在", {"path": str(path)})
        return cls(path)

    def path(self, relative: str) -> Path:
        """数据目录内安全路径（拒绝越界、链接、保留名）。"""
        return safe_path(self.root, relative)

    def state(self, relative: str = "") -> Path:
        """`.burrowl/` 下的程序状态路径。"""
        return safe_path(self.root, f"{STATE_DIR}/{relative}" if relative else STATE_DIR)

    def relative(self, path: Path) -> str:
        return path.resolve().relative_to(self.root).as_posix()
