import io
import zipfile
from pathlib import Path

from fastapi.testclient import TestClient

import paper_checkup.server as server

FIXTURE = Path(__file__).parent / "fixtures" / "sample"


def _zip_fixture() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for p in FIXTURE.rglob("*"):
            if p.is_file():
                zf.write(p, p.relative_to(FIXTURE.parent).as_posix())
    return buf.getvalue()


def test_access_token_required_for_llm(monkeypatch, tmp_path):
    monkeypatch.setattr(server, "LLM_TOKEN", "s3cret")
    monkeypatch.setattr(server, "UPLOAD_DIR", tmp_path)
    client = TestClient(server.app)
    assert client.get("/api/health").json()["llm"]["requires_token"] is True
    files = {"files": ("paper.zip", _zip_fixture(), "application/zip")}
    r = client.post("/api/analyze", files=files, data={"use_llm": "true"})
    assert r.status_code == 403
    r = client.post("/api/analyze", files=files, data={"use_llm": "true", "access_token": "wrong"})
    assert r.status_code == 403
    # Deterministic analysis does not need the code.
    r = client.post("/api/analyze", files=files, data={"use_llm": "false"})
    assert r.status_code == 200 and r.json()["job_id"]


def test_token_check_without_configured_token(monkeypatch):
    monkeypatch.setattr(server, "LLM_TOKEN", "")
    assert server._token_ok(None) and server._token_ok("anything")
    monkeypatch.setattr(server, "LLM_TOKEN", "abc")
    assert server._token_ok("abc") and server._token_ok(" abc ") and not server._token_ok("abd")
