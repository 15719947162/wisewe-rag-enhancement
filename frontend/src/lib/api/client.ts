import type {
  AnswerResult,
  AlertItem,
  AuditLogRecord,
  AiBaseSsoConfig,
  ApiKeyRecord,
  AuthSessionPayload,
  ConsoleIngestionTasksPayload,
  DocumentDetail,
  DocumentGraphPayload,
  DocumentRecord,
  EvaluationRecord,
  ChunkDraftRecord,
  IngestionTask,
  KnowledgeBase,
  LatestIngestionLogPayload,
  OverviewMetric,
  OpenApiAppRecord,
  QueryLogRecord,
  QueueItem,
  SettingsGroup,
  IdentitySnapshotUsersPayload,
  IdentitySyncLogRecord,
  TokenUsageSummary,
} from "@/lib/contracts/types";
import { getIdentityHeaders } from "@/lib/auth/identity";

export function getBaseUrl(): string {
  const publicBaseUrl = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://127.0.0.1:8000";
  const internalBaseUrl = process.env.INTERNAL_API_BASE_URL ?? publicBaseUrl;
  return typeof window === "undefined" ? internalBaseUrl : publicBaseUrl;
}

function resolveDetailSuffix(payload: { detail?: string } | null | undefined): string {
  return payload?.detail ? ` - ${payload.detail}` : "";
}

function resolveDownloadFilename(contentDisposition: string | null, fallbackName: string): string {
  if (contentDisposition) {
    const encodedMatch = contentDisposition.match(/filename\*=UTF-8''([^;]+)/i);
    if (encodedMatch?.[1]) {
      try {
        return decodeURIComponent(encodedMatch[1]);
      } catch {
        return fallbackName;
      }
    }

    const plainMatch = contentDisposition.match(/filename="([^"]+)"/i);
    if (plainMatch?.[1]) {
      return plainMatch[1];
    }
  }
  return fallbackName;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const headers = new Headers(init?.headers);
  headers.set("Content-Type", headers.get("Content-Type") ?? "application/json");
  for (const [key, value] of Object.entries(getIdentityHeaders())) {
    headers.set(key, value);
  }

  const response = await fetch(`${getBaseUrl()}${path}`, {
    ...init,
    headers,
    credentials: "include",
    cache: "no-store",
  });

  if (!response.ok) {
    let detail = "";
    try {
      const payload = (await response.json()) as { detail?: string };
      detail = resolveDetailSuffix(payload);
    } catch {
      detail = "";
    }
    throw new Error(`请求失败：${response.status}${detail}`);
  }

  return (await response.json()) as T;
}

export async function getOverviewMetrics(): Promise<OverviewMetric[]> {
  return request<OverviewMetric[]>("/api/console/overview-metrics");
}

export async function getAlerts() {
  return request<AlertItem[]>("/api/console/alerts");
}

export async function getQueueItems(): Promise<QueueItem[]> {
  return request<QueueItem[]>("/api/console/queue");
}

export async function getKnowledgeBases(): Promise<KnowledgeBase[]> {
  return request<KnowledgeBase[]>("/api/knowledge-bases");
}

export async function getDocuments(kbId?: string): Promise<DocumentRecord[]> {
  const query = kbId ? `?kb_id=${encodeURIComponent(kbId)}` : "";
  return request<DocumentRecord[]>(`/api/documents${query}`);
}

export async function getDocumentDetail(documentId: string): Promise<DocumentDetail> {
  return request<DocumentDetail>(`/api/documents/${encodeURIComponent(documentId)}`);
}

export async function getDocumentGraph(documentId: string): Promise<DocumentGraphPayload> {
  return request<DocumentGraphPayload>(`/api/documents/${encodeURIComponent(documentId)}/graph`);
}

export async function getKnowledgeBaseGraph(kbId: string): Promise<DocumentGraphPayload> {
  return request<DocumentGraphPayload>(`/api/knowledge-bases/${encodeURIComponent(kbId)}/graph`);
}

export async function deleteDocument(documentId: string): Promise<{ deleted: boolean; document_id: string }> {
  return request<{ deleted: boolean; document_id: string }>(`/api/documents/${encodeURIComponent(documentId)}`, {
    method: "DELETE",
  });
}

export async function getIngestionTasks(kbId?: string): Promise<IngestionTask[]> {
  const query = kbId ? `?kb_id=${encodeURIComponent(kbId)}` : "";
  return request<IngestionTask[]>(`/api/ingestion/tasks${query}`);
}

