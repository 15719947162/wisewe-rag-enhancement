"use client";

import { useEffect, useMemo, useState } from "react";
import { ContextRail } from "@/components/layout/context-rail";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { EmptyState } from "@/components/ui/empty-state";
import { Input } from "@/components/ui/input";
import { LoadingOverlay, LoadingRows } from "@/components/ui/skeleton";
import { TablePagination, useClientPagination } from "@/components/ui/table-pagination";
import { InfoTooltip } from "@/components/ui/info-tooltip";
import { getSelectedIdentity, IDENTITY_CHANGED_EVENT } from "@/lib/auth/identity";
import { getAuditLogs, getIdentitySyncLogs, getLatestIngestionLog, getQueryLogs, syncIdentityDelta, type AuditLogFilters, type QueryLogFilters } from "@/lib/api/client";
import type { AuditLogRecord, IdentitySyncLogRecord, LatestIngestionLogPayload, QueryLogRecord } from "@/lib/contracts/types";
import { formatLatency, formatNumber, formatScore, formatTimestamp } from "@/lib/formatters";

type LogTab = "query" | "ingestion" | "identity-sync" | "audit";

const pipelineOptions = [
  { value: "", label: "全部链路" },
  { value: "online_rag", label: "在线问答" },
  { value: "graph_rag", label: "Graph RAG 问答" },
  { value: "openapi", label: "OpenAPI" },
  { value: "evaluation", label: "评测" },
  { value: "ingestion", label: "文档入库" },
];

const limitOptions = [20, 50, 100];

function statusVariant(status: string, cannotAnswer = false) {
  if (status === "success" && !cannotAnswer) return "success";
  if (cannotAnswer || status === "warning") return "warning";
  if (status === "failed" || status === "error") return "danger";
  return "neutral";
}

function riskVariant(risk: string) {
  if (risk === "high") return "danger";
  if (risk === "medium") return "warning";
  return "neutral";
}

function normalizeQueryFilters(filters: QueryLogFilters): QueryLogFilters {
  return {
    kbId: filters.kbId?.trim() || undefined,
    requestId: filters.requestId?.trim() || undefined,
    actorId: filters.actorId?.trim() || undefined,
    apiKeyId: filters.apiKeyId?.trim() || undefined,
    pipelineDomain: filters.pipelineDomain || undefined,
    startAt: filters.startAt || undefined,
    endAt: filters.endAt || undefined,
    limit: filters.limit ?? 100,
  };
}

function normalizeAuditFilters(filters: AuditLogFilters): AuditLogFilters {
  return {
    actorId: filters.actorId?.trim() || undefined,
    action: filters.action?.trim() || undefined,
    resourceType: filters.resourceType?.trim() || undefined,
    resourceId: filters.resourceId?.trim() || undefined,
    requestId: filters.requestId?.trim() || undefined,
    kbId: filters.kbId?.trim() || undefined,
    outcome: filters.outcome || undefined,
    startAt: filters.startAt || undefined,
    endAt: filters.endAt || undefined,
    limit: filters.limit ?? 100,
  };
}

