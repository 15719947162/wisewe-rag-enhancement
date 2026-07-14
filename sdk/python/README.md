# WiseWe RAG Python SDK

这个目录是 OpenAPI / API Key 的最小 Python 交付包，面向 AI 基座、教务系统、内容平台和第三方业务系统等通用调用方。

## 能力

- HMAC-SHA256 签名头生成
- 知识库列表查询
- 普通 RAG 查询
- Graph RAG 查询
- 入库可选项查询
- 入库任务详情查询
- 任务 / 请求用量与成本查询

## 快速使用

```python
from wisewe_rag_client import WiseWeRagClient

client = WiseWeRagClient(
    base_url="http://localhost:8000",
    api_key="wwkb_ak_xxx_secret",
)

result = client.query(
    kb_id="6a30fe65b0b256647e733f4b",
    query="针灸学的发展脉络是什么？",
)
print(result["data"]["answer"])

task = client.get_ingestion_task(
    "94388fd5-495f-40d7-b78f-5eea33760eb7",
    kb_id="6a30fe65b0b256647e733f4b",
)
print(task["data"]["status"])

usage = client.get_task_usage(
    "94388fd5-495f-40d7-b78f-5eea33760eb7",
    limit=100,
)
print(usage["data"]["overall"])

```

## 签名口径

签名字符串固定为：

```text
METHOD
PATH_WITH_QUERY
TIMESTAMP
NONCE
BODY_SHA256
```

当前后端使用以下请求头：

- `Authorization: Bearer <api_key>`
- `X-KB-Timestamp`
- `X-KB-Nonce`
- `X-KB-Body-SHA256`
- `X-KB-Signature`

`X-KB-Signature` 为 `HMAC-SHA256(api_key, canonical_string)` 的 lowercase hex。

## 注意事项

- API Key 明文只在控制台创建或轮换时返回一次，SDK 不负责保存密钥。
- `nonce` 在同一个 API Key 的有效窗口内不能重复。
- `get_ingestion_task(task_id, kb_id=...)` 中的 `kb_id` 是可选兜底参数：当 API Key 未绑定任何知识库时必须传入，且必须等于任务真实所属知识库 ID；该 query 会参与 `PATH_WITH_QUERY` 签名。
- `get_task_usage(task_id, limit=...)` 使用 `GET /openapi/v1/usage/tasks/{task_id}`，需要 API Key 具备 `usage.read`。这是第三方单任务账单主入口，返回模型、解析、OSS 等用量与成本拆分；固定绑定 Key 只返回绑定知识库内的成本事件，空绑定 Key 按所属租户范围查询。
- multipart 上传接口当前按文件 bytes 计算 body hash；复杂上传场景建议先用后端文档中的 curl / Python 示例联调。
 
## Phase 12 多来源入库

SDK 现在覆盖 OpenAPI v1 的三类入库来源：

```python
file_task = client.upload_document(
    kb_id="6a30fe65b0b256647e733f4b",
    file_path="./教材.pdf",
)

web_task = client.ingest_webpage(
    kb_id="6a30fe65b0b256647e733f4b",
    url="https://example.com/docs",
    max_pages=10,
)

backup_task = client.upload_backup_csv(
    kb_id="6a30fe65b0b256647e733f4b",
    file_path="./document-backup.csv",
)
```

文件和备份 CSV 端点按上传文件 bytes 计算 `X-KB-Body-SHA256`；网页端点按实际 JSON body bytes 计算。
