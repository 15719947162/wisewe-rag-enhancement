"use client";

import { useEffect, useMemo, useState } from "react";
import { ContextRail } from "@/components/layout/context-rail";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { EmptyState } from "@/components/ui/empty-state";
import { LoadingOverlay, LoadingRows, Skeleton } from "@/components/ui/skeleton";
import { TablePagination, useClientPagination } from "@/components/ui/table-pagination";
import { getTokenUsage } from "@/lib/api/client";
import type {
  LlmCallUsageRecord,
  TokenUsageBucket,
  TokenUsageHourlyBucket,
  TokenUsageQuotaAlert,
  TokenUsageStageBucket,
  TokenUsageSummary,
} from "@/lib/contracts/types";
import { formatLatency, formatNumber, formatTimestamp } from "@/lib/formatters";

type UsageTab = {
  key: string;
  label: string;
  domain?: string;
};

const usageTabs: UsageTab[] = [
  { key: "all", label: "全部链路" },
  { key: "ingestion", label: "入库链路", domain: "ingestion" },
  { key: "online_rag", label: "在线问答", domain: "online_rag" },
  { key: "graph_rag", label: "Graph RAG", domain: "graph_rag" },
  { key: "openapi", label: "OpenAPI", domain: "openapi" },
  { key: "evaluation", label: "评测", domain: "evaluation" },
];

type MetricCardConfig = {
  key: keyof TokenUsageBucket;
  label: string;
  helper: string;
  tone: "blue" | "teal" | "amber" | "violet";
  formatter?: "latency";
};

const metricCards: MetricCardConfig[] = [
  { key: "requestCount", label: "调用次数", helper: "来自模型调用明细", tone: "blue" },
  { key: "promptTokens", label: "输入 Token", helper: "Prompt / Embedding 输入", tone: "teal" },
  { key: "completionTokens", label: "输出 Token", helper: "模型生成输出", tone: "amber" },
  { key: "totalTokens", label: "总 Token", helper: "用于费用估算与 quota 告警", tone: "violet" },
  { key: "avgLatencyMs", label: "平均延迟", helper: "忽略 0ms 样本", tone: "blue", formatter: "latency" },
];