export type ConsoleIngestionTaskFilters = {
  keyword?: string;
  status?: string;
  strategy?: string;
  page?: number;
  pageSize?: number;
};

export async function getConsoleIngestionTasks(
  filters: ConsoleIngestionTaskFilters = {},
): Promise<ConsoleIngestionTasksPayload> {
  const params = new URLSearchParams();
  if (filters.keyword) params.set("keyword", filters.keyword);
  if (filters.status) params.set("status", filters.status);
  if (filters.strategy) params.set("strategy", filters.strategy);
  params.set("page", String(filters.page ?? 1));
  params.set("page_size", String(filters.pageSize ?? 20));
  const query = params.toString();
  return request<ConsoleIngestionTasksPayload>(`/api/console/ingestion-tasks${query ? `?${query}` : ""}`);
}

export async function getLatestIngestionLog(kbId?: string, maxLines = 500): Promise<LatestIngestionLogPayload> {
  const params = new URLSearchParams();
  if (kbId) params.set("kb_id", kbId);
  params.set("max_lines", String(maxLines));
  return request<LatestIngestionLogPayload>(`/api/console/ingestion-logs/latest?${params.toString()}`);
}

export async function getAnswerPreview(
  query = "系统如何保证答案证据可追溯？",
): Promise<AnswerResult> {
  return request<AnswerResult>("/api/rag/query", {
    method: "POST",
    body: JSON.stringify({
      query,
      kb_id: "default",
      top_k: 8,
      min_score: 0.3,
      use_llm_check: false,
      use_llm_score: false,
    }),
  });
}

export async function getEvaluations(kbId?: string): Promise<EvaluationRecord[]> {
  const query = kbId ? `?kb_id=${encodeURIComponent(kbId)}` : "";
  return request<EvaluationRecord[]>(`/api/console/evaluations${query}`);
}

export type QueryLogFilters = {
  kbId?: string;
  requestId?: string;
  actorId?: string;
  apiKeyId?: string;
  pipelineDomain?: string;
  startAt?: string;
  endAt?: string;
  limit?: number;
};

export type AuditLogFilters = {
  actorId?: string;
  action?: string;
  resourceType?: string;
  resourceId?: string;
  requestId?: string;
  kbId?: string;
  outcome?: string;
  startAt?: string;
  endAt?: string;
  limit?: number;
};

function buildQueryLogParams(filters: QueryLogFilters = {}, defaultLimit = 50): URLSearchParams {
  const params = new URLSearchParams();
  if (filters.kbId) params.set("kb_id", filters.kbId);
  if (filters.requestId) params.set("request_id", filters.requestId);
  if (filters.actorId) params.set("actor_id", filters.actorId);
  if (filters.apiKeyId) params.set("api_key_id", filters.apiKeyId);
  if (filters.pipelineDomain) params.set("pipeline_domain", filters.pipelineDomain);
  if (filters.startAt) params.set("start_at", filters.startAt);
  if (filters.endAt) params.set("end_at", filters.endAt);
  params.set("limit", String(filters.limit ?? defaultLimit));
  return params;
}

function buildAuditLogParams(filters: AuditLogFilters = {}, defaultLimit = 50): URLSearchParams {
  const params = new URLSearchParams();
  if (filters.actorId) params.set("actor_id", filters.actorId);
  if (filters.action) params.set("action", filters.action);
  if (filters.resourceType) params.set("resource_type", filters.resourceType);
  if (filters.resourceId) params.set("resource_id", filters.resourceId);
  if (filters.requestId) params.set("request_id", filters.requestId);
  if (filters.kbId) params.set("kb_id", filters.kbId);
  if (filters.outcome) params.set("outcome", filters.outcome);
  if (filters.startAt) params.set("start_at", filters.startAt);
  if (filters.endAt) params.set("end_at", filters.endAt);
  params.set("limit", String(filters.limit ?? defaultLimit));
  return params;
}

export async function getQueryLogs(filters: QueryLogFilters = {}): Promise<QueryLogRecord[]> {
  const params = buildQueryLogParams(filters, 50);
  const query = params.toString();
  return request<QueryLogRecord[]>(`/api/console/query-logs${query ? `?${query}` : ""}`);
}

