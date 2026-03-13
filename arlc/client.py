"""
Agentic RAG Legal Challenge 2026 — platform API client.

Downloads questions and documents from the competition platform.
API key: ARLC_API_KEY. Base URL: ARLC_PLATFORM_URL (default https://platform.agentic-challenge.ai).
"""

from __future__ import annotations

import json
import os
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests


def _get_api_key(env_key: str = "ARLC_API_KEY") -> Optional[str]:
    key = (os.getenv(env_key) or "").strip()
    return key or None


def _get_base_url() -> str:
    return (os.getenv("ARLC_PLATFORM_URL") or "https://platform.agentic-challenge.ai").rstrip("/")


class EvaluationClient:
    """
    Client for the Agentic RAG Legal Challenge platform.
    - download_questions(): GET /api/v1/questions -> list of {id, question, answer_type}
    - download_documents(): GET /api/v1/documents -> ZIP of PDFs, extracted to dest dir
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
    ):
        self.api_key = api_key or _get_api_key()
        self.base_url = (base_url or _get_base_url()).rstrip("/")
        self._session = requests.Session()
        if self.api_key:
            self._session.headers["Authorization"] = f"Bearer {self.api_key}"
            self._session.headers["X-API-Key"] = self.api_key

    @classmethod
    def from_env(cls, api_key_env: str = "ARLC_API_KEY") -> "EvaluationClient":
        return cls(api_key=_get_api_key(api_key_env))

    def _get(self, path: str, stream: bool = False) -> requests.Response:
        url = f"{self.base_url}{path}"
        r = self._session.get(url, stream=stream, timeout=60)
        r.raise_for_status()
        return r

    def download_questions(self, save_dir: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        Fetch questions from the platform.
        Returns list of dicts with at least: id, question, answer_type.
        If save_dir is provided, also writes questions to save_dir/questions.json.
        """
        questions = None
        for path in ("/api/v1/questions", "/questions"):
            try:
                r = self._get(path)
                data = r.json()
                if isinstance(data, list):
                    questions = data
                elif isinstance(data, dict) and "questions" in data:
                    questions = data["questions"]
                else:
                    questions = data.get("data", data) if isinstance(data, dict) else []
                if questions is not None:
                    break
            except Exception:
                continue
        if questions is None:
            r = self._get("/api/v1/questions")
            data = r.json()
            if isinstance(data, list):
                questions = data
            elif isinstance(data, dict):
                questions = data.get("questions", data.get("data", []))
            else:
                questions = []
        questions = questions or []
        if save_dir:
            Path(save_dir).mkdir(parents=True, exist_ok=True)
            out_path = Path(save_dir) / "questions.json"
            with open(out_path, "w", encoding="utf-8") as f:
                json.dump(questions if isinstance(questions, list) else {"questions": questions}, f, ensure_ascii=False, indent=2)

        return questions

    def download_documents(self, dest_dir: str) -> Path:
        """Download documents ZIP from the platform and extract to dest_dir. Returns Path to dest_dir."""
        dest = Path(dest_dir)
        dest.mkdir(parents=True, exist_ok=True)
        for path in ("/api/v1/documents", "/documents"):
            try:
                r = self._get(path, stream=True)
                break
            except Exception:
                continue
        else:
            r = self._get("/api/v1/documents", stream=True)

        content_type = (r.headers.get("Content-Type") or "").lower()
        if "json" in content_type:
            data = r.json()
            url = data.get("url") or data.get("download_url") or data.get("link")
            if url:
                r = self._session.get(url, stream=True)
                r.raise_for_status()
            else:
                raise ValueError("API returned JSON but no download URL for documents")

        zip_path = dest / "_documents.zip"
        with open(zip_path, "wb") as f:
            for chunk in r.iter_content(chunk_size=65536):
                if chunk:
                    f.write(chunk)
        with zipfile.ZipFile(zip_path, "r") as zf:
            zf.extractall(dest)
        zip_path.unlink(missing_ok=True)
        return dest
