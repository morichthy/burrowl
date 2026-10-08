"""`.burrowl/config.json` 的读取与比较并交换写入。

revision 是文件内容的 SHA-256（文件不存在时为 "default"）。并发保存时，
基于旧 revision 的写入会被拒绝，而不是悄悄覆盖别人刚保存的设置。
"""
from pathlib import Path

from pydantic import ValidationError

from ..core.atomic import exclusive_lock, write_json_atomic
from ..core.config import BurrowlConfig
from ..core.errors import BurrowlError
from ..core.hashing import sha256_bytes, strict_json_loads
from .layout import Workspace

FILE = "config.json"
MAX_BYTES = 256 * 1024


def validation_errors(exc: ValidationError) -> list[dict]:
    return [{"code": "INVALID_ARGUMENT", "path": "/" + "/".join(str(p) for p in e["loc"]), "message": e["msg"]}
            for e in exc.errors()]


class ConfigStore:
    def __init__(self, workspace: Workspace):
        self.ws = workspace
        self._cache: tuple[tuple, BurrowlConfig, str] | None = None

    @property
    def path(self) -> Path:
        return self.ws.state(FILE)

    def read(self) -> tuple[BurrowlConfig, str]:
        path = self.path
        try:
            stat = path.stat()
        except FileNotFoundError:
            return BurrowlConfig(), "default"
        signature = (stat.st_mtime_ns, stat.st_size, stat.st_ino)
        if self._cache and self._cache[0] == signature:
            return self._cache[1].model_copy(deep=True), self._cache[2]
        raw = path.read_bytes()
        if len(raw) > MAX_BYTES:
            raise BurrowlError("INTEGRITY_ERROR", "配置文件过大")
        try:
            config = BurrowlConfig.model_validate(strict_json_loads(raw.decode("utf-8")))
        except (ValueError, UnicodeError) as exc:
            errors = validation_errors(exc) if isinstance(exc, ValidationError) else []
            raise BurrowlError("INTEGRITY_ERROR", "配置文件无法解析或不符合契约", {"path": str(path)}, errors) from exc
        revision = sha256_bytes(raw)
        self._cache = (signature, config, revision)
        return config.model_copy(deep=True), revision

    def write(self, config: BurrowlConfig, expected_revision: str) -> str:
        self.ws.state().mkdir(exist_ok=True)
        with exclusive_lock(self.ws.state("config.lock"), "config-update"):
            _current, revision = self.read()
            if revision != expected_revision:
                raise BurrowlError("VERSION_CONFLICT", "配置已被其他操作修改，请刷新后重试",
                               {"expected_revision": expected_revision, "current_revision": revision})
            write_json_atomic(self.path, config.model_dump(mode="json"), pretty=True)
            self._cache = None
            return self.read()[1]