export default function LogsPage() {
  const [activeTab, setActiveTab] = useState<LogTab>("query");
  const [queryFilters, setQueryFilters] = useState<QueryLogFilters>({ limit: 100 });
  const [auditFilters, setAuditFilters] = useState<AuditLogFilters>({ limit: 100 });
  const [queryLogs, setQueryLogs] = useState<QueryLogRecord[]>([]);
  const [auditLogs, setAuditLogs] = useState<AuditLogRecord[]>([]);
  const [identitySyncLogs, setIdentitySyncLogs] = useState<IdentitySyncLogRecord[]>([]);
  const [ingestionLog, setIngestionLog] = useState<LatestIngestionLogPayload | null>(null);
  const [currentIdentity, setCurrentIdentity] = useState(() => getSelectedIdentity());
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  async function loadQueryLogs(nextFilters = queryFilters) {
    setLoading(true);
    try {
      setQueryLogs(await getQueryLogs(normalizeQueryFilters(nextFilters)));
      setError(null);
    } catch (err) {
      setQueryLogs([]);
      setError(err instanceof Error ? err.message : "加载查询日志失败");
    } finally {
      setLoading(false);
    }
  }

  async function loadAuditLogs(nextFilters = auditFilters) {
    setLoading(true);
    try {
      setAuditLogs(await getAuditLogs(normalizeAuditFilters(nextFilters)));
      setError(null);
    } catch (err) {
      setAuditLogs([]);
      setError(err instanceof Error ? err.message : "加载审计日志失败");
    } finally {
      setLoading(false);
    }
  }

  async function loadIngestionLog() {
    setLoading(true);
    try {
      setIngestionLog(await getLatestIngestionLog(undefined, 500));
      setError(null);
    } catch (err) {
      setIngestionLog(null);
      setError(err instanceof Error ? err.message : "加载入库日志失败");
    } finally {
      setLoading(false);
    }
  }

  async function loadIdentitySyncLogs() {
    setLoading(true);
    try {
      setIdentitySyncLogs(await getIdentitySyncLogs(100));
      setError(null);
    } catch (err) {
      setIdentitySyncLogs([]);
      setError(err instanceof Error ? err.message : "加载用户及权限同步日志失败");
    } finally {
      setLoading(false);
    }
  }

  async function runIdentitySyncDelta() {
    setLoading(true);
    try {
      await syncIdentityDelta();
      setIdentitySyncLogs(await getIdentitySyncLogs(100));
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "触发用户及权限同步失败");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    loadQueryLogs({ limit: 100 });
  }, []);

  useEffect(() => {
    const refreshIdentity = () => setCurrentIdentity(getSelectedIdentity());
    refreshIdentity();
    window.addEventListener(IDENTITY_CHANGED_EVENT, refreshIdentity);
    window.addEventListener("storage", refreshIdentity);
    return () => {
      window.removeEventListener(IDENTITY_CHANGED_EVENT, refreshIdentity);
      window.removeEventListener("storage", refreshIdentity);
    };
  }, []);

  function switchTab(tab: LogTab) {
    setActiveTab(tab);
    if (tab === "query") void loadQueryLogs();
    if (tab === "audit") void loadAuditLogs();
    if (tab === "ingestion") void loadIngestionLog();
    if (tab === "identity-sync") void loadIdentitySyncLogs();
  }

  const stats = useMemo(() => {
    const totalTokens = queryLogs.reduce((sum, item) => sum + item.totalTokens, 0);
    const failed = queryLogs.filter((item) => item.status !== "success").length;
    const highRisk = auditLogs.filter((item) => item.riskLevel === "high").length;
    const syncFailed = identitySyncLogs.filter((item) => item.status !== "success").length;
    return { totalTokens, failed, highRisk, syncFailed };
  }, [queryLogs, auditLogs, identitySyncLogs]);

  const queryPagination = useClientPagination(queryLogs, 20);
  const auditPagination = useClientPagination(auditLogs, 20);
  const identitySyncPagination = useClientPagination(identitySyncLogs, 20);
  const canRunIdentitySync =
    Boolean(currentIdentity?.source?.startsWith("ai_base_sso_")) &&
    Boolean(currentIdentity?.roleCodes?.includes("superManager"));

  return (
    <div className="space-y-6">
      <ContextRail
        title="日志管理"
        description="按用途拆分查询日志、入库日志和审计日志；模型调用与 Token 明细继续在 Token 统计中查看。"
        showGlobalHint={false}
      />

      <section className="grid gap-4 md:grid-cols-4">
        <MetricCard label="查询日志" value={formatNumber(queryLogs.length)} helper="RAG / OpenAPI 脱敏请求记录" />
        <MetricCard label="Token 合计" value={formatNumber(stats.totalTokens)} helper="来自当前查询日志筛选范围" />
        <MetricCard label="失败请求" value={formatNumber(stats.failed)} helper="status 不是 success" />
        <MetricCard label="同步失败" value={formatNumber(stats.syncFailed)} helper="用户及权限同步失败记录" />
      </section>

      <div className="flex flex-wrap gap-2 rounded-lg border border-[#FECDD3]/80 bg-white/80 p-2 shadow-sm">
        <TabButton active={activeTab === "query"} onClick={() => switchTab("query")}>查询日志</TabButton>
        <TabButton active={activeTab === "ingestion"} onClick={() => switchTab("ingestion")}>入库日志</TabButton>
        <TabButton active={activeTab === "identity-sync"} onClick={() => switchTab("identity-sync")}>用户及权限同步</TabButton>
        <TabButton active={activeTab === "audit"} onClick={() => switchTab("audit")}>审计日志</TabButton>
      </div>

      {error && <div className="rounded-sm border border-status-danger bg-[#FEF2F2] px-4 py-3 text-sm text-status-danger">{error}</div>}

      {activeTab === "query" && (
        <QueryLogPanel
          filters={queryFilters}
          setFilters={setQueryFilters}
          logs={queryLogs}
          loading={loading}
          pagination={queryPagination}
          onQuery={() => loadQueryLogs(queryFilters)}
          onReset={() => {
            const next = { limit: 100 };
            setQueryFilters(next);
            void loadQueryLogs(next);
          }}
        />
      )}

      {activeTab === "ingestion" && (
        <IngestionLogPanel payload={ingestionLog} loading={loading} onRefresh={loadIngestionLog} />
      )}

      {activeTab === "identity-sync" && (
        <IdentitySyncLogPanel
          logs={identitySyncLogs}
          loading={loading}
          pagination={identitySyncPagination}
          canSync={canRunIdentitySync}
          onRefresh={loadIdentitySyncLogs}
          onSync={runIdentitySyncDelta}
        />
      )}

      {activeTab === "audit" && (
        <AuditLogPanel
          filters={auditFilters}
          setFilters={setAuditFilters}
          logs={auditLogs}
          loading={loading}
          pagination={auditPagination}
          onQuery={() => loadAuditLogs(auditFilters)}
          onReset={() => {
            const next = { limit: 100 };
            setAuditFilters(next);
            void loadAuditLogs(next);
          }}
        />
      )}
    </div>
  );
}