export async function getAuditLogs(filters: AuditLogFilters = {}): Promise<AuditLogRecord[]> {
  const params = buildAuditLogParams(filters, 50);
  const query = params.toString();
  return request<AuditLogRecord[]>(`/api/console/audit-logs${query ? `?${query}` : ""}`);
}

export async function getIdentitySyncLogs(limit = 100): Promise<IdentitySyncLogRecord[]> {
  return request<IdentitySyncLogRecord[]>(`/api/console/identity-sync-logs?limit=${encodeURIComponent(limit)}`);
}

export async function syncIdentityDelta(lastSyncAt?: string): Promise<{
  mode: string;
  lastSyncAt: string;
  maxUpdatedAt: string;
  snapshotVersion: string;
  generatedAt?: string;
  hasMore: boolean;
  counts: {
    tenants: number;
    users: number;
    roles: number;
    user_roles: number;
    deleted: number;
  };
}> {
  const query = lastSyncAt ? `?last_sync_at=${encodeURIComponent(lastSyncAt)}` : "";
  return request(`/api/identity/sync-delta${query}`, {
    method: "POST",
  });
}

export async function downloadQueryLogsCsv(filters: QueryLogFilters = {}): Promise<void> {
  const params = buildQueryLogParams(filters, 10000);
  const query = params.toString();
  const response = await fetch(`${getBaseUrl()}/api/console/query-logs/export.csv${query ? `?${query}` : ""}`, {
    method: "GET",
    headers: getIdentityHeaders(),
    credentials: "include",
    cache: "no-store",
  });

  if (!response.ok) {
    let detail = "";
    try {
      const payload = (await response.json()) as { detail?: string };
      detail = resolveDetailSuffix(payload);
    } catch {
      detail = "";
    }
    throw new Error(`导出失败：${response.status}${detail}`);
  }

  const blob = await response.blob();
  const filename = resolveDownloadFilename(response.headers.get("Content-Disposition"), "rag-query-logs.csv");
  const url = window.URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  window.URL.revokeObjectURL(url);
}

export async function getTokenUsage(limit = 10, pipelineDomain?: string): Promise<TokenUsageSummary> {
  const params = new URLSearchParams();
  params.set("limit", String(limit));
  if (pipelineDomain) params.set("pipeline_domain", pipelineDomain);
  return request<TokenUsageSummary>(`/api/console/token-usage?${params.toString()}`);
}

export async function getApiKeys(): Promise<ApiKeyRecord[]> {
  return request<ApiKeyRecord[]>("/api/console/api-keys");
}

export async function getOpenApiApps(): Promise<OpenApiAppRecord[]> {
  return request<OpenApiAppRecord[]>("/api/console/openapi-apps");
}

export async function createOpenApiApp(data: {
  name: string;
  note?: string;
}): Promise<OpenApiAppRecord> {
  return request<OpenApiAppRecord>("/api/console/openapi-apps", {
    method: "POST",
    body: JSON.stringify(data),
  });
}

export async function updateOpenApiApp(
  id: string,
  data: Partial<{
    name: string;
    status: "active" | "disabled";
    note: string;
  }>,
): Promise<OpenApiAppRecord> {
  return request<OpenApiAppRecord>(`/api/console/openapi-apps/${encodeURIComponent(id)}`, {
    method: "PATCH",
    body: JSON.stringify(data),
  });
}

export async function deleteOpenApiApp(id: string): Promise<{ deleted: boolean; id: string }> {
  return request<{ deleted: boolean; id: string }>(`/api/console/openapi-apps/${encodeURIComponent(id)}`, {
    method: "DELETE",
  });
}

export async function createApiKey(data: {
  appId?: string | null;
  name: string;
  kbIds: string[];
  capabilities: string[];
  requireSignature?: boolean;
  allowedIps?: string[];
  rpmLimit?: number;
  dailyRequestLimit?: number;
  note?: string;
  expiresAt?: string | null;
}): Promise<ApiKeyRecord> {
  return request<ApiKeyRecord>("/api/console/api-keys", {
    method: "POST",
    body: JSON.stringify(data),
  });
}

export async function updateApiKey(
  id: string,
  data: Partial<{
    name: string;
    status: "active" | "disabled";
    appId: string | null;
    kbIds: string[];
    capabilities: string[];
    requireSignature: boolean;
    allowedIps: string[];
    rpmLimit: number;
    dailyRequestLimit: number;
    note: string;
    expiresAt: string | null;
  }>,
): Promise<ApiKeyRecord> {
  return request<ApiKeyRecord>(`/api/console/api-keys/${encodeURIComponent(id)}`, {
    method: "PATCH",
    body: JSON.stringify(data),
  });
}

