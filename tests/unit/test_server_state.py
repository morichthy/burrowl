"""工作台后台运行的状态文件（R21 D-12）：网址固定、残留的进程信息被清除、不误认复用了 PID 的进程。"""
import os
import socket
import subprocess
import sys

from burrowl.core.atomic import process_identity
from burrowl.interfaces import server


def test_endpoint_is_kept_and_moves_only_when_port_taken(tmp_path):
    port, token = server.endpoint(tmp_path)
    assert server.endpoint(tmp_path) == (port, token)                    # 第二次启动：同一网址
    with socket.socket() as holder:
        holder.bind(("127.0.0.1", port))                                 # 端口被别的程序占用
        moved, same_token = server.endpoint(tmp_path)
    assert moved != port and same_token == token
    assert server.load(tmp_path)["port"] == moved


def test_running_clears_dead_or_reused_process(tmp_path):
    server.endpoint(tmp_path)
    server.mark_running(tmp_path, 1, "t")
    assert server.running(tmp_path)["pid"] == os.getpid()               # 本进程就是"运行中的工作台"
    child = subprocess.Popen([sys.executable, "-c", "pass"])
    child.wait()
    state = server.load(tmp_path) | {"pid": child.pid, "start_identity": "123"}
    server._save(tmp_path, state)
    assert server.running(tmp_path) is None                              # 进程已退出 → 清除运行信息
    assert "pid" not in server.load(tmp_path) and server.load(tmp_path)["token"]
    server._save(tmp_path, state | {"pid": os.getpid(), "start_identity": "not-this-process"})
    assert process_identity(os.getpid()) != "not-this-process"
    assert server.running(tmp_path) is None                              # PID 被复用 → 不认作工作台


def test_mark_stopped_only_clears_own_record(tmp_path):
    server.mark_running(tmp_path, 1, "t")
    server._save(tmp_path, server.load(tmp_path) | {"pid": os.getpid() + 1})   # 重启后新进程已写入
    server.mark_stopped(tmp_path)
    assert server.load(tmp_path)["pid"] == os.getpid() + 1
