"""全系统唯一的业务错误类型。CLI、HTTP 与 MCP 都由它映射，不再各有一套格式。"""
from dataclasses import dataclass, field
from typing import Any

# code → (HTTP 状态, CLI 退出码, 是否可重试)
CODES: dict[str, tuple[int, int, bool]] = {
    "INVALID_ARGUMENT": (400, 2, False),
    "INVALID_SCHEMA": (400, 2, False),
    "UNRESOLVED_REFERENCE": (400, 2, False),
    "UNRESOLVED_LINK": (400, 2, False),
    "NOT_FOUND": (404, 2, False),
    "ACCESS_DENIED": (403, 4, False),
    "PERMISSION_DENIED": (403, 4, False),
    "UNSAFE_PATH": (403, 4, False),
    "APPROVAL_REQUIRED": (403, 4, False),
    "VERSION_CONFLICT": (409, 5, True),
    "STALE_BASIS": (409, 5, True),
    "IDEMPOTENCY_CONFLICT": (409, 5, False),
    "INVALID_TRANSITION": (409, 9, False),
    "EVIDENCE_INELIGIBLE": (422, 9, False),
    "BUDGET_EXCEEDED": (422, 9, False),
    "LOCKED": (423, 6, True),
    "INDEX_PENDING": (202, 3, True),
    "CAPABILITY_UNAVAILABLE": (503, 10, True),
    "INTEGRITY_ERROR": (500, 7, False),
    "STORAGE_ERROR": (500, 8, True),
}


@dataclass
class BurrowlError(Exception):
    """业务拒绝。details 只放定位信息，不放未获准来源的正文。"""

    code: str
    message: str
    details: dict[str, Any] = field(default_factory=dict)
    errors: list[dict[str, Any]] = field(default_factory=list)

    def __post_init__(self):
        super().__init__(self.message)

    @property
    def http_status(self) -> int:
        return CODES.get(self.code, (500, 8, False))[0]

    @property
    def exit_code(self) -> int:
        return CODES.get(self.code, (500, 8, False))[1]

    @property
    def retryable(self) -> bool:
        return CODES.get(self.code, (500, 8, False))[2]

    def problem(self) -> dict[str, Any]:
        """对外统一结构：HTTP 响应体、MCP isError 内容和 CLI JSON 输出都用它。"""
        return {"code": self.code, "message": self.message, "details": self.details,
                "errors": self.errors, "retryable": self.retryable}


def schema_error(path: str, message: str, code: str = "INVALID_SCHEMA") -> dict[str, str]:
    """单条字段错误，path 使用 JSON Pointer。"""
    return {"code": code, "path": path, "message": message}