export async function rotateApiKey(id: string): Promise<ApiKeyRecord> {
  return request<ApiKeyRecord>(`/api/console/api-keys/${encodeURIComponent(id)}/rotate`, {
    method: "POST",
  });
}

export async function deleteApiKey(id: string): Promise<{ deleted: boolean; id: string }> {
  return request<{ deleted: boolean; id: string }>(`/api/console/api-keys/${encodeURIComponent(id)}`, {
    method: "DELETE",
  });
}

export async function getSettingsGroups(): Promise<SettingsGroup[]> {
  return request<SettingsGroup[]>("/api/console/settings");
}

export async function getIdentitySnapshotUsers(limit = 10): Promise<IdentitySnapshotUsersPayload> {
  return request<IdentitySnapshotUsersPayload>(`/api/identity/snapshot-users?limit=${encodeURIComponent(limit)}`);
}

export async function removeIdentitySnapshotData(): Promise<{
  deleted: {
    userRoles: number;
    users: number;
    roles: number;
    tenants: number;
    syncRuns: number;
  };
}> {
  return request("/api/identity/snapshot-data", {
    method: "DELETE",
  });
}

export async function getAiBaseSsoConfig(): Promise<AiBaseSsoConfig> {
  return request<AiBaseSsoConfig>("/api/auth/ai-base/config");
}

export function getAiBaseSsoLaunchUrl(nextPath = "/knowledge-bases"): string {
  return `${getBaseUrl()}${`/api/auth/ai-base/launch?next=${encodeURIComponent(nextPath)}`}`;
}

export async function getAuthSession(): Promise<AuthSessionPayload | null> {
  try {
    const response = await fetch(`${getBaseUrl()}/api/auth/session`, {
      method: "GET",
      credentials: "include",
      cache: "no-store",
    });
    if (!response.ok) return null;
    return (await response.json()) as AuthSessionPayload;
  } catch {
    return null;
  }
}

export async function exchangeAiBaseJwt(jwt: string): Promise<{ identity: IdentitySnapshotUsersPayload["users"][number]; expiresAt: string; mode: string }> {
  return request("/api/auth/ai-base/exchange", {
    method: "POST",
    body: JSON.stringify({ jwt }),
  });
}

export async function logoutAuthSession(): Promise<{ ok: boolean; revoked: boolean }> {
  return request("/api/auth/logout", {
    method: "POST",
  });
}

// ── T5: 新增端点 ──────────────────────────────────────────

export async function createKnowledgeBase(data: {
  name: string;
  description?: string;
  strategy?: string;
}): Promise<KnowledgeBase> {
  return request<KnowledgeBase>("/api/knowledge-bases", {
    method: "POST",
    body: JSON.stringify({ description: "", strategy: "hierarchical", ...data }),
  });
}

export async function updateKnowledgeBase(
  kbId: string,
  data: {
    name: string;
    description?: string;
    strategy?: string;
  },
): Promise<KnowledgeBase> {
  return request<KnowledgeBase>(`/api/knowledge-bases/${encodeURIComponent(kbId)}`, {
    method: "PUT",
    body: JSON.stringify({ description: "", strategy: "hierarchical", ...data }),
  });
}

export async function deleteKnowledgeBase(kbId: string): Promise<{ deleted: boolean; kb_id: string }> {
  return request<{ deleted: boolean; kb_id: string }>(`/api/knowledge-bases/${encodeURIComponent(kbId)}`, {
    method: "DELETE",
  });
}

export async function uploadDocument(
  file: File,
  kbId: string,
  strategy: string,
  subjectType: string = "general",
  layoutType: string = "single_column",
): Promise<{ task_id: string; status: string; filename: string }> {
  const form = new FormData();
  form.append("file", file);
  const url = `${getBaseUrl()}/api/ingestion/upload?kb_id=${encodeURIComponent(kbId)}&strategy=${encodeURIComponent(strategy)}&subject_type=${encodeURIComponent(subjectType)}&layout_type=${encodeURIComponent(layoutType)}`;
  const res = await fetch(url, {
    method: "POST",
    body: form,
    headers: getIdentityHeaders(),
    credentials: "include",
    cache: "no-store",
  });
  if (!res.ok) {
    let detail = "";
    try {
      const payload = (await res.json()) as { detail?: string };
      detail = resolveDetailSuffix(payload);
    } catch {
      detail = "";
    }
    throw new Error(`上传失败：${res.status}${detail}`);
  }
  return res.json();
}

