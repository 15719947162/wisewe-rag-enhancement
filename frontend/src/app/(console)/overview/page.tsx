"use client";

import { useEffect, useState } from "react";
import { MetricCardV3 } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { StatusBadge } from "@/components/ui/status-badge";
import { LoadingOverlay, Skeleton } from "@/components/ui/skeleton";
import { TablePagination, useClientPagination } from "@/components/ui/table-pagination";
import { getDashboardStats, getAlerts, getQueueItems } from "@/lib/api/client";
import { getSelectedIdentity, IDENTITY_CHANGED_EVENT } from "@/lib/auth/identity";
import { formatTimestamp } from "@/lib/formatters";
import { getQueueLaneLabel } from "@/lib/i18n/zh-cn";
import { formatKbId } from "@/lib/kb-id";
import type { AlertItem, IntegrationIdentity, QueueItem } from "@/lib/contracts/types";

type Stats = {
  kb_count: number;
  doc_count: number;
  chunk_count: number;
  recent_tasks: Array<{ id: string; kbId: string; documentName: string; status: string; updatedAt: string }>;
};

export default function OverviewPage() {
  const [stats, setStats] = useState<Stats | null>(null);
  const [alerts, setAlerts] = useState<AlertItem[]>([]);
  const [queue, setQueue] = useState<QueueItem[]>([]);
  const [identity, setIdentity] = useState<IntegrationIdentity | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  async function load() {
    setLoading(true);
    setError(null);
    try {
      const [s, a, q] = await Promise.all([getDashboardStats(), getAlerts(), getQueueItems()]);
      setStats(s);
      setAlerts(a);
      setQueue(q);
    } catch (err) {
      setStats(null);
      setAlerts([]);
      setQueue([]);
      setError(err instanceof Error ? err.message : "加载系统总览数据失败");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    load();
  }, []);

  useEffect(() => {
    const refreshIdentity = () => setIdentity(getSelectedIdentity());
    refreshIdentity();
    window.addEventListener(IDENTITY_CHANGED_EVENT, refreshIdentity);
    window.addEventListener("storage", refreshIdentity);
    return () => {
      window.removeEventListener(IDENTITY_CHANGED_EVENT, refreshIdentity);
      window.removeEventListener("storage", refreshIdentity);
    };
  }, []);

  const metrics = stats
    ? [
        { label: "知识库", value: String(stats.kb_count), hint: "当前可见知识资产" },
        { label: "文档", value: String(stats.doc_count), hint: "已纳入治理范围" },
        { label: "切片", value: stats.chunk_count.toLocaleString(), hint: "向量化知识规模" },
        { label: "最近任务", value: String(stats.recent_tasks.length), hint: "本地队列活跃度" },
      ]
    : [];
  const hasLoaded = !loading || Boolean(stats) || queue.length > 0 || alerts.length > 0 || Boolean(error);
  const queuePagination = useClientPagination(queue, 20);
  const recentTasksPagination = useClientPagination(stats?.recent_tasks ?? [], 20);

  return (
    <div className="space-y-6">
      <section className="grid gap-5 xl:grid-cols-[minmax(0,1.7fr)_minmax(340px,0.9fr)]">
        <div className="preview-panel p-6 [--panel-tone:#365DFF]">
          <div className="relative z-10 flex flex-col gap-5 lg:flex-row lg:items-start lg:justify-between">
            <div className="max-w-3xl">
              <p className="preview-eyebrow text-[#365DFF]">Command Center</p>
              <h1 className="mt-3 text-[34px] font-extrabold leading-tight text-ink-primary">
                教材知识库全链路控制台
              </h1>
              <p className="mt-3 text-sm leading-7 text-ink-secondary">
                把文档解析、入库生产线、RAG 验证、OpenAPI 治理和日志用量收拢到一个工作台。第一屏直接回答：系统是否健康、任务卡在哪里、风险是什么、下一步该处理什么。
              </p>
            </div>
            <button
              onClick={load}
              className="inline-flex h-[38px] shrink-0 cursor-pointer items-center gap-1.5 rounded-lg border border-[#365DFF]/25 bg-white px-3 text-[13px] font-semibold text-[#2447DB] shadow-sm transition-colors hover:border-[#365DFF]/45 hover:bg-[#EEF3FF]"
            >
              <RefreshIcon /> 刷新
            </button>
          </div>

          <div className="relative z-10 mt-6 grid gap-3 md:grid-cols-4">
            <QuickCard tone="#00A889" title="知识资产" description="文档、切片、图谱、证据统一管理" />
            <QuickCard tone="#FF8A00" title="入库生产线" description="解析、增强、向量化阶段可观测" />
            <QuickCard tone="#7C3AED" title="问答验证" description="召回、引用、评分和拒答归因" />
            <QuickCard tone="#E11D48" title="审计用量" description="requestId、Token、延迟、导出闭环" />
          </div>
        </div>

        <div className="grid gap-4">
          <HealthCard alerts={alerts.length} queue={queue.length} />
          <CurrentIdentityCard identity={identity} />
        </div>
      </section>

      <section className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        {loading
          ? [1, 2, 3, 4].map((i) => <Skeleton key={i} className="h-28 rounded-md" />)
          : metrics.map((m, index) => (
              <MetricCardV3
                key={m.label}
                label={m.label}
                value={m.value}
                trendLabel={m.hint}
                accent={(["knowledge", "ingestion", "rag", "observe"] as const)[index] ?? "command"}
              />
            ))}
      </section>

      {error && (
        <div className="rounded-sm border border-status-danger bg-[#FEF2F2] px-4 py-3 text-sm text-status-danger">
          {error}
        </div>
      )}

      <section className="grid gap-6 xl:grid-cols-[7fr_5fr]">
        <div className="relative overflow-hidden rounded-lg border border-[#FF8A00]/24 bg-[linear-gradient(135deg,rgba(255,138,0,0.10),rgba(255,255,255,0.92))] shadow-panel">
          <LoadingOverlay active={loading && hasLoaded} tone="amber" label="正在刷新队列" />
          <div className="flex items-center justify-between border-b border-[#FF8A00]/18 bg-[linear-gradient(90deg,rgba(255,138,0,0.12),rgba(255,255,255,0.76))] px-5 py-4">
            <div>
              <p className="text-[11px] font-semibold uppercase tracking-[0.1em] text-[#C2410C]">Ingestion</p>
              <h2 className="mt-1 text-base font-semibold text-ink-primary">入库生产线</h2>
            </div>
            <Badge variant="neutral">{queue.length} 个任务</Badge>
          </div>
          <div className="divide-y divide-[#FED7AA]/70 bg-white/72">
            {loading && !hasLoaded ? (
              [1, 2, 3, 4].map((item) => (
                <div key={item} className="px-5 py-3">
                  <Skeleton className="h-4 w-56" />
                  <Skeleton className="mt-2 h-3 w-72" />
                </div>
              ))
            ) : queue.length === 0 ? (
              <div className="px-5 py-8 text-center text-sm text-[#9A3412]">当前没有待处理任务</div>
            ) : (
              queuePagination.pageItems.map((item) => (
                <article key={item.id} className="grid gap-3 bg-[linear-gradient(90deg,rgba(255,138,0,0.08),rgba(255,255,255,0.80)_48%)] px-5 py-3 transition-colors hover:bg-[#FFF7ED]/78 lg:grid-cols-[140px_minmax(0,1fr)_auto] lg:items-center">
                  <span className="text-[11px] font-semibold uppercase tracking-[0.18em] text-[#C2410C]">
                    {getQueueLaneLabel(item.lane)}
                  </span>
                  <div>
                    <p className="text-sm font-medium text-ink-primary">{item.title}</p>
                    <p className="mt-0.5 text-xs text-ink-tertiary">{item.subtitle}</p>
                  </div>
                  <div className="flex items-center gap-2">
                    <span className="font-mono text-xs text-ink-tertiary">{formatTimestamp(item.updatedAt)}</span>
                    <StatusBadge status={item.status} />
                  </div>
                </article>
              ))
            )}
          </div>
          {queue.length > 0 && <PaginationControls pagination={queuePagination} />}
        </div>

        <div className="relative overflow-hidden rounded-lg border border-[#E11D48]/24 bg-[linear-gradient(135deg,rgba(225,29,72,0.10),rgba(255,255,255,0.92))] shadow-panel">
          <LoadingOverlay active={loading && hasLoaded} tone="rose" label="正在刷新告警" />
          <div className="flex items-center justify-between border-b border-[#E11D48]/18 bg-[linear-gradient(90deg,rgba(225,29,72,0.12),rgba(255,255,255,0.76))] px-5 py-4">
            <div>
              <p className="text-[11px] font-semibold uppercase tracking-[0.1em] text-[#BE123C]">Risk</p>
              <h2 className="mt-1 text-base font-semibold text-ink-primary">风险与告警</h2>
            </div>
            {alerts.length > 0 && <Badge variant="danger" dot>{alerts.length}</Badge>}
          </div>
          {loading && !hasLoaded ? (
            <div className="space-y-3 px-5 py-5">
              {[1, 2, 3].map((item) => <Skeleton key={item} className="h-12 rounded-sm" />)}
            </div>
          ) : alerts.length === 0 ? (
            <div className="flex flex-col items-center justify-center py-10 text-center">
              <CheckCircleIcon />
              <p className="mt-2 text-sm text-ink-secondary">系统运行正常</p>
            </div>
          ) : (
            <div className="divide-y divide-[#FECDD3]/70 bg-white/72">
              {alerts.map((alert) => (
                <article key={alert.id} className="flex gap-3 bg-[linear-gradient(90deg,rgba(225,29,72,0.08),rgba(255,255,255,0.80)_56%)] px-5 py-3">
                  <div className={["mt-1 w-1 shrink-0 self-stretch rounded-full", alert.severity === "failed" ? "bg-status-danger" : "bg-status-warning"].join(" ")} />
                  <div>
                    <p className="text-sm font-medium text-ink-primary">{alert.title}</p>
                    <p className="mt-0.5 text-xs text-ink-tertiary">{alert.description}</p>
                  </div>
                </article>
              ))}
            </div>
          )}
        </div>
      </section>

      {stats && stats.recent_tasks.length > 0 && (
        <section className="overflow-hidden rounded-lg border border-[#365DFF]/22 bg-white/86 shadow-panel">
          <div className="border-b border-[#365DFF]/18 bg-[linear-gradient(90deg,rgba(54,93,255,0.12),rgba(6,182,212,0.08),rgba(255,255,255,0.76))] px-5 py-4">
            <p className="text-[11px] font-semibold uppercase tracking-[0.1em] text-[#2447DB]">Recent</p>
            <h2 className="mt-1 text-base font-semibold text-ink-primary">最近入库任务</h2>
          </div>
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-[#BAE6FD]/80 bg-[linear-gradient(90deg,#EEF3FF,#ECF8FF)] text-[12px] font-medium uppercase tracking-[0.06em] text-[#2447DB]">
                <th className="px-4 py-2.5 text-left">文档</th>
                <th className="px-4 py-2.5 text-left">知识库</th>
                <th className="px-4 py-2.5 text-left">状态</th>
                <th className="px-4 py-2.5 text-right">更新时间</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-[#BAE6FD]/70">
              {recentTasksPagination.pageItems.map((task) => (
                <tr key={task.id} className="transition-colors hover:bg-[#EEF3FF]/70">
                  <td className="px-4 py-3 font-medium text-ink-primary">{task.documentName}</td>
                  <td className="px-4 py-3 font-mono text-xs text-ink-tertiary">{formatKbId(task.kbId)}</td>
                  <td className="px-4 py-3">
                    <StatusBadge status={task.status as "success" | "failed" | "pending" | "running" | "degraded" | "empty"} />
                  </td>
                  <td className="px-4 py-3 text-right font-mono text-xs text-ink-tertiary">{formatTimestamp(task.updatedAt)}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <PaginationControls pagination={recentTasksPagination} />
        </section>
      )}
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

function QuickCard({ tone, title, description }: { tone: string; title: string; description: string }) {
  return (
    <div
      className="relative min-h-24 overflow-hidden rounded-lg border p-3 shadow-[0_12px_28px_rgba(36,48,86,0.08)]"
      style={{
        borderColor: `color-mix(in srgb, ${tone} 26%, var(--color-border-subtle))`,
        background: `linear-gradient(135deg, color-mix(in srgb, ${tone} 18%, white), rgba(255,255,255,0.88))`,
      }}
    >
      <span
        className="preview-dot"
        style={{
          ["--dot-tone" as string]: tone,
        }}
      />
      <b className="relative z-10 mt-3 block text-[13px] text-ink-primary">{title}</b>
      <span className="relative z-10 mt-1 block text-xs leading-5 text-ink-secondary">{description}</span>
      <span
        className="absolute -bottom-8 -right-6 h-20 w-20 rounded-full"
        style={{ backgroundColor: `color-mix(in srgb, ${tone} 18%, transparent)` }}
      />
    </div>
  );
}

function HealthCard({ alerts, queue }: { alerts: number; queue: number }) {
  return (
    <section className="preview-panel p-5 [--panel-tone:#0EA5E9]">
      <div className="flex items-center justify-between gap-3">
        <div>
          <p className="preview-eyebrow text-[#0369A1]">System Health</p>
          <h2 className="mt-1 text-base font-semibold text-ink-primary">系统健康</h2>
        </div>
        <Badge variant={alerts > 0 ? "warning" : "success"} dot>
          {alerts > 0 ? "有告警" : "正常"}
        </Badge>
      </div>
      <div className="mt-4 divide-y divide-border-subtle text-sm">
        <HealthRow label="后端服务" value="正常" tone="#00A889" />
        <HealthRow label="入库队列" value={`${queue} 个任务`} tone="#FF8A00" />
        <HealthRow label="风险告警" value={`${alerts} 条`} tone={alerts > 0 ? "#E11D48" : "#00A889"} />
        <HealthRow label="治理能力" value="持续收口" tone="#0EA5E9" />
      </div>
    </section>
  );
}

function HealthRow({ label, value, tone }: { label: string; value: string; tone: string }) {
  return (
    <div className="flex items-center justify-between gap-3 py-3">
      <span className="text-ink-secondary">{label}</span>
      <b className="font-semibold" style={{ color: tone }}>{value}</b>
    </div>
  );
}

function CurrentIdentityCard({ identity }: { identity: IntegrationIdentity | null }) {
  const roleLabel = identity?.isTenantAdmin ? "租户管理员" : "普通用户";
  const sourceLabel = identity?.source === "identity_snapshot" ? "本地身份快照" : identity?.source || "会话身份";

  return (
    <section className="preview-panel [--panel-tone:#0EA5E9]">
      <div className="preview-panel-header">
        <div className="flex flex-wrap items-center gap-2">
          <p className="preview-eyebrow text-[#0369A1]">当前身份</p>
          <Badge variant={identity?.isTenantAdmin ? "info" : "neutral"}>{roleLabel}</Badge>
          <Badge variant="neutral">{sourceLabel}</Badge>
        </div>
        <h2 className="mt-3 truncate text-[22px] font-bold leading-tight text-ink-primary">
          {identity?.displayName || identity?.username || identity?.userId || "未读取到身份"}
        </h2>
        <p className="mt-2 text-sm leading-6 text-ink-secondary">
          操作会按该身份注入租户与用户上下文。
        </p>
      </div>

      <div className="grid gap-px bg-[#BAE6FD]/70 sm:grid-cols-2">
        <IdentityMeta label="租户" value={identity?.tenantName || (identity ? `租户 ${identity.tenantId}` : "--")} />
        <IdentityMeta label="用户名" value={identity?.username || "--"} />
        <IdentityMeta label="用户 ID" value={identity?.userId || "--"} mono />
        <IdentityMeta label="角色编码" value={identity?.roleCodes?.join(" / ") || "--"} mono />
      </div>
    </section>
  );
}

function IdentityMeta({ label, value, mono = false }: { label: string; value: string; mono?: boolean }) {
  return (
    <div className="min-h-[82px] bg-white/72 px-5 py-4">
      <p className="text-[11px] font-semibold uppercase tracking-[0.12em] text-[#0369A1]">{label}</p>
      <p className={`mt-2 truncate text-sm font-semibold text-ink-primary ${mono ? "font-mono" : ""}`} title={value}>
        {value}
      </p>
    </div>
  );
}

function RefreshIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
      <path d="M3 12a9 9 0 0 1 9-9 9.75 9.75 0 0 1 6.74 2.74L21 8" />
      <path d="M21 3v5h-5" />
      <path d="M21 12a9 9 0 0 1-9 9 9.75 9.75 0 0 1-6.74-2.74L3 16" />
      <path d="M8 16H3v5" />
    </svg>
  );
}

function CheckCircleIcon() {
  return (
    <svg width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="#1F9D55" strokeWidth="1.5">
      <path d="M20 6 9 17l-5-5" />
      <circle cx="12" cy="12" r="10" />
    </svg>
  );
}