export default function UsagePage() {
  const [summary, setSummary] = useState<TokenUsageSummary | null>(null);
  const [limit, setLimit] = useState(50);
  const [activeTab, setActiveTab] = useState("all");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const activeDomain = usageTabs.find((tab) => tab.key === activeTab)?.domain;

  async function load(nextLimit = limit, nextDomain = activeDomain) {
    setLoading(true);
    try {
      const data = await getTokenUsage(nextLimit, nextDomain);
      setSummary(data);
      setError(null);
    } catch (err) {
      setSummary(null);
      setError(err instanceof Error ? err.message : "加载 Token 统计失败");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void load(limit, activeDomain);
  }, [activeTab]);

  const overall = summary?.overall ?? emptyBucket();
  const stages = useMemo(() => summary?.pipelineStages ?? [], [summary]);
  const recordedStageCount = stages.filter((stage) => stage.detailStatus === "recorded").length;
  const hasLoaded = !loading || Boolean(summary) || Boolean(error);

  return (
    <div className="space-y-5">
      <ContextRail
        title="Token 统计"
        description="按业务链路查看模型调用消耗，覆盖入库增强、向量化、在线问答、Graph RAG、OpenAPI 与评测等环节。"
        showGlobalHint={false}
      />

      <section className="relative overflow-hidden rounded-md border border-[#E11D48]/24 bg-[radial-gradient(circle_at_88%_12%,rgba(225,29,72,0.14),transparent_34%),linear-gradient(135deg,rgba(225,29,72,0.08),rgba(255,255,255,0.92))] shadow-panel">
        <div className="border-b border-[#FECDD3]/80 bg-white/62 px-6 py-5">
          <div className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
            <div>
              <div className="flex flex-wrap items-center gap-2">
                <Badge variant="info">明细源：{summary?.source ?? "kb_llm_call_logs"}</Badge>
                <Badge variant={summary?.detailAvailable ? "success" : "warning"}>
                  {summary?.detailAvailable ? "已有模型调用明细" : "当前链路暂无调用明细"}
                </Badge>
                <Badge variant="neutral">{scopeLabel(summary?.scope)}</Badge>
              </div>
              <h1 className="mt-2 text-[30px] font-semibold leading-tight text-ink-primary">按链路统计模型消耗</h1>
              <p className="mt-2 max-w-4xl text-sm leading-6 text-ink-secondary">
                每条模型调用都会记录供应商、模型、Token、延迟、状态和使用时间。入库切片增强与向量化现在也会写入同一张明细表。
              </p>
            </div>
            <div className="flex shrink-0 items-center gap-2">
              <select
                value={limit}
                onChange={(event) => setLimit(Number(event.target.value))}
                className="h-9 rounded-md border border-border-subtle bg-panel px-3 text-[13px] font-medium text-ink-primary transition-colors focus:border-border-focus focus:outline-none focus:ring-2 focus:ring-border-focus/30"
                aria-label="明细条数"
              >
                {[20, 50, 100].map((item) => (
                  <option key={item} value={item}>
                    最近 {item} 条
                  </option>
                ))}
              </select>
              <Button size="md" variant="primary" loading={loading} onClick={() => load(limit, activeDomain)}>
                刷新
              </Button>
            </div>
          </div>

          <div className="mt-5 flex flex-wrap gap-2">
            {usageTabs.map((tab) => (
              <button
                key={tab.key}
                type="button"
                onClick={() => setActiveTab(tab.key)}
                className={[
                  "rounded-md border px-3 py-2 text-sm font-medium transition-colors",
                  activeTab === tab.key
                    ? "border-[#E11D48] bg-[#FFF0F4] text-[#BE123C]"
                    : "border-border-subtle bg-white text-ink-secondary hover:border-[#FECDD3] hover:bg-[#FFF7F9]",
                ].join(" ")}
              >
                {tab.label}
              </button>
            ))}
          </div>

          <div className="mt-6 grid gap-3 md:grid-cols-5">
            {loading && !hasLoaded
              ? [1, 2, 3, 4, 5].map((item) => <Skeleton key={item} className="h-24 rounded-lg" />)
              : metricCards.map((card) => (
                  <MetricCard
                    key={card.key}
                    label={card.label}
                    value={card.formatter === "latency" ? formatLatency(overall[card.key]) : formatNumber(overall[card.key])}
                    helper={card.helper}
                    tone={card.tone}
                  />
                ))}
          </div>
        </div>

        <div className="grid gap-0 lg:grid-cols-3">
          <SummaryCell label="已记录环节" value={`${recordedStageCount} / ${stages.length || 8}`} />
          <SummaryCell label="最近明细" value={`${summary?.llmCalls?.length ?? 0} 条`} />
          <SummaryCell label="当前链路" value={usageTabs.find((tab) => tab.key === activeTab)?.label ?? "全部链路"} />
        </div>
      </section>

      {error && (
        <div role="alert" className="rounded-md border border-status-danger bg-[#FEF2F2] px-4 py-3 text-sm text-status-danger">
          {error}
        </div>
      )}

      <UsageGovernancePanel summary={summary} loading={loading && !hasLoaded} refreshing={loading && hasLoaded} />

      <StageUsagePanel stages={stages} loading={loading && !hasLoaded} refreshing={loading && hasLoaded} />

      <RecentCallsPanel calls={summary?.llmCalls ?? []} loading={loading && !hasLoaded} refreshing={loading && hasLoaded} />
    </div>
  );
}

