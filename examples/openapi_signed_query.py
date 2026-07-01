from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import time
import uuid
from urllib import request as urllib_request


def build_signed_headers(api_key: str, method: str, path: str, body: bytes) -> dict[str, str]:
    timestamp = str(int(time.time()))
    nonce = uuid.uuid4().hex
    body_sha256 = hashlib.sha256(body).hexdigest()
    canonical = "\n".join([method.upper(), path, timestamp, nonce, body_sha256])
    signature = hmac.new(api_key.encode("utf-8"), canonical.encode("utf-8"), hashlib.sha256).hexdigest()
    return {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "X-KB-Timestamp": timestamp,
        "X-KB-Nonce": nonce,
        "X-KB-Body-SHA256": body_sha256,
        "X-KB-Signature": signature,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Call WiseWe RAG OpenAPI with HMAC signed headers.")
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument("--api-key", required=True)
    parser.add_argument("--kb-id", required=True)
    parser.add_argument("--query", required=True)
    parser.add_argument("--top-k", type=int, default=8)
    args = parser.parse_args()

    path = "/openapi/v1/rag/query"
    payload = {
        "query": args.query,
        "kb_id": args.kb_id,
        "top_k": args.top_k,
        "min_score": 0.3,
    }
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    headers = build_signed_headers(args.api_key, "POST", path, body)
    req = urllib_request.Request(f"{args.base_url.rstrip('/')}{path}", data=body, headers=headers, method="POST")
    with urllib_request.urlopen(req, timeout=60) as response:
        print(response.read().decode("utf-8"))


if __name__ == "__main__":
    main()
