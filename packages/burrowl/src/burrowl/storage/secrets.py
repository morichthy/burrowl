"""密钥引用解析。配置文件里只存引用，从不存明文：

    env:WEKNORA_API_KEY        读环境变量
    keyring:burrowl/weknora     读 Windows 凭据管理器（经 keyring 库）

写入只允许从 CLI 或工作台的本机密码框进行；MCP 不提供写密钥的工具，以免密钥进入模型上下文。
"""
import os

from ..core.errors import BurrowlError

SERVICE = "burrowl"


def _parse(ref: str) -> tuple[str, str]:
    scheme, _, name = (ref or "").partition(":")
    if scheme not in {"env", "keyring"} or not name:
        raise BurrowlError("INVALID_ARGUMENT", "密钥引用格式应为 env:VAR 或 keyring:burrowl/<名称>")
    if scheme == "keyring":
        service, _, name = name.partition("/")
        if service != SERVICE or not name:
            raise BurrowlError("INVALID_ARGUMENT", "keyring 引用格式应为 keyring:burrowl/<名称>")
    return scheme, name


def _keyring():
    try:
        import keyring  # noqa: PLC0415 - 可选依赖，仅在使用 keyring 引用时加载
    except ImportError as exc:
        raise BurrowlError("CAPABILITY_UNAVAILABLE", "未安装 keyring，无法使用凭据管理器") from exc
    return keyring


def resolve(ref: str | None) -> str | None:
    if not ref:
        return None
    scheme, name = _parse(ref)
    value = os.environ.get(name) if scheme == "env" else _keyring().get_password(SERVICE, name)
    return value or None


def status(ref: str | None) -> dict:
    """只报告是否已设置，不返回任何密钥内容。"""
    if not ref:
        return {"secret_ref": None, "set": False}
    try:
        return {"secret_ref": ref, "set": resolve(ref) is not None}
    except BurrowlError as exc:
        return {"secret_ref": ref, "set": False, "error": exc.code}


def store(ref: str, value: str) -> None:
    scheme, name = _parse(ref)
    if scheme != "keyring":
        raise BurrowlError("INVALID_ARGUMENT", "环境变量引用由系统环境管理，不能在此写入")
    if not value:
        raise BurrowlError("INVALID_ARGUMENT", "密钥不能为空")
    _keyring().set_password(SERVICE, name, value)
