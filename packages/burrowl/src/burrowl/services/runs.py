"""Run：一次实际分析/执行的输入、程序、输出与失败记录（L0 原始依据）。

    <owner 目录>/runs/<run-id>/run.json      业务卡（输入/产物指纹、状态、结论）
    <owner 目录>/runs/<run-id>/README.md
    <run>/.run-captures/<id>/                每次登记或执行的回执、输出与日志

登记只固定字节与出处，不代表科学结论正确；结论写成记忆记录并单独复核。
执行使用当前 Python 前台运行，不是操作系统级安全沙箱。
"""
import base64
import json
import os
import platform
import subprocess
import sys
import uuid
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any

from ..core.atomic import exclusive_lock, read_json, write_json_atomic, write_new
from ..core.clock import utc_now
from ..core.errors import BurrowlError
from ..core.hashing import sha256_bytes, sha256_file
from ..core.pathsafe import safe_path
from ..storage.owners import Owner
from .memory.context import Storage

STATUSES = ("planned", "running", "succeeded", "failed", "abandoned")


def _json_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, indent=2).encode("utf-8") + b"\n"


class RunService:
    def __init__(self, storage: Storage):
        self.storage = storage

    # ---------- 读取 ----------
    def _run(self, run_id: str) -> Owner:
        owner = self.storage.owners.scan().get(run_id)
        if owner.owner_type != "run":
            raise BurrowlError("INVALID_ARGUMENT", "目标不是 Run", {"owner_id": run_id})
        return owner.require_readable()

    def search(self, *, query: str = "", owner_id: str | None = None, limit: int = 50) -> list[dict]:
        needle = query.casefold()
        items = []
        for owner in self.storage.owners.scan().readable("run"):
            card = owner.native
            text = " ".join([card.get("title", ""), card.get("question", ""), *card.get("keywords", [])]).casefold()
            if (owner_id and card.get("owner_id") != owner_id) or (needle and needle not in text):
                continue
            items.append({"run_id": owner.owner_id, "title": card.get("title"), "status": card.get("status"),
                          "owner_id": card.get("owner_id"), "created_at": card.get("created_at"),
                          "path": owner.native_path})
        return sorted(items, key=lambda r: r["created_at"] or "", reverse=True)[:limit]

    # ---------- 创建与更新 ----------
    def create(self, title: str, *, owner_id: str | None = None, question: str = "",
               keywords: list[str] | None = None, run_type: str = "analysis") -> dict:
        if not title.strip():
            raise BurrowlError("INVALID_ARGUMENT", "Run 标题不能为空")
        moment = datetime.now(UTC)
        run_id = f"RUN-{moment.strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:12].upper()}"
        sensitivity = "internal"
        if owner_id:
            parent = self.storage.owners.scan().get(owner_id).require_readable()
            base = PurePosixPath(parent.native_path).parent / "runs"
            sensitivity = parent.sensitivity
        else:
            base = PurePosixPath("runs")
        folder = self.storage.ws.path(f"{base}/{run_id.lower()}")
        card = {"schema_version": 1, "run_id": run_id, "owner_id": owner_id, "title": title.strip(),
                "question": question, "keywords": list(dict.fromkeys(k.strip() for k in keywords or [] if k.strip())),
                "run_type": run_type, "status": "planned", "sensitivity": sensitivity,
                "created_at": moment.replace(microsecond=0).isoformat().replace("+00:00", "Z"),
                "started_at": None, "ended_at": None, "inputs": [], "artifacts": [], "parameters": {},
                "environment": {"python": platform.python_version(), "platform": platform.platform()},
                "conclusion": "", "limitations": [], "parent_run_ids": []}
        try:
            folder.mkdir(parents=True)
        except FileExistsError as exc:
            raise BurrowlError("VERSION_CONFLICT", "Run 目录已存在，请重试", {"path": str(folder)}) from exc
        write_new(folder / "run.json", _json_bytes(card))
        write_new(folder / "README.md", f"# {run_id}：{title}\n\n## 问题\n\n{question}\n\n## 方法\n\n## 结果\n\n"
                  f"## 结论与限制\n".encode())
        return {"run_id": run_id, "path": self.storage.ws.relative(folder / "run.json")}

    def update(self, run_id: str, fields: dict, *, expected_sha256: str | None = None) -> dict:
        """修改结论、限制、问题、参数或状态（不改输入/产物登记）。"""
        allowed = {"title", "question", "conclusion", "limitations", "parameters", "status", "keywords",
                   "parent_run_ids"}
        if set(fields) - allowed:
            raise BurrowlError("INVALID_ARGUMENT", "只能修改：" + "、".join(sorted(allowed)))
        if "status" in fields and fields["status"] not in STATUSES:
            raise BurrowlError("INVALID_ARGUMENT", "未知 Run 状态", {"status": fields["status"]})
        return self._mutate(run_id, lambda card: {**card, **deepcopy(fields)}, expected_sha256=expected_sha256)

    # ---------- 材料登记 ----------
    def _entry(self, reference: str, role: str) -> dict:
        """只读取明确指定的文件：数据目录内相对路径，或已登记来源 ID（SRC-…）。"""
        if not isinstance(reference, str) or not reference.strip():
            raise BurrowlError("INVALID_ARGUMENT", "材料路径必须是非空字符串")
        if reference.startswith("SRC-"):
            source = self.storage.sources.resolve(reference)
            path, label = source.path, reference
        else:
            path = self.storage.ws.path(reference.replace("\\", "/"))
            label = self.storage.ws.relative(path)
            self.storage.owners.scan().require_path_readable(label)  # 受限对象的材料不能登记进 Run
        if not path.is_file():
            raise BurrowlError("NOT_FOUND", "登记需要实际文件", {"path": reference})
        return {"path": label, "sha256": sha256_file(path), "role": role, "registered_at": utc_now()}

    @staticmethod
    def _merge(card: dict, entries: dict[str, list[dict]]) -> dict:
        result = deepcopy(card)
        for group, items in entries.items():
            current = result.setdefault(group, [])
            for item in items:
                old = [x for x in current if x.get("path") == item["path"]]
                if any(x["sha256"] != item["sha256"] for x in old):
                    raise BurrowlError("STALE_BASIS", "已登记文件内容变化；新版本请用新路径或新 Run",
                                   {"path": item["path"]})
                if not old:
                    current.append(item)
        return result

    def register(self, run_id: str, *, inputs: list[str] = (), artifacts: list[str] = (),
                 preview: bool = False) -> dict:
        if not inputs and not artifacts:
            raise BurrowlError("INVALID_ARGUMENT", "至少指定一个输入或产物文件")
        owner = self._run(run_id)
        entries = {"inputs": [self._entry(p, "input") for p in inputs],
                   "artifacts": [self._entry(p, "artifact") for p in artifacts]}
        if any(e["path"] == owner.native_path for group in entries.values() for e in group):
            raise BurrowlError("INVALID_ARGUMENT", "不能把 run.json 自身登记为材料")
        if preview:
            return {"status": "preview", "entries": entries, "writes": 0}
        return self._mutate(run_id, lambda card: self._merge(card, entries))

    def _mutate(self, run_id: str, change, *, expected_sha256: str | None = None,
                capture: Path | None = None) -> dict:
        """单次 CAS 修改 run.json，并在修改前写可恢复回执。"""
        owner = self._run(run_id)
        path = self.storage.ws.path(owner.native_path)
        with exclusive_lock(path.with_name(".run.lock"), f"run:{run_id}"):
            before = path.read_bytes()
            if expected_sha256 and sha256_bytes(before) != expected_sha256:
                raise BurrowlError("VERSION_CONFLICT", "Run 已被修改，请刷新后重试")
            card = json.loads(before.decode("utf-8-sig"))
            after = change(card)
            if after == card:
                return {"status": "unchanged", "run_id": run_id, "writes": 0}
            folder = capture or path.parent / ".run-captures" / uuid.uuid4().hex
            folder.mkdir(parents=True, exist_ok=True)
            write_new(folder / "registration.json", _json_bytes({
                "schema_version": 1, "run_path": owner.native_path, "before_bytes": base64.b64encode(before).decode(),
                "after": after, "created_at": utc_now()}))
            write_json_atomic(path, after, pretty=True)
        return {"status": "updated", "run_id": run_id,
                "receipt": self.storage.ws.relative(folder / "registration.json"),
                "sha256": sha256_file(path)}

    def rollback(self, receipt_path: str, *, preview: bool = False) -> dict:
        """按回执恢复 run.json 到登记前；若之后又有新修改则拒绝覆盖。不删除任何文件。"""
        receipt_file = self.storage.ws.path(receipt_path)
        receipt = read_json(receipt_file)
        owner = self._run(str(receipt.get("after", {}).get("run_id")))  # Run 必须存在且可读
        if receipt.get("run_path") != owner.native_path:
            raise BurrowlError("INVALID_ARGUMENT", "回执与 Run 不对应")
        path = self.storage.ws.path(owner.native_path)
        if not receipt_file.is_relative_to(path.parent / ".run-captures"):
            raise BurrowlError("INVALID_ARGUMENT", "回执不属于该 Run")
        original = base64.b64decode(receipt["before_bytes"], validate=True)

        def check() -> str:
            current = json.loads(path.read_bytes().decode("utf-8-sig"))
            if current == json.loads(original.decode("utf-8-sig")):
                return "already_restored"
            if current != receipt["after"]:
                raise BurrowlError("VERSION_CONFLICT", "登记后 Run 又有新修改，拒绝覆盖")
            return "restorable"
        if preview or check() == "already_restored":
            status = check()
            return {"status": "preview" if preview and status == "restorable" else status, "writes": 0}
        with exclusive_lock(path.with_name(".run.lock"), "run-rollback"):
            if check() == "already_restored":  # 锁内重新核对，防止检查与写入之间被修改
                return {"status": "already_restored", "writes": 0}
            temporary = path.with_name(f".run.{uuid.uuid4().hex}.tmp")
            write_new(temporary, original)
            os.replace(temporary, path)
        return {"status": "restored", "files_deleted": 0}

    # ---------- 执行 ----------
    def execute(self, run_id: str, *, script: str, inputs: list[str] = (), args: list[str] = (),
                timeout_seconds: float = 3600, preview: bool = False) -> dict:
        """前台运行 Python 脚本：固定脚本与输入指纹，捕获本次输出目录、stdout/stderr。

        参数中可用 {input0}、{input1}… 与 {output_dir} 占位；不经过 shell。失败也完整登记日志。
        """
        if not 0 < timeout_seconds <= 86400:
            raise BurrowlError("INVALID_ARGUMENT", "超时范围为 (0, 86400] 秒")
        owner = self._run(run_id)
        pinned = [self._entry(script, "script"), *[self._entry(p, "input") for p in inputs]]
        if not pinned[0]["path"].endswith(".py"):
            raise BurrowlError("INVALID_ARGUMENT", "当前只支持 Python .py 脚本")
        if any(p["path"] == owner.native_path for p in pinned):
            raise BurrowlError("INVALID_ARGUMENT", "不能把 run.json 自身作为脚本或输入")
        run_dir = self.storage.ws.path(owner.native_path).parent
        folder = run_dir / ".run-captures" / uuid.uuid4().hex
        output = folder / "outputs"

        def locate(item: dict) -> Path:
            return (self.storage.sources.resolve(item["path"]).path if item["path"].startswith("SRC-")
                    else safe_path(self.storage.ws.root, item["path"]))

        values = {"output_dir": str(output), **{f"input{i}": str(locate(p)) for i, p in enumerate(pinned[1:])}}
        command = [sys.executable, str(locate(pinned[0])),
                   *[self._substitute(a, values) for a in args]]
        if preview:
            return {"status": "preview", "command": command, "inputs": pinned, "writes": 0}
        output.mkdir(parents=True)
        execution = {"command": command, "started_at": utc_now(), "python": sys.version, "inputs": pinned,
                     "exit_code": None, "status": "failed", "errors": []}
        try:
            with (folder / "stdout.txt").open("xb") as stdout, (folder / "stderr.txt").open("xb") as stderr:
                process = subprocess.run(command, cwd=output, stdout=stdout, stderr=stderr,
                                         timeout=timeout_seconds, shell=False, check=False)
            execution["exit_code"] = process.returncode
            execution["status"] = "succeeded" if process.returncode == 0 else "failed"
        except (OSError, subprocess.TimeoutExpired) as exc:
            execution["errors"].append(f"{type(exc).__name__}: {exc}")
        execution["ended_at"] = utc_now()
        artifacts = []
        try:
            for directory, _dirs, files in os.walk(output, followlinks=False):
                for name in files:
                    artifacts.append(self._entry(self.storage.ws.relative(Path(directory) / name), "output"))
            for item in pinned:
                if sha256_file(locate(item)) != item["sha256"]:
                    raise BurrowlError("STALE_BASIS", "执行期间输入文件发生变化", {"path": item["path"]})
        except (OSError, BurrowlError) as exc:
            execution["errors"].append(str(exc))
            execution["status"] = "failed"
        write_new(folder / "execution.json", _json_bytes(execution))
        for name in ("stdout.txt", "stderr.txt", "execution.json"):
            artifacts.append(self._entry(self.storage.ws.relative(folder / name), "execution_log"))

        def change(card: dict) -> dict:
            merged = self._merge(card, {"inputs": pinned, "artifacts": artifacts})
            merged.update(status=execution["status"], ended_at=execution["ended_at"],
                          started_at=card.get("started_at") or execution["started_at"])
            return merged

        result = self._mutate(run_id, change, capture=folder)
        return {**result, "execution_status": execution["status"], "exit_code": execution["exit_code"],
                "errors": execution["errors"], "output_dir": self.storage.ws.relative(output)}

    @staticmethod
    def _substitute(value: str, values: dict[str, str]) -> str:
        for key, replacement in values.items():
            value = value.replace("{" + key + "}", replacement)
        return value
