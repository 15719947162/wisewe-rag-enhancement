"use client";

import { useState } from "react";
import Link from "next/link";
import { ContextRail } from "@/components/layout/context-rail";
import { Badge } from "@/components/ui/badge";
import { Modal } from "@/components/ui/modal";

type ApiStatus = "已开放" | "规划待接入";
type SignaturePolicy = "必须强签名" | "生产建议强签名" | "按 Key 策略";

type ApiDoc = {
  name: string;
  purpose: string;
  scenario: string;
  method: "GET" | "POST";
  path: string;
  capability: string;
  auth: string;
  signature: SignaturePolicy;
  status: ApiStatus;
  note: string;
  params: Array<[string, string, string, string]>;
  example: string;
  success: string;
  error: string;
};

const apiDocs: ApiDoc[] = [
  {
    name: "查询知识库列表",
    purpose: "供 AI 基座用户端按当前用户、当前租户或管理员视角选择可用知识库。",
    scenario: "知识库选择、上传前选择目标库、用户工作台初始化",
    method: "GET",
    path: "/openapi/v1/knowledge-bases",
    capability: "kb.list",
    auth: "API Key + 可信用户上下文",
    signature: "生产建议强签名",
    status: "已开放",
    note: "scope=mine/tenant/all 用于区分本人、租户和平台视角；userId/roleCode 只能作为过滤提示，最终权限以后端身份快照为准。",
    params: [
      ["scope", "query", "string", "可选，mine / tenant / all，默认 mine"],
      ["user_id", "query", "string", "可选，仅管理员代查时使用"],
      ["role_code", "query", "string", "可选，只作过滤提示，不作为可信授权依据"],
      ["page", "query", "integer", "可选，默认 1"],
      ["page_size", "query", "integer", "可选，默认 20，最大 100"],
    ],
    example: `curl "http://127.0.0.1:8000/openapi/v1/knowledge-bases?scope=mine&page=1&page_size=20" \\
  -H "Authorization: Bearer wwkb_ak_xxx_once_visible_secret"`,
    success: `{
  "requestId": "uuid",
  "data": {
    "scope": "mine",
    "items": [
      {
        "id": "6a30fe65b0b256647e733f4b",
        "name": "中医教材知识库",
        "tenantId": "1",
        "ownerUserId": "100",
        "documentCount": 12,
        "updatedAt": "2026-06-26 10:00:00"
      }
    ],
    "total": 1
  }
}`,
    error: `{
  "requestId": "uuid",
  "error": {
    "code": "CAPABILITY_DENIED",
    "message": "API Key lacks kb.list capability",
    "details": {}
  }
}`,
  },
  {
    name: "上传文件并创建入库任务",
    purpose: "AI 基座用户端上传教材 PDF，并选择切片策略、教材类型、教材排版和解析管道。",
    scenario: "用户上传教材、讲义入库、第三方系统推送文档",
    method: "POST",
    path: "/openapi/v1/ingestion/upload",
    capability: "ingestion.upload",
    auth: "API Key + 目标知识库绑定",
    signature: "必须强签名",
    status: "已开放",
    note: "文件上传是写操作且请求体较大，生产必须校验 HMAC、timestamp、nonce、body hash、IP 白名单和能力范围。",
    params: [
      ["file", "form-data", "file", "必填，仅支持 PDF，沿用系统 500MB 上限"],
      ["kb_id", "form-data", "string", "必填，目标知识库 ID"],
      ["chunk_strategy", "form-data", "string", "可选，hierarchical / semantic / paragraph / fixed_length / separator / llm"],
      ["subject_type", "form-data", "string", "可选，默认 general，必须遵循 RAG 已支持教材类型"],
      ["layout_type", "form-data", "string", "可选，默认 single_column，必须遵循 RAG 已支持排版类型"],
      ["parser_provider", "form-data", "string", "可选，mineru / mineru_official / ali_document_mind"],
      ["auto_confirm", "form-data", "boolean", "可选，默认 false；是否自动确认入库"],
    ],
    example: `curl -X POST http://127.0.0.1:8000/openapi/v1/ingestion/upload \\
  -H "Authorization: Bearer wwkb_ak_xxx_once_visible_secret" \\
  -H "X-KB-Timestamp: 1792999200" \\
  -H "X-KB-Nonce: upload-20260626-001" \\
  -H "X-KB-Body-SHA256: <file-bytes-sha256>" \\
  -H "X-KB-Signature: <hmac-sha256>" \\
  -F "file=@教材.pdf" \\
  -F "kb_id=6a30fe65b0b256647e733f4b" \\
  -F "chunk_strategy=hierarchical" \\
  -F "subject_type=general" \\
  -F "layout_type=single_column" \\
  -F "parser_provider=mineru_official"`,
    success: `{
  "requestId": "uuid",
  "data": {
    "taskId": "task_xxx",
    "kbId": "6a30fe65b0b256647e733f4b",
    "status": "pending",
    "autoConfirm": false
  }
}`,
    error: `{
  "requestId": "uuid",
  "error": {
    "code": "SIGNATURE_REQUIRED",
    "message": "Signed OpenAPI headers are required for this API",
    "details": {}
  }
}`,
  },
  {
    name: "查询入库任务详情",
    purpose: "查询上传后解析、清洗、切片、质检、向量化和写库进度。",
    scenario: "上传后轮询、任务状态展示、失败原因排查",
    method: "GET",
    path: "/openapi/v1/ingestion/tasks/{task_id}",
    capability: "ingestion.read",
    auth: "API Key + 任务所属知识库权限",
    signature: "生产建议强签名",
    status: "已开放",
    note: "只返回任务状态、阶段进度和脱敏错误，不返回完整 prompt、模型原始响应或文档正文。",
    params: [
      ["task_id", "path", "string", "必填，入库任务 ID"],
    ],
    example: `curl "http://127.0.0.1:8000/openapi/v1/ingestion/tasks/task_xxx" \\
  -H "Authorization: Bearer wwkb_ak_xxx_once_visible_secret"`,
    success: `{
  "requestId": "uuid",
  "data": {
    "taskId": "task_xxx",
    "status": "running",
    "currentStage": "chunk",
    "stages": [
      { "key": "parse", "status": "success", "progress": 100 },
      { "key": "chunk", "status": "running", "progress": 42 }
    ]
  }
}`,
    error: `{
  "requestId": "uuid",
  "error": {
    "code": "TASK_NOT_FOUND",
    "message": "Task not found or not accessible",
    "details": {}
  }
}`,
  },
  {
    name: "查询入库可选项",
    purpose: "给 AI 基座用户端下拉框提供 RAG 当前支持的切片策略、教材类型、教材排版和解析管道。",
    scenario: "上传表单初始化、解析管道选择、前端下拉配置",
    method: "GET",
    path: "/openapi/v1/ingestion/options",
    capability: "ingestion.options",
    auth: "API Key",
    signature: "按 Key 策略",
    status: "已开放",
    note: "解析管道应返回 available/reason，避免前端选到当前环境缺少密钥或未启用的 provider。",
    params: [
      ["include_unavailable", "query", "boolean", "可选，是否返回当前环境不可用的解析管道"],
    ],
    example: `curl "http://127.0.0.1:8000/openapi/v1/ingestion/options" \\
  -H "Authorization: Bearer wwkb_ak_xxx_once_visible_secret"`,
    success: `{
  "requestId": "uuid",
  "data": {
    "chunkStrategies": [
      { "value": "hierarchical", "label": "三层切片" }
    ],
    "subjectTypes": [
      { "value": "general", "label": "通用教材" }
    ],
    "layoutTypes": [
      { "value": "single_column", "label": "单栏排版" }
    ],
    "parserProviders": [
      { "value": "mineru", "label": "302AI MinerU", "available": true },
      { "value": "mineru_official", "label": "官方 MinerU", "available": true },
      { "value": "ali_document_mind", "label": "阿里 Document Mind", "available": false, "reason": "缺少 AK/SK" }
    ]
  }
}`,
    error: `{
  "requestId": "uuid",
  "error": {
    "code": "API_KEY_REQUIRED",
    "message": "OpenAPI authentication is required",
    "details": {}
  }
}`,
  },
  {
    name: "普通 RAG 查询",
    purpose: "基于指定知识库执行普通问答，返回答案、引用证据、候选和评分摘要。",
    scenario: "AI 基座问答入口、第三方业务系统知识问答",
    method: "POST",
    path: "/openapi/v1/rag/query",
    capability: "rag.query",
    auth: "API Key + 目标知识库绑定",
    signature: "生产建议强签名",
    status: "已开放",
    note: "当前后端已实现。若 API Key requireSignature=true，则必须携带强签名请求头。",
    params: [
      ["query", "json", "string", "必填，1-4000 字符"],
      ["kb_id", "json", "string", "必填，知识库 ID"],
      ["top_k", "json", "integer", "可选，默认 8，范围 1-20"],
      ["min_score", "json", "number", "可选，默认 0.3，范围 0-1"],
      ["use_llm_check", "json", "boolean", "可选，默认 false"],
      ["use_llm_score", "json", "boolean", "可选，默认 false"],
    ],
    example: `curl -X POST http://127.0.0.1:8000/openapi/v1/rag/query \\
  -H "Content-Type: application/json" \\
  -H "Authorization: Bearer wwkb_ak_xxx_once_visible_secret" \\
  -d '{
    "kb_id": "6a30fe65b0b256647e733f4b",
    "query": "系统如何保证答案证据可追溯？",
    "top_k": 8,
    "min_score": 0.3
  }'`,
    success: `{
  "requestId": "uuid",
  "data": {
    "requestId": "uuid",
    "answer": "...",
    "citations": [],
    "scores": {},
    "candidates": [],
    "trace": []
  }
}`,
    error: `{
  "requestId": "uuid",
  "error": {
    "code": "KB_BINDING_DENIED",
    "message": "API Key is not bound to this knowledge base",
    "details": {}
  }
}`,
  },
  {
    name: "Graph RAG 查询",
    purpose: "在普通召回基础上附加图谱关系、结构化解释或意图信息。",
    scenario: "带关系解释的知识问答、图谱增强检索",
    method: "POST",
    path: "/openapi/v1/rag/graph-query",
    capability: "rag.graph_query",
    auth: "API Key + 目标知识库绑定",
    signature: "生产建议强签名",
    status: "已开放",
    note: "当前后端已实现。若 API Key requireSignature=true，则必须携带强签名请求头。",
    params: [
      ["query", "json", "string", "必填，1-4000 字符"],
      ["kb_id", "json", "string", "必填，知识库 ID"],
      ["top_k", "json", "integer", "可选，默认 5，范围 1-20"],
      ["min_score", "json", "number", "可选，默认 0.3，范围 0-1"],
      ["explain", "json", "boolean", "可选，默认 false"],
      ["intent", "json", "string", "可选，最长 100 字符"],
    ],
    example: `curl -X POST http://127.0.0.1:8000/openapi/v1/rag/graph-query \\
  -H "Content-Type: application/json" \\
  -H "Authorization: Bearer wwkb_ak_xxx_once_visible_secret" \\
  -d '{
    "kb_id": "6a30fe65b0b256647e733f4b",
    "query": "针灸学发展涉及哪些关键阶段？",
    "top_k": 5,
    "explain": true
  }'`,
    success: `{
  "requestId": "uuid",
  "data": {
    "requestId": "uuid",
    "answer": "...",
    "citations": [],
    "graph": {},
    "trace": []
  }
}`,
    error: `{
  "requestId": "uuid",
  "error": {
    "code": "CAPABILITY_DENIED",
    "message": "API Key lacks the required capability",
    "details": {}
  }
}`,
  },
  {
    name: "清洗提示词追加",
    purpose: "在保持 RAG 系统默认清洗提示词不变的前提下，为单次入库追加用户侧清洗要求。",
    scenario: "教材清洗保留章节号、保留表格标题、避免删除特定术语",
    method: "POST",
    path: "/openapi/v1/ingestion/upload",
    capability: "ingestion.clean_prompt.append",
    auth: "API Key + ingestion.upload",
    signature: "必须强签名",
    status: "已开放",
    note: "不建议开放 raw system prompt replace。第一期仅允许 append 或后续审核过的 prompt_profile_id。",
    params: [
      ["cleaning_prompt_override.mode", "json/form-data", "string", "可选，仅允许 append"],
      ["cleaning_prompt_override.content", "json/form-data", "string", "可选，追加清洗要求，需做长度和敏感内容限制"],
      ["cleaning_prompt_profile_id", "json/form-data", "string", "可选，后续审核通过的清洗 Prompt Profile"],
    ],
    example: `{
  "cleaning_prompt_override": {
    "mode": "append",
    "content": "请保留教材中的章节编号和表格标题。"
  }
}`,
    success: `{
  "requestId": "uuid",
  "data": {
    "taskId": "task_xxx",
    "promptPolicy": "system_default_plus_append"
  }
}`,
    error: `{
  "requestId": "uuid",
  "error": {
    "code": "PROMPT_OVERRIDE_DENIED",
    "message": "This API Key cannot override cleaning prompt",
    "details": {}
  }
}`,
  },
  {
    name: "质检提示词追加",
    purpose: "在保持 RAG 系统默认质检提示词不变的前提下，为单次入库追加用户侧质检重点。",
    scenario: "医学术语质检、表格完整性检查、章节结构检查",
    method: "POST",
    path: "/openapi/v1/ingestion/upload",
    capability: "ingestion.quality_prompt.append",
    auth: "API Key + ingestion.upload",
    signature: "必须强签名",
    status: "已开放",
    note: "质检提示词会影响入库结果，必须审计，不写入普通查询日志，不返回完整 prompt。",
    params: [
      ["quality_prompt_override.mode", "json/form-data", "string", "可选，仅允许 append"],
      ["quality_prompt_override.content", "json/form-data", "string", "可选，追加质检要求，需做长度和敏感内容限制"],
      ["quality_prompt_profile_id", "json/form-data", "string", "可选，后续审核通过的质检 Prompt Profile"],
    ],
    example: `{
  "quality_prompt_override": {
    "mode": "append",
    "content": "请重点检查医学术语是否被错误合并。"
  }
}`,
    success: `{
  "requestId": "uuid",
  "data": {
    "taskId": "task_xxx",
    "promptPolicy": "system_default_plus_append"
  }
}`,
    error: `{
  "requestId": "uuid",
  "error": {
    "code": "PROMPT_OVERRIDE_DENIED",
    "message": "This API Key cannot override quality prompt",
    "details": {}
  }
}`,
  },
];