export async function getIngestionTask(taskId: string): Promise<IngestionTask> {
  return request<IngestionTask>(`/api/ingestion/tasks/${encodeURIComponent(taskId)}`);
}

export async function deleteIngestionTask(taskId: string): Promise<{
  deleted: boolean;
  task_id: string;
  removed: {
    sourceFile: boolean;
    logFile: boolean;
    chunkDrafts: number;
    taskRecord: boolean;
  };
}> {
  return request(`/api/ingestion/tasks/${encodeURIComponent(taskId)}`, {
    method: "DELETE",
  });
}

export async function getChunkDrafts(taskId: string): Promise<{ taskId: string; status: string; items: ChunkDraftRecord[]; count: number }> {
  return request(`/api/ingestion/chunks/preview/${encodeURIComponent(taskId)}`);
}

export async function updateChunkDraft(draftId: string, content: string): Promise<ChunkDraftRecord> {
  return request(`/api/ingestion/chunks/${encodeURIComponent(draftId)}`, {
    method: "PUT",
    body: JSON.stringify({ content }),
  });
}

export async function deleteChunkDraft(draftId: string): Promise<{ deleted: boolean; draftId: string }> {
  return request(`/api/ingestion/chunks/${encodeURIComponent(draftId)}`, {
    method: "DELETE",
  });
}

export async function mergeChunkDrafts(taskId: string, draftIds: string[]): Promise<ChunkDraftRecord> {
  return request("/api/ingestion/chunks/merge", {
    method: "POST",
    body: JSON.stringify({ task_id: taskId, draft_ids: draftIds }),
  });
}

export async function confirmChunkDrafts(taskId: string): Promise<IngestionTask> {
  return request(`/api/ingestion/chunks/confirm/${encodeURIComponent(taskId)}`, {
    method: "POST",
  });
}

export async function getDashboardStats(): Promise<{
  kb_count: number;
  doc_count: number;
  chunk_count: number;
  recent_tasks: Array<{ id: string; kbId: string; documentName: string; status: string; updatedAt: string }>;
}> {
  return request("/api/dashboard/stats");
}

export async function downloadDocumentCsv(documentId: string, fallbackFilename: string): Promise<void> {
  const response = await fetch(`${getBaseUrl()}/api/documents/${encodeURIComponent(documentId)}/export.csv`, {
    method: "GET",
    headers: getIdentityHeaders(),
    credentials: "include",
    cache: "no-store",
  });

  if (!response.ok) {
    let detail = "";
    try {
      const payload = (await response.json()) as { detail?: string };
      detail = resolveDetailSuffix(payload);
    } catch {
      detail = "";
    }
    throw new Error(`导出失败：${response.status}${detail}`);
  }

  const blob = await response.blob();
  const filename = resolveDownloadFilename(
    response.headers.get("Content-Disposition"),
    `${fallbackFilename.replace(/\.[^.]+$/, "") || "document"}-chunks.csv`,
  );
  const url = window.URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  window.URL.revokeObjectURL(url);
}

export async function downloadDocumentSource(documentId: string): Promise<void> {
  const response = await fetch(`${getBaseUrl()}/api/documents/${encodeURIComponent(documentId)}/source`, {
    method: "GET",
    headers: getIdentityHeaders(),
    credentials: "include",
    cache: "no-store",
  });

  if (!response.ok) {
    let detail = "";
    try {
      const payload = (await response.json()) as { detail?: string };
      detail = resolveDetailSuffix(payload);
    } catch {
      detail = "";
    }
    throw new Error(`下载失败：${response.status}${detail}`);
  }

  const blob = await response.blob();
  const filename = resolveDownloadFilename(response.headers.get("Content-Disposition"), `${documentId}.pdf`);
  const url = window.URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  window.URL.revokeObjectURL(url);
}

export async function updateSettings(
  data: Record<string, string>,
): Promise<{ updated: string[]; count: number }> {
  return request("/api/console/settings", {
    method: "PUT",
    body: JSON.stringify(data),
  });
}
