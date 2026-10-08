"""HTTP：操作调用、错误映射、安全边界（Host/Origin/内容类型/token 前缀）、用户动作。"""
import pytest
from fastapi.testclient import TestClient

from burrowl.app import App
from burrowl.interfaces.api import create_api

OWNER = "RES-FLOATING-POINT-SUMMATION"


@pytest.fixture
def client(sample_workspace):
    app = App.open(sample_workspace, fake_vectors=True)
    app.index.reconcile()
    api, token = create_api(app)
    return TestClient(api), f"/{token}/api/v1"


def test_ops_and_errors(client):
    http, base = client
    catalog = http.get(f"{base}/ops").json()
    assert any(op["name"] == "reading.respond" and op["effect"] == "user" for op in catalog)
    ok = http.post(f"{base}/ops/memory.inspect", json={"owner_id": OWNER})
    assert ok.status_code == 200 and ok.json()["head"]["generation"] == 19
    missing = http.post(f"{base}/ops/memory.inspect", json={"owner_id": "RES-NOPE"})
    assert missing.status_code == 404 and missing.json()["code"] == "NOT_FOUND"
    invalid = http.post(f"{base}/ops/memory.inspect", json={"owner": 1})
    assert invalid.status_code == 400 and invalid.json()["errors"]


def test_security_boundaries(client):
    http, base = client
    assert http.get("/api/v1/ops").status_code == 404  # 没有 token 前缀
    assert http.get(f"{base}/ops", headers={"host": "evil.example"}).status_code == 403
    cross = http.post(f"{base}/ops/memory.owners", json={}, headers={"origin": "http://evil.example"})
    assert cross.status_code == 403
    form = http.post(f"{base}/ops/memory.owners", content=b"a=1",
                     headers={"content-type": "application/x-www-form-urlencoded"})
    assert form.status_code == 415
    assert "default-src 'self'" in http.get(f"{base}/ops").headers["content-security-policy"]


def test_reading_user_actions_over_http(client):
    http, base = client
    started = http.post(f"{base}/ops/reading.start", json={"question": "浮点求和顺序误差"}).json()
    sid = started["session_id"]
    asked = http.post(f"{base}/ops/reading.ask_user", json={"session_id": sid, "kind": "fulltext",
                                                            "prompt": "允许全文补偿？"}).json()
    approved = http.post(f"{base}/ops/reading.respond", json={
        "session_id": sid, "request_id": asked["request"]["request_id"], "decision": "approve"}).json()
    assert approved["approval_id"].startswith("AP-")
    recalled = http.post(f"{base}/ops/reading.recall", json={"session_id": sid, "mode": "fulltext",
                                                             "approval_id": approved["approval_id"]})
    assert recalled.status_code == 200
