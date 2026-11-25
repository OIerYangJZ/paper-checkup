"""Paths and environment loading."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import dotenv_values

BACKEND_DIR = Path(__file__).resolve().parents[2]
REPO_DIR = BACKEND_DIR.parent
FRONTEND_DIR = REPO_DIR / "frontend"
PRESET_DATA_DIR = Path(os.environ.get("PRESET_DATA_DIR", FRONTEND_DIR / "public" / "data"))
FRONTEND_DIST = Path(os.environ.get("FRONTEND_DIST", FRONTEND_DIR / "dist"))
UPLOAD_DIR = Path(os.environ.get("UPLOAD_DIR", BACKEND_DIR / "data" / "uploads"))
PAPERS_DIR = REPO_DIR / "papers"
DEMO_CONFIG = REPO_DIR / "demo" / "papers.json"


def load_env():
    """Load .env files without overriding the real environment.

    Empty assignments (e.g. `OPENAI_BASE_URL=`) mean "not set": exporting them as empty strings would make
    SDKs that read their own env vars (OPENAI_BASE_URL, ANTHROPIC_BASE_URL, ...) use an empty URL.
    """
    for p in (REPO_DIR / ".env", BACKEND_DIR / ".env"):
        if not p.exists():
            continue
        for k, v in dotenv_values(p).items():
            if v is None or v.strip() == "" or k in os.environ:
                continue
            os.environ[k] = v.strip()