function UsageGovernancePanel({
  summary,
  loading,
  refreshing,
}: {
  summary: TokenUsageSummary | null;
  loading: boolean;
  refreshing: boolean;
}) {
  const hourlyUsage = summary?.hourlyUsage ?? [];
  const costSummary = summary?.costSummary;
  const quota = summary?.quota;
  const quotaAlerts = summary?.quotaAlerts ?? [];
  return (
    <section className="relative overflow-hidden rounded-lg border border-[#E11D48]/24 bg-white/88 shadow-panel">
      <LoadingOverlay active={refreshing} tone="rose" label="正在刷新用量治理统计" />
      <div className="border-b border-[#FECDD3]/80 bg-[linear-gradient(90deg,rgba(14,165,233,0.12),rgba(225,29,72,0.08),rgba(255,255,255,0.76))] px-5 py-4">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <p className="metric-label">用量治理</p>
            <h2 className="mt-1 text-base font-semibold text-ink-primary">小时统计、费用估算与 quota 告警</h2>
          </div>
          <Badge variant={summary?.chartReady ? "success" : "warning"}>
            {summary?.chartReady ? "小时 rollup 已可用" : "暂无小时 rollup"}
          </Badge>
        </div>
      </div>

      {loading ? (
        <LoadingRows rows={4} />
      ) : (
        <div className="grid gap-4 p-5 lg:grid-cols-[1.2fr_1fr]">
          <HourlyUsageChart rows={hourlyUsage} />
          <div className="space-y-3">
            <div className="grid gap-3 sm:grid-cols-2">
              <GovernanceMetric
                label="估算总费用"
                value={`${costSummary?.currency ?? "CNY"} ${formatCost(costSummary?.estimatedCost ?? 0)}`}
                helper={costSummary?.configured ? "按当前费率估算" : "未配置费用费率，按 0 计算"}
              />
              <GovernanceMetric
                label="近 24 小时费用"
                value={`${costSummary?.currency ?? "CNY"} ${formatCost(costSummary?.recent24hEstimatedCost ?? 0)}`}
                helper="来自 kb_token_usage_hourly"
              />
              <GovernanceMetric
                label="日 quota"
                value={quota?.dailyTokenLimit ? `${Math.round((quota.dailyUsageRatio ?? 0) * 100)}%` : "未配置"}
                helper={quota?.dailyTokenLimit ? `${formatNumber(quota.currentScopeTokenUsage)} / ${formatNumber(quota.dailyTokenLimit)} Token` : "仅生成告警，不做强制拦截"}
              />
              <GovernanceMetric
                label="月 quota"
                value={quota?.monthlyTokenLimit ? `${Math.round((quota.monthlyUsageRatio ?? 0) * 100)}%` : "未配置"}
                helper={quota?.monthlyTokenLimit ? `${formatNumber(quota.currentScopeTokenUsage)} / ${formatNumber(quota.monthlyTokenLimit)} Token` : "仅生成告警，不做强制拦截"}
              />
            </div>
            <QuotaAlerts alerts={quotaAlerts} />
          </div>
        </div>
      )}
    </section>
  );
}