const errors = [
  ["400", "KB_ID_REQUIRED", "OpenAPI 调用必须显式传入 kb_id。"],
  ["401", "API_KEY_REQUIRED", "缺少 Authorization: Bearer <api_key> 或 X-API-Key。"],
  ["401", "SIGNATURE_REQUIRED", "当前 API Key 或接口要求强签名，但缺少签名请求头。"],
  ["401", "INVALID_SIGNATURE / BODY_HASH_MISMATCH", "签名无效或 body hash 与原始请求体不一致。"],
  ["401", "TIMESTAMP_EXPIRED / NONCE_REPLAYED", "请求时间窗过期或 nonce 重放。"],
  ["403", "IP_NOT_ALLOWED", "调用方 IP 不在 API Key 白名单。"],
  ["403", "KB_BINDING_DENIED / CAPABILITY_DENIED", "API Key 未绑定目标知识库或缺少能力。"],
  ["422", "VALIDATION_ERROR", "请求体字段类型、范围或未知字段校验失败。"],
  ["503", "OPENAPI_QUERY_FAILED / OPENAPI_GRAPH_QUERY_FAILED", "查询链路执行失败。"],
];

export default function OpenApiPage() {
  const [detailApi, setDetailApi] = useState<ApiDoc | null>(null);

  return (
    <div className="space-y-6">
      <ContextRail
        title="OpenAPI 调用文档"
        description="面向 AI 基座用户端和第三方系统的知识库开放接口说明，覆盖 API 名称、用途、场景、强签名要求、参数、调用示例和响应格式。"
        showGlobalHint={false}
      />

      <div className="flex flex-col gap-3 lg:flex-row lg:items-start lg:justify-between">
        <div>
          <div className="flex flex-wrap items-center gap-2">
            <h1 className="text-[30px] font-bold leading-tight text-ink-primary">开放 API 清单</h1>
            <Badge variant="info">v1</Badge>
            <Badge variant="success">API Key</Badge>
            <Badge variant="warning">高风险接口强签名</Badge>
          </div>
          <p className="mt-1 max-w-4xl text-sm leading-6 text-ink-secondary">
            当前已开放普通 RAG 查询、Graph RAG 查询、知识库列表、上传入库、入库任务、可选项和提示词追加的最小接口。
            写操作、提示词影响类接口和生产外网调用必须结合 OpenAPI 强签名，至少校验 HMAC、timestamp、nonce、body hash、IP 白名单和能力范围。
          </p>
        </div>
        <Link
          href="/api-keys"
          className="inline-flex h-9 items-center rounded-md border border-[#0EA5E9]/24 bg-white/82 px-3 text-[13px] font-medium text-[#0369A1] shadow-sm transition-colors hover:border-[#0EA5E9]/45 hover:bg-[#ECF8FF]"
        >
          管理 API Key
        </Link>
      </div>

      <section className="overflow-hidden rounded-lg border border-[#0EA5E9]/24 bg-white/86 shadow-panel">
        <div className="border-b border-[#BAE6FD]/80 bg-[linear-gradient(90deg,rgba(14,165,233,0.12),rgba(79,70,229,0.08),rgba(255,255,255,0.76))] px-5 py-4">
          <p className="text-[11px] font-semibold uppercase tracking-[0.1em] text-[#0369A1]">API Table</p>
          <h2 className="mt-1 text-base font-semibold text-ink-primary">API 总表</h2>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full min-w-[1120px] text-sm">
            <thead>
              <tr className="border-b border-[#BAE6FD]/80 bg-[#ECF8FF] text-[12px] font-medium uppercase tracking-[0.06em] text-[#0369A1]">
                <th className="px-4 py-2.5 text-left">API 名称</th>
                <th className="px-4 py-2.5 text-left">API 用途</th>
                <th className="px-4 py-2.5 text-left">方法</th>
                <th className="px-4 py-2.5 text-left">路径</th>
                <th className="px-4 py-2.5 text-left">能力</th>
                <th className="px-4 py-2.5 text-left">强签名</th>
                <th className="px-4 py-2.5 text-left">状态</th>
                <th className="px-4 py-2.5 text-left">详情</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-[#BAE6FD]/70">
              {apiDocs.map((api) => (
                <tr
                  key={api.name}
                  className="align-top transition-colors hover:bg-[#ECF8FF]/70"
                >
                  <td className="px-4 py-3 font-medium text-ink-primary">{api.name}</td>
                  <td className="px-4 py-3 text-xs leading-5 text-ink-secondary">{api.purpose}</td>
                  <td className="px-4 py-3"><Badge variant={api.method === "GET" ? "info" : "success"}>{api.method}</Badge></td>
                  <td className="px-4 py-3"><code className="break-all rounded-sm bg-subtle px-2 py-1 font-mono text-xs text-ink-primary">{api.path}</code></td>
                  <td className="px-4 py-3"><code className="font-mono text-xs text-ink-secondary">{api.capability}</code></td>
                  <td className="px-4 py-3">
                    <Badge
                      variant={api.signature === "必须强签名" ? "warning" : api.signature === "生产建议强签名" ? "info" : "neutral"}
                      className="whitespace-nowrap"
                    >
                      {api.signature}
                    </Badge>
                  </td>
                  <td className="px-4 py-3">
                    <Badge variant={api.status === "已开放" ? "success" : "warning"} dot className="whitespace-nowrap">
                      {api.status}
                    </Badge>
                  </td>
                  <td className="px-4 py-3">
                    <button
                      type="button"
                      onClick={() => setDetailApi(api)}
                      className="inline-flex h-8 cursor-pointer items-center rounded-md border border-[#0EA5E9]/24 bg-white px-3 text-xs font-semibold text-[#0369A1] transition-colors hover:border-[#0EA5E9]/45 hover:bg-[#ECF8FF]"
                    >
                      查看
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      <Modal
        open={Boolean(detailApi)}
        onClose={() => setDetailApi(null)}
        title={detailApi?.name ?? "API 详情"}
        size="xl"
      >
        {detailApi && <ApiDetailPanel api={detailApi} />}
      </Modal>

      <section className="overflow-hidden rounded-lg border border-[#0EA5E9]/24 bg-white/86 shadow-panel">
        <div className="border-b border-[#BAE6FD]/80 bg-[linear-gradient(90deg,rgba(14,165,233,0.12),rgba(79,70,229,0.08),rgba(255,255,255,0.76))] px-5 py-4">
          <p className="text-[11px] font-semibold uppercase tracking-[0.1em] text-[#0369A1]">Signature</p>
          <h2 className="mt-1 text-base font-semibold text-ink-primary">强签名判断规则</h2>
        </div>
        <div className="grid gap-4 p-5 lg:grid-cols-3">
          <InfoBlock label="必须强签名" value="上传文件、提示词追加、后续批量导出或任何写操作。原因是这些接口会改变入库结果或携带大体积请求体。" />
          <InfoBlock label="生产建议强签名" value="知识库列表、任务查询、RAG 查询和 Graph RAG 查询。若 API Key requireSignature=true，后端会强制校验。" />
          <InfoBlock label="暂不可信任 roleCode" value="userId/roleCode 只能辅助筛选，不能直接授权；最终以 AI 基座 SSO 身份快照或可信签名上下文为准。" />
        </div>
      </section>

      <section className="overflow-hidden rounded-lg border border-[#0EA5E9]/24 bg-white/86 shadow-panel">
        <div className="border-b border-[#BAE6FD]/80 bg-[linear-gradient(90deg,rgba(14,165,233,0.12),rgba(79,70,229,0.08),rgba(255,255,255,0.76))] px-5 py-4">
          <p className="text-[11px] font-semibold uppercase tracking-[0.1em] text-[#0369A1]">Errors</p>
          <h2 className="mt-1 text-base font-semibold text-ink-primary">错误码</h2>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full min-w-[760px] text-sm">
            <thead>
              <tr className="border-b border-[#BAE6FD]/80 bg-[#ECF8FF] text-[12px] font-medium uppercase tracking-[0.06em] text-[#0369A1]">
                <th className="px-4 py-2.5 text-left">HTTP</th>
                <th className="px-4 py-2.5 text-left">code</th>
                <th className="px-4 py-2.5 text-left">说明</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-[#BAE6FD]/70">
              {errors.map(([status, code, note]) => (
                <tr key={code} className="transition-colors hover:bg-[#ECF8FF]/70">
                  <td className="px-4 py-3 font-mono text-xs text-ink-primary">{status}</td>
                  <td className="px-4 py-3 font-mono text-xs text-ink-primary">{code}</td>
                  <td className="px-4 py-3 text-ink-secondary">{note}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>
    </div>
  );
}

function ApiDetailPanel({ api }: { api: ApiDoc }) {
  return (
    <div className="overflow-hidden rounded-lg border border-[#0EA5E9]/24 bg-white/86 shadow-panel">
      <div className="border-b border-[#BAE6FD]/80 bg-[linear-gradient(90deg,rgba(14,165,233,0.12),rgba(79,70,229,0.08),rgba(255,255,255,0.76))] px-5 py-4">
        <div className="flex flex-col gap-3 lg:flex-row lg:items-start lg:justify-between">
          <div>
            <p className="text-[11px] font-semibold uppercase tracking-[0.1em] text-[#0369A1]">API Detail</p>
            <div className="mt-1 flex flex-wrap items-center gap-2">
              <Badge variant={api.method === "GET" ? "info" : "success"}>{api.method}</Badge>
              <h2 className="text-base font-semibold text-ink-primary">{api.name}</h2>
              <Badge variant={api.status === "已开放" ? "success" : "warning"}>{api.status}</Badge>
            </div>
          </div>
          <code className="break-all rounded-sm bg-white/86 px-2 py-1 font-mono text-xs text-ink-primary shadow-sm">
            {api.path}
          </code>
        </div>
      </div>
      <div className="space-y-5 p-5">
        <div className="grid gap-4 lg:grid-cols-3">
          <InfoBlock label="使用场景" value={api.scenario} />
          <InfoBlock label="鉴权方式" value={api.auth} />
          <InfoBlock label="强签名要求" value={api.signature} />
        </div>
        <p className="text-sm leading-6 text-ink-secondary">{api.note}</p>
        <div className="overflow-x-auto rounded-lg border border-[#BAE6FD]/80">
          <table className="w-full min-w-[760px] text-sm">
            <thead>
              <tr className="bg-[#ECF8FF] text-[12px] font-medium uppercase tracking-[0.06em] text-[#0369A1]">
                <th className="px-4 py-2.5 text-left">参数</th>
                <th className="px-4 py-2.5 text-left">位置</th>
                <th className="px-4 py-2.5 text-left">类型</th>
                <th className="px-4 py-2.5 text-left">说明</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-[#BAE6FD]/70">
              {api.params.map(([name, place, type, note]) => (
                <tr key={name}>
                  <td className="px-4 py-3 font-mono text-xs text-ink-primary">{name}</td>
                  <td className="px-4 py-3 font-mono text-xs text-ink-secondary">{place}</td>
                  <td className="px-4 py-3 font-mono text-xs text-ink-secondary">{type}</td>
                  <td className="px-4 py-3 text-ink-secondary">{note}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <div className="grid gap-5 xl:grid-cols-3">
          <CodePanel title="调用示例" code={api.example} />
          <CodePanel title="成功响应" code={api.success} />
          <CodePanel title="错误响应" code={api.error} />
        </div>
      </div>
    </div>
  );
}

function InfoBlock({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-lg border border-[#BAE6FD]/80 bg-[#F8FCFF] p-4">
      <p className="text-[11px] font-semibold uppercase tracking-[0.08em] text-[#0369A1]">{label}</p>
      <p className="mt-1 text-sm leading-6 text-ink-secondary">{value}</p>
    </div>
  );
}

function CodePanel({ title, code }: { title: string; code: string }) {
  return (
    <div className="overflow-hidden rounded-lg border border-[#0EA5E9]/24 bg-white/86 shadow-panel">
      <div className="border-b border-[#BAE6FD]/80 bg-[linear-gradient(90deg,rgba(14,165,233,0.12),rgba(79,70,229,0.08),rgba(255,255,255,0.76))] px-5 py-4">
        <h2 className="text-base font-semibold text-ink-primary">{title}</h2>
      </div>
      <pre className="overflow-x-auto bg-[#0F172A] p-4 text-xs leading-6 text-[#E2E8F0]">
        <code>{code}</code>
      </pre>
    </div>
  );
}
