"""用户维护的术语词库 `.burrowl/query-terms.json`：中英术语、别名与单跳关联词。"""
from ..core.errors import BurrowlError
from ..core.hashing import sha256_bytes, strict_json_loads
from .layout import Workspace

FILE = "query-terms.json"
MAX_BYTES = 1024 * 1024


def load(workspace: Workspace) -> tuple[dict | None, str | None]:
    """返回 (词库内容, 字节指纹)；文件不存在时返回 (None, None)。"""
    path = workspace.state(FILE)
    if not path.exists():
        return None, None
    raw = path.read_bytes()
    if len(raw) > MAX_BYTES:
        raise BurrowlError("INVALID_ARGUMENT", "术语词库超过 1 MiB 上限")
    try:
        return strict_json_loads(raw.decode("utf-8-sig")), sha256_bytes(raw)
    except (ValueError, UnicodeError) as exc:
        raise BurrowlError("INVALID_ARGUMENT", "术语词库不是合法 JSON") from exc