function QueryLogPanel({
  filters,
  setFilters,
  logs,
  loading,
  pagination,
  onQuery,
  onReset,
}: {
  filters: QueryLogFilters;
  setFilters: React.Dispatch<React.SetStateAction<QueryLogFilters>>;
  logs: QueryLogRecord[];
  loading: boolean;
  pagination: ReturnType<typeof useClientPagination<QueryLogRecord>>;
  onQuery: () => void;
  onReset: () => void;
}) {
  return (
    <>
      <section className="overflow-hidden rounded-lg border border-[#E11D48]/24 bg-white/86 shadow-panel">
        <PanelHeader eyebrow="Query Logs" title="查询日志筛选" description="只展示脱敏摘要、requestId、链路、Token、延迟和评分，不展示完整 prompt、答案或文档正文。" />
        <div className="grid gap-3 bg-white/62 px-5 py-4 md:grid-cols-3 xl:grid-cols-6">
          <Input inputSize="sm" placeholder="知识库 ID" value={filters.kbId ?? ""} onChange={(e) => setFilters((prev) => ({ ...prev, kbId: e.target.value }))} />
          <Input inputSize="sm" placeholder="requestId" value={filters.requestId ?? ""} onChange={(e) => setFilters((prev) => ({ ...prev, requestId: e.target.value }))} />
          <Input inputSize="sm" placeholder="调用方 ID" value={filters.actorId ?? ""} onChange={(e) => setFilters((prev) => ({ ...prev, actorId: e.target.value }))} />
          <Input inputSize="sm" placeholder="API Key ID" value={filters.apiKeyId ?? ""} onChange={(e) => setFilters((prev) => ({ ...prev, apiKeyId: e.target.value }))} />
          <Input inputSize="sm" type="datetime-local" aria-label="开始时间" value={filters.startAt ?? ""} onChange={(e) => setFilters((prev) => ({ ...prev, startAt: e.target.value }))} />
          <Input inputSize="sm" type="datetime-local" aria-label="结束时间" value={filters.endAt ?? ""} onChange={(e) => setFilters((prev) => ({ ...prev, endAt: e.target.value }))} />
          <Select value={filters.pipelineDomain ?? ""} onChange={(value) => setFilters((prev) => ({ ...prev, pipelineDomain: value }))} options={pipelineOptions} />
          <Select value={String(filters.limit ?? 100)} onChange={(value) => setFilters((prev) => ({ ...prev, limit: Number(value) }))} options={limitOptions.map((item) => ({ value: String(item), label: `最近 ${item} 条` }))} />
        </div>
        <PanelActions onReset={onReset} onQuery={onQuery} loading={loading} />
      </section>

      <section className="overflow-hidden rounded-lg border border-[#E11D48]/24 bg-white/86 shadow-panel">
        <PanelHeader eyebrow="Requests" title="查询记录" />
        <LoadingOverlay active={loading && logs.length > 0} tone="rose" label="正在刷新查询日志" />
        {loading && logs.length === 0 ? (
          <LoadingRows rows={6} />
        ) : logs.length === 0 ? (
          <EmptyState title="暂无查询日志" description="运行在线问答或 OpenAPI 查询后，这里会显示脱敏后的请求记录。" />
        ) : (
          <div className="overflow-x-auto animate-data-enter">
            <table className="w-full min-w-[1120px] text-sm">
              <thead>
                <tr className="border-b border-[#FECDD3]/80 bg-[#FFF0F4] text-[12px] font-medium uppercase tracking-[0.06em] text-[#BE123C]">
                  <th className="px-4 py-2.5 text-left">时间 / requestId</th>
                  <th className="px-4 py-2.5 text-left">链路</th>
                  <th className="px-4 py-2.5 text-left">知识库 / 身份</th>
                  <th className="px-4 py-2.5 text-left">查询摘要</th>
                  <th className="px-4 py-2.5 text-right">Token</th>
                  <th className="px-4 py-2.5 text-right">延迟</th>
                  <th className="px-4 py-2.5 text-right">评分</th>
                  <th className="px-4 py-2.5 text-left">状态</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-[#FECDD3]/70">
                {pagination.pageItems.map((item) => (
                  <tr key={item.requestId} className="transition-colors hover:bg-[#FFF0F4]/70">
                    <td className="px-4 py-3 align-top">
                      <p className="font-medium text-ink-primary">{formatTimestamp(item.createdAt)}</p>
                      <p className="mt-1 max-w-[180px] truncate font-mono text-xs text-ink-tertiary">{item.requestId}</p>
                    </td>
                    <td className="px-4 py-3 align-top">
                      <p className="font-medium text-ink-primary">{item.pipelineDomain || "-"}</p>
                      <p className="mt-1 text-xs text-ink-tertiary">{item.pipelineStage || "query"}</p>
                    </td>
                    <td className="px-4 py-3 align-top">
                      <p className="font-mono text-xs text-ink-primary">{item.kbId}</p>
                      <p className="mt-1 text-xs text-ink-tertiary">{item.actorId || "console"} / {item.apiKeyId || "无 API Key"}</p>
                    </td>
                    <td className="max-w-[340px] px-4 py-3 align-top">
                      <p className="line-clamp-2 text-ink-primary">{item.querySummary || "未记录查询摘要"}</p>
                      <p className="mt-1 line-clamp-1 text-xs text-ink-tertiary">{item.answerSummary || "未记录答案摘要"}</p>
                    </td>
                    <td className="px-4 py-3 text-right font-mono align-top text-ink-primary">
                      {formatNumber(item.totalTokens)}
                      <p className="mt-1 text-xs text-ink-tertiary">{formatNumber(item.promptTokens)} + {formatNumber(item.completionTokens)}</p>
                    </td>
                    <td className="px-4 py-3 text-right font-mono align-top text-ink-primary">{formatLatency(item.latencyMs)}</td>
                    <td className="px-4 py-3 text-right font-mono align-top text-ink-primary">
                      {formatScore(item.relevanceScore)}
                      <p className="mt-1 text-xs text-ink-tertiary">{formatScore(item.faithfulnessScore)}</p>
                    </td>
                    <td className="px-4 py-3 align-top">
                      <Badge variant={statusVariant(item.status, item.cannotAnswer)} dot>{item.cannotAnswer ? "资料不足" : item.status}</Badge>
                      {item.errorCode && <p className="mt-1 text-xs text-status-danger">{item.errorCode}</p>}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            <PaginationControls pagination={pagination} />
          </div>
        )}
      </section>
    </>
  );
}

function IngestionLogPanel({ payload, loading, onRefresh }: { payload: LatestIngestionLogPayload | null; loading: boolean; onRefresh: () => void }) {
  return (
    <section className="overflow-hidden rounded-lg border border-[#E11D48]/24 bg-white/86 shadow-panel">
      <PanelHeader eyebrow="Ingestion" title="最新入库日志" description="用于排查最近一次入库任务的技术日志；任务运行态和阶段耗时请优先看入库管理页。" />
      <div className="flex justify-end border-b border-[#FECDD3]/80 px-5 py-3">
        <Button size="sm" variant="primary" onClick={onRefresh} loading={loading}>刷新</Button>
      </div>
      <LoadingOverlay active={loading && Boolean(payload)} tone="rose" label="正在刷新入库日志" />
      {!payload?.task ? (
        <EmptyState title="暂无入库日志" description="完成文档上传或入库任务后，这里会显示最近一次任务日志。" />
      ) : (
        <div className="space-y-4 p-5">
          <div className="grid gap-3 text-sm md:grid-cols-4">
            <Info label="任务 ID" value={payload.task.id} mono />
            <Info label="知识库" value={`${payload.task.kbName} / ${payload.task.kbId}`} />
            <Info label="文档" value={payload.task.documentName || "-"} />
            <Info label="状态" value={payload.task.status} />
          </div>
          <pre className="max-h-[520px] overflow-auto rounded-md border border-[#FECDD3]/80 bg-[#1F2937] p-4 text-xs leading-6 text-[#E5E7EB]">
            {payload.lines.length > 0 ? payload.lines.join("\n") : "日志文件为空或暂不可读。"}
          </pre>
          {payload.truncated && <p className="text-xs text-ink-tertiary">仅展示最新 500 行，共 {payload.lineCount} 行。</p>}
        </div>
      )}
    </section>
  );
}

function AuditLogPanel({
  filters,
  setFilters,
  logs,
  loading,
  pagination,
  onQuery,
  onReset,
}: {
  filters: AuditLogFilters;
  setFilters: React.Dispatch<React.SetStateAction<AuditLogFilters>>;
  logs: AuditLogRecord[];
  loading: boolean;
  pagination: ReturnType<typeof useClientPagination<AuditLogRecord>>;
  onQuery: () => void;
  onReset: () => void;
}) {
  return (
    <>
      <section className="overflow-hidden rounded-lg border border-[#E11D48]/24 bg-white/86 shadow-panel">
        <PanelHeader eyebrow="Audit" title="审计日志筛选" description="记录 API Key、配置等治理操作，不写入密钥明文、prompt、答案或文档正文。" />
        <div className="grid gap-3 bg-white/62 px-5 py-4 md:grid-cols-3 xl:grid-cols-6">
          <Input inputSize="sm" placeholder="动作，如 api_key.create" value={filters.action ?? ""} onChange={(e) => setFilters((prev) => ({ ...prev, action: e.target.value }))} />
          <Input inputSize="sm" placeholder="资源类型" value={filters.resourceType ?? ""} onChange={(e) => setFilters((prev) => ({ ...prev, resourceType: e.target.value }))} />
          <Input inputSize="sm" placeholder="资源 ID" value={filters.resourceId ?? ""} onChange={(e) => setFilters((prev) => ({ ...prev, resourceId: e.target.value }))} />
          <Input inputSize="sm" placeholder="操作者 ID" value={filters.actorId ?? ""} onChange={(e) => setFilters((prev) => ({ ...prev, actorId: e.target.value }))} />
          <Input inputSize="sm" type="datetime-local" aria-label="开始时间" value={filters.startAt ?? ""} onChange={(e) => setFilters((prev) => ({ ...prev, startAt: e.target.value }))} />
          <Input inputSize="sm" type="datetime-local" aria-label="结束时间" value={filters.endAt ?? ""} onChange={(e) => setFilters((prev) => ({ ...prev, endAt: e.target.value }))} />
          <Select value={filters.outcome ?? ""} onChange={(value) => setFilters((prev) => ({ ...prev, outcome: value }))} options={[{ value: "", label: "全部结果" }, { value: "success", label: "成功" }, { value: "failed", label: "失败" }]} />
          <Select value={String(filters.limit ?? 100)} onChange={(value) => setFilters((prev) => ({ ...prev, limit: Number(value) }))} options={limitOptions.map((item) => ({ value: String(item), label: `最近 ${item} 条` }))} />
        </div>
        <PanelActions onReset={onReset} onQuery={onQuery} loading={loading} />
      </section>

      <section className="overflow-hidden rounded-lg border border-[#E11D48]/24 bg-white/86 shadow-panel">
        <PanelHeader eyebrow="Governance" title="审计记录" />
        <LoadingOverlay active={loading && logs.length > 0} tone="rose" label="正在刷新审计日志" />
        {loading && logs.length === 0 ? (
          <LoadingRows rows={6} />
        ) : logs.length === 0 ? (
          <EmptyState title="暂无审计日志" description="API Key 管理和配置保存等治理操作会写入这里。" />
        ) : (
          <div className="overflow-x-auto animate-data-enter">
            <table className="w-full min-w-[1040px] text-sm">
              <thead>
                <tr className="border-b border-[#FECDD3]/80 bg-[#FFF0F4] text-[12px] font-medium uppercase tracking-[0.06em] text-[#BE123C]">
                  <th className="px-4 py-2.5 text-left">时间</th>
                  <th className="px-4 py-2.5 text-left">动作</th>
                  <th className="px-4 py-2.5 text-left">操作者</th>
                  <th className="px-4 py-2.5 text-left">资源</th>
                  <th className="px-4 py-2.5 text-left">摘要</th>
                  <th className="px-4 py-2.5 text-left">风险</th>
                  <th className="px-4 py-2.5 text-left">结果</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-[#FECDD3]/70">
                {pagination.pageItems.map((item) => (
                  <tr key={item.id} className="transition-colors hover:bg-[#FFF0F4]/70">
                    <td className="px-4 py-3 align-top">{formatTimestamp(item.createdAt)}</td>
                    <td className="px-4 py-3 align-top font-mono text-xs text-ink-primary">{item.action}</td>
                    <td className="px-4 py-3 align-top">
                      <p className="text-ink-primary">{item.actorName || item.actorId || "console"}</p>
                      <p className="mt-1 text-xs text-ink-tertiary">{item.tenantId || "未绑定租户"}</p>
                    </td>
                    <td className="px-4 py-3 align-top">
                      <p className="text-ink-primary">{item.resourceType}</p>
                      <p className="mt-1 max-w-[180px] truncate font-mono text-xs text-ink-tertiary">{item.resourceId || item.kbId || item.apiKeyId || "-"}</p>
                    </td>
                    <td className="max-w-[320px] px-4 py-3 align-top text-ink-secondary">{item.summary || "-"}</td>
                    <td className="px-4 py-3 align-top"><Badge variant={riskVariant(item.riskLevel)} dot>{item.riskLevel}</Badge></td>
                    <td className="px-4 py-3 align-top"><Badge variant={statusVariant(item.outcome)} dot>{item.outcome}</Badge></td>
                  </tr>
                ))}
              </tbody>
            </table>
            <PaginationControls pagination={pagination} />
          </div>
        )}
      </section>
    </>
  );
}

function IdentitySyncLogPanel({
  logs,
  loading,
  pagination,
  canSync,
  onRefresh,
  onSync,
}: {
  logs: IdentitySyncLogRecord[];
  loading: boolean;
  pagination: ReturnType<typeof useClientPagination<IdentitySyncLogRecord>>;
  canSync: boolean;
  onRefresh: () => void;
  onSync: () => void;
}) {
  return (
    <section className="overflow-hidden rounded-lg border border-[#E11D48]/24 bg-white/86 shadow-panel">
      <PanelHeader eyebrow="Identity Sync" title="用户及权限同步日志" description="展示 AI 基座租户、用户、角色、用户角色关系和删除事件的同步运行记录。" />
      <div className="flex justify-end gap-2 border-b border-[#FECDD3]/80 px-5 py-3">
        <Button size="sm" variant="ghost" onClick={onRefresh} disabled={loading}>刷新</Button>
        <Button
          size="sm"
          variant="primary"
          onClick={onSync}
          loading={loading}
          disabled={loading || !canSync}
          title={canSync ? "触发 AI 基座身份增量同步" : "仅 AI 基座 SSO 登录的 superManager 可同步"}
        >
          立即同步
        </Button>
      </div>
      <LoadingOverlay active={loading && logs.length > 0} tone="rose" label="正在刷新用户及权限同步日志" />
      {loading && logs.length === 0 ? (
        <LoadingRows rows={6} />
      ) : logs.length === 0 ? (
        <EmptyState title="暂无用户及权限同步日志" description="使用 AI 基座 SSO 的 superManager 触发同步后，这里会显示同步记录；后台调度无授权上下文时只会跳过。" />
      ) : (
        <div className="overflow-x-auto animate-data-enter">
          <table className="w-full min-w-[1120px] text-sm">
            <thead>
              <tr className="border-b border-[#FECDD3]/80 bg-[#FFF0F4] text-[12px] font-medium uppercase tracking-[0.06em] text-[#BE123C]">
                <th className="px-4 py-2.5 text-left">完成时间</th>
                <th className="px-4 py-2.5 text-left">模式 / 来源</th>
                <th className="px-4 py-2.5 text-right">租户</th>
                <th className="px-4 py-2.5 text-right">用户</th>
                <th className="px-4 py-2.5 text-right">角色</th>
                <th className="px-4 py-2.5 text-right">用户角色</th>
                <th className="px-4 py-2.5 text-right">删除</th>
                <th className="px-4 py-2.5 text-left">水位</th>
                <th className="px-4 py-2.5 text-left">状态</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-[#FECDD3]/70">
              {pagination.pageItems.map((item) => (
                <tr key={item.id} className="transition-colors hover:bg-[#FFF0F4]/70">
                  <td className="px-4 py-3 align-top">
                    <p className="font-medium text-ink-primary">{formatTimestamp(item.finishedAt || item.startedAt)}</p>
                    <p className="mt-1 text-xs text-ink-tertiary">#{item.id}</p>
                  </td>
                  <td className="px-4 py-3 align-top">
                    <p className="font-mono text-xs text-ink-primary">{item.syncMode}</p>
                    <p className="mt-1 max-w-[220px] truncate text-xs text-ink-tertiary">{item.sourceHost || "-"}</p>
                    {item.sourceSchema && (
                      <p className="mt-1 max-w-[260px] truncate text-xs text-ink-tertiary" title={item.sourceSchema}>
                        {item.sourceSchema}
                      </p>
                    )}
                  </td>
                  <td className="px-4 py-3 text-right font-mono align-top">{formatNumber(item.tenantsCount)}</td>
                  <td className="px-4 py-3 text-right font-mono align-top">{formatNumber(item.usersCount)}</td>
                  <td className="px-4 py-3 text-right font-mono align-top">{formatNumber(item.rolesCount)}</td>
                  <td className="px-4 py-3 text-right font-mono align-top">{formatNumber(item.userRolesCount)}</td>
                  <td className="px-4 py-3 text-right font-mono align-top">{formatNumber(item.deletedCount)}</td>
                  <td className="px-4 py-3 align-top">
                    <p className="text-xs text-ink-primary">last: {item.lastSyncAt || "首次同步"}</p>
                    <p className="mt-1 text-xs text-ink-tertiary">max: {item.maxUpdatedAt || "-"}</p>
                    {item.snapshotVersion && <p className="mt-1 max-w-[220px] truncate text-xs text-ink-tertiary">version: {item.snapshotVersion}</p>}
                  </td>
                  <td className="px-4 py-3 align-top">
                    <Badge variant={statusVariant(item.status)} dot>{item.status}</Badge>
                    {item.hasMore && <p className="mt-1 text-xs text-status-warning">仍有后续数据</p>}
                    {item.errorMessage && <p className="mt-1 max-w-[260px] truncate text-xs text-status-danger">{item.errorMessage}</p>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          <PaginationControls pagination={pagination} />
        </div>
      )}
    </section>
  );
}

function TabButton({ active, onClick, children }: { active: boolean; onClick: () => void; children: React.ReactNode }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={`h-9 rounded-md px-4 text-sm font-medium transition-colors ${active ? "bg-[#E11D48] text-white shadow-sm" : "text-ink-secondary hover:bg-[#FFF0F4] hover:text-[#BE123C]"}`}
    >
      {children}
    </button>
  );
}

function Select({ value, onChange, options }: { value: string; onChange: (value: string) => void; options: Array<{ value: string; label: string }> }) {
  return (
    <select
      value={value}
      onChange={(e) => onChange(e.target.value)}
      className="h-8 rounded-md border border-[#FECDD3] bg-white px-3 text-[13px] text-ink-primary shadow-sm transition-colors hover:border-[#E11D48]/45 focus:border-[#E11D48] focus:outline-none focus:ring-2 focus:ring-[#E11D48]/20"
    >
      {options.map((item) => (
        <option key={item.value} value={item.value}>{item.label}</option>
      ))}
    </select>
  );
}

function PanelHeader({ eyebrow, title, description }: { eyebrow: string; title: string; description?: string }) {
  return (
    <div className="border-b border-[#FECDD3]/60 bg-[linear-gradient(90deg,rgba(225,29,72,0.05),rgba(249,115,22,0.035),rgba(255,255,255,0.82))] px-5 py-3">
      <p className="text-[11px] font-semibold uppercase tracking-[0.1em] text-[#BE123C]">{eyebrow}</p>
      <div className="mt-1 flex items-center gap-2">
        <h2 className="text-base font-semibold text-ink-primary">{title}</h2>
        {description && <InfoTooltip content={description} />}
      </div>
    </div>
  );
}

function PanelActions({ onReset, onQuery, loading }: { onReset: () => void; onQuery: () => void; loading: boolean }) {
  return (
    <div className="flex justify-end gap-2 border-t border-[#FECDD3]/80 px-5 py-3">
      <Button size="sm" variant="ghost" onClick={onReset} disabled={loading}>重置</Button>
      <Button size="sm" variant="primary" onClick={onQuery} loading={loading}>查询</Button>
    </div>
  );
}

function PaginationControls<T>({ pagination }: { pagination: ReturnType<typeof useClientPagination<T>> }) {
  return (
    <TablePagination
      page={pagination.page}
      pageSize={pagination.pageSize}
      total={pagination.total}
      pageCount={pagination.pageCount}
      startIndex={pagination.startIndex}
      endIndex={pagination.endIndex}
      onPageChange={pagination.setPage}
      onPageSizeChange={pagination.setPageSize}
    />
  );
}

function Info({ label, value, mono = false }: { label: string; value: string; mono?: boolean }) {
  return (
    <div className="rounded-md border border-[#FECDD3]/80 bg-[#FFF0F4]/48 p-3">
      <p className="text-xs text-ink-tertiary">{label}</p>
      <p className={`mt-1 truncate text-sm text-ink-primary ${mono ? "font-mono" : ""}`}>{value}</p>
    </div>
  );
}

function MetricCard({ label, value, helper }: { label: string; value: string; helper: string }) {
  return (
    <div className="relative min-h-[108px] overflow-hidden rounded-md border border-[#E11D48]/18 bg-[radial-gradient(circle_at_88%_18%,rgba(225,29,72,0.07),transparent_36%),linear-gradient(135deg,rgba(255,240,244,0.55),#FFFFFF_62%)] p-4 shadow-[0_10px_24px_rgba(225,29,72,0.055)] after:pointer-events-none after:absolute after:-bottom-10 after:-right-8 after:h-24 after:w-24 after:rounded-full after:bg-[#E11D48]/7 after:content-['']">
      <div className="relative z-10 flex items-center gap-2">
        <p className="text-[12px] font-medium uppercase tracking-[0.08em] text-[#BE123C]">{label}</p>
        <InfoTooltip content={helper} />
      </div>
      <p className="mt-2 font-mono text-[34px] font-bold leading-none text-ink-primary">{value}</p>
    </div>
  );
}
