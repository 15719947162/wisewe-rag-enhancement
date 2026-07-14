from __future__ import annotations

import hashlib
import hmac
import json
import mimetypes
import os
import time
import uuid
from dataclasses import dataclass
from urllib import error as urllib_error
from urllib import parse as urllib_parse
from urllib import request as urllib_request


class WiseWeRagError(RuntimeError):
    def __init__(self, message: str, *, status: int | None = None, payload: dict | None = None) -> None:
        super().__init__(message)
        self.status = status
        self.payload = payload or {}


@dataclass(frozen=True)
class WiseWeRagClient:
    base_url: str
    api_key: str
    timeout: int = 60

    def list_knowledge_bases(self, *, scope: str = "mine", page: int = 1, page_size: int = 20) -> dict:
        query = urllib_parse.urlencode({"scope": scope, "page": page, "page_size": page_size})
        return self._request("GET", f"/openapi/v1/knowledge-bases?{query}", b"")

    def query(
        self,
        *,
        kb_id: str,
        query: str,
        top_k: int = 8,
        min_score: float = 0.3,
        use_llm_check: bool = False,
        use_llm_score: bool = False,
    ) -> dict:
        body = self._json_body(
            {
                "kb_id": kb_id,
                "query": query,
                "top_k": top_k,
                "min_score": min_score,
                "use_llm_check": use_llm_check,
                "use_llm_score": use_llm_score,
            }
        )
        return self._request("POST", "/openapi/v1/rag/query", body)

    def graph_query(
        self,
        *,
        kb_id: str,
        query: str,
        top_k: int = 8,
        min_score: float = 0.3,
        explain: bool = True,
        intent: str | None = None,
    ) -> dict:
        body = self._json_body(
            {
                "kb_id": kb_id,
                "query": query,
                "top_k": top_k,
                "min_score": min_score,
                "explain": explain,
                "intent": intent,
            }
        )
        return self._request("POST", "/openapi/v1/rag/graph-query", body)

    def ingestion_options(self) -> dict:
        return self._request("GET", "/openapi/v1/ingestion/options", b"")

    def get_ingestion_task(self, task_id: str, *, kb_id: str | None = None) -> dict:
        path = f"/openapi/v1/ingestion/tasks/{urllib_parse.quote(task_id)}"
        if kb_id:
            path = f"{path}?{urllib_parse.urlencode({'kb_id': kb_id})}"
        return self._request("GET", path, b"")

    def get_task_usage(self, task_id: str, *, limit: int = 100) -> dict:
        query = urllib_parse.urlencode({"limit": limit})
        path = f"/openapi/v1/usage/tasks/{urllib_parse.quote(task_id)}?{query}"
        return self._request("GET", path, b"")

    def upload_document(
        self,
        *,
        kb_id: str,
        file_path: str,
        chunk_strategy: str = "hierarchical",
        subject_type: str = "general",
        layout_type: str = "single_column",
        parser_provider: str | None = None,
        auto_confirm: bool = False,
    ) -> dict:
        fields = {
            "kb_id": kb_id,
            "chunk_strategy": chunk_strategy,
            "subject_type": subject_type,
            "layout_type": layout_type,
            "auto_confirm": "true" if auto_confirm else "false",
        }
        if parser_provider:
            fields["parser_provider"] = parser_provider
        return self._multipart_file_request("POST", "/openapi/v1/ingestion/upload", fields, "file", file_path)

    def ingest_webpage(
        self,
        *,
        kb_id: str,
        url: str,
        chunk_strategy: str = "hierarchical",
        subject_type: str = "general",
        layout_type: str = "single_column",
        max_depth: int = 1,
        max_pages: int = 10,
        same_domain_only: bool = True,
        include_patterns: list[str] | None = None,
        exclude_patterns: list[str] | None = None,
        max_page_bytes: int = 2 * 1024 * 1024,
        timeout_seconds: int = 12,
    ) -> dict:
        body = self._json_body(
            {
                "kb_id": kb_id,
                "url": url,
                "chunk_strategy": chunk_strategy,
                "subject_type": subject_type,
                "layout_type": layout_type,
                "max_depth": max_depth,
                "max_pages": max_pages,
                "same_domain_only": same_domain_only,
                "include_patterns": include_patterns or [],
                "exclude_patterns": exclude_patterns or [],
                "max_page_bytes": max_page_bytes,
                "timeout_seconds": timeout_seconds,
            }
        )
        return self._request("POST", "/openapi/v1/ingestion/webpage", body)

    def upload_backup_csv(self, *, kb_id: str, file_path: str) -> dict:
        return self._multipart_file_request(
            "POST",
            "/openapi/v1/ingestion/backup-csv",
            {"kb_id": kb_id},
            "file",
            file_path,
        )

    def signed_headers(self, method: str, path: str, body: bytes = b"") -> dict[str, str]:
        timestamp = str(int(time.time()))
        nonce = uuid.uuid4().hex
        body_sha256 = hashlib.sha256(body).hexdigest()
        canonical = "\n".join([method.upper(), path, timestamp, nonce, body_sha256])
        signature = hmac.new(self.api_key.encode("utf-8"), canonical.encode("utf-8"), hashlib.sha256).hexdigest()
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "X-KB-Timestamp": timestamp,
            "X-KB-Nonce": nonce,
            "X-KB-Body-SHA256": body_sha256,
            "X-KB-Signature": signature,
        }

    def _request(self, method: str, path: str, body: bytes) -> dict:
        headers = self.signed_headers(method, path, body)
        req = urllib_request.Request(
            f"{self.base_url.rstrip('/')}{path}",
            data=body if method.upper() not in {"GET", "HEAD"} else None,
            headers=headers,
            method=method.upper(),
        )
        try:
            with urllib_request.urlopen(req, timeout=self.timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib_error.HTTPError as exc:
            payload = _read_error_payload(exc)
            message = payload.get("error", {}).get("message") or payload.get("detail") or str(exc)
            raise WiseWeRagError(message, status=exc.code, payload=payload) from exc

    def _multipart_file_request(
        self,
        method: str,
        path: str,
        fields: dict[str, str],
        file_field: str,
        file_path: str,
    ) -> dict:
        filename = os.path.basename(file_path)
        with open(file_path, "rb") as file_obj:
            file_bytes = file_obj.read()
        body, content_type = _multipart_body(fields, file_field, filename, file_bytes)
        headers = self.signed_headers(method, path, file_bytes)
        headers["Content-Type"] = content_type
        req = urllib_request.Request(
            f"{self.base_url.rstrip('/')}{path}",
            data=body,
            headers=headers,
            method=method.upper(),
        )
        try:
            with urllib_request.urlopen(req, timeout=self.timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib_error.HTTPError as exc:
            payload = _read_error_payload(exc)
            message = payload.get("error", {}).get("message") or payload.get("detail") or str(exc)
            raise WiseWeRagError(message, status=exc.code, payload=payload) from exc

    @staticmethod
    def _json_body(payload: dict) -> bytes:
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def _read_error_payload(exc: urllib_error.HTTPError) -> dict:
    try:
        return json.loads(exc.read().decode("utf-8"))
    except Exception:
        return {}


def _multipart_body(fields: dict[str, str], file_field: str, filename: str, file_bytes: bytes) -> tuple[bytes, str]:
    boundary = f"----wisewe-rag-{uuid.uuid4().hex}"
    chunks: list[bytes] = []
    for key, value in fields.items():
        chunks.extend(
            [
                f"--{boundary}\r\n".encode("utf-8"),
                f'Content-Disposition: form-data; name="{key}"\r\n\r\n'.encode("utf-8"),
                str(value).encode("utf-8"),
                b"\r\n",
            ]
        )
    content_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
    chunks.extend(
        [
            f"--{boundary}\r\n".encode("utf-8"),
            f'Content-Disposition: form-data; name="{file_field}"; filename="{filename}"\r\n'.encode("utf-8"),
            f"Content-Type: {content_type}\r\n\r\n".encode("utf-8"),
            file_bytes,
            b"\r\n",
            f"--{boundary}--\r\n".encode("utf-8"),
        ]
    )
    return b"".join(chunks), f"multipart/form-data; boundary={boundary}"