function HourlyUsageChart({ rows }: { rows: TokenUsageHourlyBucket[] }) {
  if (rows.length === 0) {
    return (
      <div className="flex min-h-[220px] items-center justify-center rounded-md border border-dashed border-[#BAE6FD] bg-[#F0F9FF] px-4 text-center text-sm text-ink-secondary">
        小时级 rollup 产生后会在这里展示最近 24 小时 Token 趋势。
      </div>
    );
  }
  const ordered = [...rows].reverse().slice(-24);
  const maxTokens = Math.max(...ordered.map((row) => row.totalTokens), 1);
  return (
    <div className="rounded-md border border-[#BAE6FD] bg-[linear-gradient(135deg,#F0F9FF,#FFFFFF_58%)] p-4">
      <div className="flex items-center justify-between gap-3">
        <div>
          <p className="metric-label">最近小时</p>
          <h3 className="mt-1 text-sm font-semibold text-ink-primary">Token 趋势</h3>
        </div>
        <Badge variant="neutral">{ordered.length} 个桶</Badge>
      </div>
      <div className="mt-5 flex h-36 items-end gap-2">
        {ordered.map((row) => (
          <div key={`${row.hourBucket}-${row.pipelineDomain}-${row.pipelineStage}-${row.featureName}`} className="flex min-w-0 flex-1 flex-col items-center gap-2">
            <div className="flex h-28 w-full items-end rounded-t-sm bg-[#E0F2FE]">
              <div
                className="w-full rounded-t-sm bg-[linear-gradient(180deg,#0EA5E9,#E11D48)]"
                style={{ height: `${Math.max((row.totalTokens / maxTokens) * 100, 6)}%` }}
                title={`${row.pipelineDomain}/${row.pipelineStage}: ${formatNumber(row.totalTokens)} Token`}
              />
            </div>
            <span className="w-full truncate text-center font-mono text-[10px] text-ink-tertiary">{formatHour(row.hourBucket)}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

function GovernanceMetric({ label, value, helper }: { label: string; value: string; helper: string }) {
  return (
    <div className="rounded-md border border-[#FECDD3]/80 bg-[#FFF7F9] p-3">
      <p className="metric-label">{label}</p>
      <p className="mt-2 font-mono text-lg font-semibold text-ink-primary">{value}</p>
      <p className="mt-1 text-xs text-ink-secondary">{helper}</p>
    </div>
  );
}

function QuotaAlerts({ alerts }: { alerts: TokenUsageQuotaAlert[] }) {
  if (alerts.length === 0) {
    return (
      <div className="rounded-md border border-[#BBF7D0] bg-[#F0FDF4] px-4 py-3 text-sm text-[#166534]">
        当前没有 quota 告警。
      </div>
    );
  }
  return (
    <div className="space-y-2">
      {alerts.map((alert) => (
        <div key={alert.id} className="rounded-md border border-[#FDBA74] bg-[#FFF7ED] px-4 py-3 text-sm text-[#9A3412]">
          <p className="font-semibold">{alert.title}</p>
          <p className="mt-1">{alert.message}</p>
        </div>
      ))}
    </div>
  );
}

function StageUsagePanel({
  stages,
  loading,
  refreshing,
}: {
  stages: TokenUsageStageBucket[];
  loading: boolean;
  refreshing: boolean;
}) {
  return (
    <section className="relative overflow-hidden rounded-lg border border-[#E11D48]/24 bg-white/88 shadow-panel">
      <LoadingOverlay active={refreshing} tone="rose" label="正在刷新环节明细" />
      <div className="border-b border-[#FECDD3]/80 bg-[linear-gradient(90deg,rgba(225,29,72,0.12),rgba(249,115,22,0.08),rgba(255,255,255,0.76))] px-5 py-4">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <p className="metric-label">功能环节</p>
            <h2 className="mt-1 text-base font-semibold text-ink-primary">链路消耗明细</h2>
          </div>
          <Badge variant="neutral">{stages.length} 个环节</Badge>
        </div>
      </div>

      {loading ? (
        <LoadingRows rows={5} />
      ) : stages.length === 0 ? (
        <EmptyState title="暂无 Token 环节统计" description="产生模型调用明细后会自动展示到这里。" />
      ) : (
        <div className="divide-y divide-[#FECDD3]/70">
          {stages.map((stage) => (
            <StageRow key={`${stage.pipelineStage}-${stage.featureName}`} stage={stage} />
          ))}
        </div>
      )}
    </section>
  );
}

function StageRow({ stage }: { stage: TokenUsageStageBucket }) {
  return (
    <details className="group open:bg-[#FFF7F9]">
      <summary className="grid cursor-pointer list-none gap-3 px-5 py-4 transition-colors hover:bg-[#FFF0F4]/70 md:grid-cols-[1.1fr_0.9fr_0.9fr_0.9fr_0.8fr] md:items-center">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <Badge variant={statusVariant(stage.detailStatus)}>{statusLabel(stage.detailStatus)}</Badge>
            <span className="font-mono text-[11px] text-ink-tertiary">{stage.pipelineStage}</span>
          </div>
          <p className="mt-2 truncate text-sm font-semibold text-ink-primary">{stage.stageLabel} / {stage.featureName}</p>
        </div>
        <MetricInline label="调用" value={formatNumber(stage.requestCount)} />
        <MetricInline label="Token" value={formatNumber(stage.totalTokens)} helper={`${formatNumber(stage.promptTokens)} / ${formatNumber(stage.completionTokens)}`} />
        <MetricInline label="平均延迟" value={formatLatency(stage.avgLatencyMs)} />
        <MetricInline label="明细" value={`${stage.calls.length} 条`} />
      </summary>
      <div className="border-t border-[#FECDD3]/70 bg-white px-5 py-4">
        {stage.calls.length === 0 ? (
          <p className="text-sm text-ink-secondary">
            {stage.detailStatus === "query_log_fallback"
              ? "当前仅有查询日志聚合，尚不能拆到具体模型、供应商和版本。"
              : "该环节尚未写入模型调用明细，需要在对应功能链路接入 kb_llm_call_logs。"}
          </p>
        ) : (
          <CallsTable calls={stage.calls} compact />
        )}
      </div>
    </details>
  );
}

function RecentCallsPanel({
  calls,
  loading,
  refreshing,
}: {
  calls: LlmCallUsageRecord[];
  loading: boolean;
  refreshing: boolean;
}) {
  const pagination = useClientPagination(calls, 20);
  return (
    <section className="relative overflow-hidden rounded-lg border border-[#E11D48]/24 bg-white/88 shadow-panel">
      <LoadingOverlay active={refreshing} tone="rose" label="正在刷新调用列表" />
      <div className="flex items-center justify-between border-b border-[#FECDD3]/80 bg-[#FFF0F4] px-5 py-4">
        <div>
          <p className="metric-label">模型调用</p>
          <h2 className="mt-1 text-base font-semibold text-ink-primary">最近调用明细</h2>
        </div>
        <Badge variant="neutral">{calls.length} 条</Badge>
      </div>
      {loading ? (
        <LoadingRows rows={5} />
      ) : calls.length === 0 ? (
        <EmptyState title="暂无模型调用明细" description="接入 kb_llm_call_logs 后将展示供应商、模型版本和 Token 消耗。" />
      ) : (
        <>
          <CallsTable calls={pagination.pageItems} />
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
        </>
      )}
    </section>
  );
}

function CallsTable({ calls, compact = false }: { calls: LlmCallUsageRecord[]; compact?: boolean }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[1040px] text-sm">
        <thead>
          <tr className="border-b border-[#FECDD3]/80 bg-[#FFF0F4] text-[11px] font-semibold uppercase tracking-[0.08em] text-[#BE123C]">
            <th className="px-4 py-2.5 text-left">使用时间</th>
            <th className="px-4 py-2.5 text-left">功能</th>
            <th className="px-4 py-2.5 text-left">供应商</th>
            <th className="px-4 py-2.5 text-left">模型 / 版本</th>
            <th className="px-4 py-2.5 text-right">输入</th>
            <th className="px-4 py-2.5 text-right">输出</th>
            <th className="px-4 py-2.5 text-right">总 Token</th>
            <th className="px-4 py-2.5 text-right">延迟</th>
            {!compact && <th className="px-4 py-2.5 text-left">请求 / 知识库</th>}
          </tr>
        </thead>
        <tbody className="divide-y divide-[#FECDD3]/70">
          {calls.map((call) => (
            <tr key={call.id} className="transition-colors hover:bg-[#FFF0F4]/70">
              <td className="px-4 py-3 font-mono text-xs text-ink-secondary">{formatTimestamp(call.createdAt)}</td>
              <td className="px-4 py-3">
                <p className="font-medium text-ink-primary">{call.featureName}</p>
                <p className="mt-1 font-mono text-[11px] text-ink-tertiary">{call.pipelineDomain} / {call.pipelineStage}</p>
              </td>
              <td className="px-4 py-3 font-mono text-xs text-ink-primary">{call.provider || "-"}</td>
              <td className="px-4 py-3">
                <p className="font-mono text-xs text-ink-primary">{call.modelName || "-"}</p>
                <p className="mt-1 font-mono text-[11px] text-ink-tertiary">{call.modelVersion || "未记录版本"}</p>
              </td>
              <td className="px-4 py-3 text-right font-mono text-ink-primary">{formatNumber(call.promptTokens)}</td>
              <td className="px-4 py-3 text-right font-mono text-ink-primary">{formatNumber(call.completionTokens)}</td>
              <td className="px-4 py-3 text-right font-mono font-semibold text-ink-primary">{formatNumber(call.totalTokens)}</td>
              <td className="px-4 py-3 text-right font-mono text-ink-primary">{formatLatency(call.latencyMs)}</td>
              {!compact && (
                <td className="max-w-[220px] px-4 py-3">
                  <p className="truncate font-mono text-[11px] text-ink-primary">{call.requestId || "-"}</p>
                  <p className="mt-1 truncate font-mono text-[11px] text-ink-tertiary">{call.kbId || "未绑定知识库"}</p>
                </td>
              )}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function MetricCard({
  label,
  value,
  helper,
  tone,
}: {
  label: string;
  value: string;
  helper: string;
  tone: "blue" | "teal" | "amber" | "violet";
}) {
  return (
    <div className="relative min-h-[118px] overflow-hidden rounded-md border border-[#E11D48]/22 bg-[radial-gradient(circle_at_88%_18%,rgba(225,29,72,0.14),transparent_36%),linear-gradient(135deg,#FFF0F4,#FFFFFF_58%)] p-4 shadow-[0_12px_28px_rgba(225,29,72,0.09)] after:pointer-events-none after:absolute after:-bottom-10 after:-right-8 after:h-24 after:w-24 after:rounded-full after:bg-[#E11D48]/12 after:content-['']">
      <div className={`tone-${tone} inline-flex rounded-md border px-2 py-1`}>
        <p className="metric-label text-current">{label}</p>
      </div>
      <p className="mt-3 metric-value">{value}</p>
      <p className="mt-2 text-xs text-ink-secondary">{helper}</p>
    </div>
  );
}

function SummaryCell({ label, value }: { label: string; value: string }) {
  return (
    <div className="border-b border-[#FECDD3]/80 bg-white/64 px-5 py-4 lg:border-b-0 lg:border-r last:lg:border-r-0">
      <p className="metric-label">{label}</p>
      <p className="mt-2 font-mono text-lg font-semibold text-ink-primary">{value}</p>
    </div>
  );
}

function MetricInline({ label, value, helper }: { label: string; value: string; helper?: string }) {
  return (
    <div className="min-w-0 text-left md:text-right">
      <p className="text-[11px] font-medium text-ink-tertiary">{label}</p>
      <p className="mt-1 font-mono text-sm font-semibold text-ink-primary">{value}</p>
      {helper && <p className="mt-1 font-mono text-[11px] text-ink-tertiary">{helper}</p>}
    </div>
  );
}

function emptyBucket(): TokenUsageBucket {
  return {
    requestCount: 0,
    promptTokens: 0,
    completionTokens: 0,
    totalTokens: 0,
    avgLatencyMs: 0,
  };
}

function statusLabel(status: string) {
  if (status === "recorded") return "已记录明细";
  if (status === "query_log_fallback") return "查询日志兜底";
  return "未接入明细";
}

function statusVariant(status: string): "success" | "warning" | "degraded" {
  if (status === "recorded") return "success";
  if (status === "query_log_fallback") return "degraded";
  return "warning";
}

function scopeLabel(scope?: string) {
  if (scope === "all_tenants") return "超级管理员：全量范围";
  if (scope === "tenant") return "当前租户范围";
  return "未启用身份过滤";
}

function formatCost(value: number) {
  return Number(value || 0).toFixed(6);
}

function formatHour(value: string) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return value;
  }
  return date.toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" });
}
