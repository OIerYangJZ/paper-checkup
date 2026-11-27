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


def test_figure_endpoint_rejects_bad_names(monkeypatch, tmp_path):
    monkeypatch.setattr(server, "UPLOAD_DIR", tmp_path)
    (tmp_path / "abc123" / "figures").mkdir(parents=True)
    (tmp_path / "abc123" / "figures" / "fig-1-1.png").write_bytes(b"\x89PNG\r\n")
    (tmp_path / "secret.png").write_bytes(b"SECRET")
    client = TestClient(server.app)
    r = client.get("/api/jobs/abc123/figures/fig-1-1.png")
    assert r.status_code == 200 and r.headers["content-type"] == "image/png"
    # An encoded traversal never reaches the file (with SERVE_STATIC the SPA fallback page may answer instead).
    r = client.get("/api/jobs/abc123/figures/..%2F..%2Fsecret.png")
    assert b"SECRET" not in r.content and not r.headers["content-type"].startswith("image/")
    assert client.get("/api/jobs/abc123/figures/report.json").status_code == 404
    assert client.get("/api/jobs/ab..c/figures/fig-1-1.png").status_code == 404


def test_retried_upload_reuses_job(monkeypatch, tmp_path):
    monkeypatch.setattr(server, "LLM_TOKEN", "")
    monkeypatch.setattr(server, "UPLOAD_DIR", tmp_path)
    monkeypatch.setattr(server, "CLIENT_JOBS", {})
    client = TestClient(server.app)
    files = {"files": ("paper.zip", _zip_fixture(), "application/zip")}
    first = client.post("/api/analyze", files=files, data={"use_llm": "false", "client_id": "abcd1234efgh"})
    again = client.post("/api/analyze", files=files, data={"use_llm": "false", "client_id": "abcd1234efgh"})
    assert first.status_code == again.status_code == 200
    assert first.json()["job_id"] == again.json()["job_id"]
    other = client.post("/api/analyze", files=files, data={"use_llm": "false", "client_id": "zzzz9999yyyy"})
    assert other.json()["job_id"] != first.json()["job_id"]
    # Without (or with a malformed) client_id every upload is a new job.
    a = client.post("/api/analyze", files=files, data={"use_llm": "false", "client_id": "x"})
    b = client.post("/api/analyze", files=files, data={"use_llm": "false", "client_id": "x"})
    assert a.json()["job_id"] != b.json()["job_id"]


def test_retry_after_error_is_a_fresh_run(monkeypatch, tmp_path):
    monkeypatch.setattr(server, "LLM_TOKEN", "")
    monkeypatch.setattr(server, "UPLOAD_DIR", tmp_path)
    monkeypatch.setattr(server, "CLIENT_JOBS", {})
    client = TestClient(server.app)
    files = {"files": ("notes.txt", b"hello", "text/plain")}
    r = client.post("/api/analyze", files=files, data={"use_llm": "false", "client_id": "rejected0001"})
    assert r.status_code == 400
    retry = client.post("/api/analyze", files=files, data={"use_llm": "false", "client_id": "rejected0001"})
    assert retry.status_code == 400 and retry.json()["detail"] == r.json()["detail"]
    # A finished-with-error job is not handed out again either.
    good = {"files": ("paper.zip", _zip_fixture(), "application/zip")}
    ok = client.post("/api/analyze", files=good, data={"use_llm": "false", "client_id": "rejected0001"})
    assert ok.status_code == 200
