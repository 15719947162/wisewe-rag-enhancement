"use client";

import { useEffect, useState } from "react";
import { ContextRail } from "@/components/layout/context-rail";
import { EmptyState } from "@/components/ui/empty-state";
import { LoadingOverlay, LoadingRows, Skeleton } from "@/components/ui/skeleton";
import { TablePagination, useClientPagination } from "@/components/ui/table-pagination";
import { getEvaluations } from "@/lib/api/client";
import { formatScore } from "@/lib/formatters";
import type { EvaluationRecord } from "@/lib/contracts/types";

function scoreClass(v: number | null) {
  if (v === null) return "text-ink-tertiary";
  if (v >= 4) return "text-status-success font-semibold";
  if (v >= 3) return "text-status-warning";
  return "text-status-danger";
}

export default function EvaluationPage() {
  const [evaluations, setEvaluations] = useState<EvaluationRecord[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    getEvaluations()
      .then((data) => {
        setEvaluations(data);
        setError(null);
      })
      .catch((err) => {
        setEvaluations([]);
        setError(err instanceof Error ? err.message : "加载评测数据失败");
      })
      .finally(() => {
        setLoading(false);
      });
  }, []);

  const avgRelevance = evaluations.length
    ? evaluations.reduce((s, e) => s + e.relevanceScore, 0) / evaluations.length
    : 0;
  const avgFaithfulness = evaluations.length
    ? evaluations.reduce((s, e) => s + e.faithfulnessScore, 0) / evaluations.length
    : 0;
  const cannotAnswerCount = evaluations.filter((e) => e.cannotAnswer).length;
  const hasLoaded = !loading || evaluations.length > 0 || Boolean(error);
  const evaluationsPagination = useClientPagination(evaluations, 20);

  return (
    <div className="space-y-6">
      <ContextRail
        title="全局评测记录"
        description="查看所有知识库的 RAG 评测样本，聚合相关性、忠实度和 LLM 评分诊断。"
      />

      <div className="relative overflow-hidden rounded-lg border border-[#7C3AED]/24 bg-[radial-gradient(circle_at_88%_12%,rgba(124,58,237,0.16),transparent_34%),linear-gradient(135deg,#F4F0FF,#FFFFFF_58%)] px-6 py-5 shadow-panel">
        <div className="relative flex items-start justify-between gap-4">
          <div>
            <h1 className="text-[30px] font-bold leading-tight text-ink-primary">策略评测</h1>
            <p className="mt-1 text-sm text-ink-secondary">相关性、忠实度与 LLM 评分诊断</p>
          </div>
          <button className="inline-flex h-[34px] items-center gap-1.5 rounded-md bg-gradient-to-r from-[#7C3AED] to-[#EC4899] px-3.5 text-[13px] font-medium text-ink-inverse shadow-[0_10px_24px_rgba(124,58,237,0.22)] hover:brightness-95">
            新建实验
          </button>
        </div>
      </div>

      <section className="grid gap-4 sm:grid-cols-3">
        {loading ? (
          [1, 2, 3].map((i) => <Skeleton key={i} className="h-28 rounded-md" />)
        ) : (
          <>
            <MetricTile label="平均相关性" value={avgRelevance.toFixed(2)} />
            <MetricTile label="平均忠实度" value={avgFaithfulness.toFixed(2)} />
            <div className="relative overflow-hidden rounded-md border border-[#EC4899]/24 bg-[radial-gradient(circle_at_88%_18%,rgba(236,72,153,0.16),transparent_36%),linear-gradient(135deg,#FFF7FB,#FFFFFF_58%)] p-5 shadow-[0_12px_28px_rgba(236,72,153,0.10)]">
              <p className="text-[12px] font-medium uppercase tracking-[0.08em] text-ink-tertiary">资料不足未回答 / 总数</p>
              <p className="mt-2 font-mono text-[40px] font-bold text-ink-primary">
                {cannotAnswerCount}
                <span className="text-xl text-ink-tertiary"> / {evaluations.length}</span>
              </p>
            </div>
          </>
        )}
      </section>

      {error && <div className="rounded-sm border border-status-danger bg-[#FEF2F2] px-4 py-3 text-sm text-status-danger">{error}</div>}

      <section className="relative overflow-hidden rounded-lg border border-[#7C3AED]/24 bg-white/86 shadow-panel">
        <LoadingOverlay active={loading && hasLoaded} tone="violet" label="正在刷新评测" />
        <div className="border-b border-[#DDD6FE]/80 bg-[linear-gradient(90deg,rgba(124,58,237,0.12),rgba(236,72,153,0.08),rgba(255,255,255,0.76))] px-5 py-4">
          <p className="text-[11px] font-semibold uppercase tracking-[0.1em] text-[#6D28D9]">Evaluation</p>
          <h2 className="mt-1 text-base font-semibold text-ink-primary">评测样本列表</h2>
        </div>
        {loading && !hasLoaded ? (
          <LoadingRows rows={5} />
        ) : evaluations.length === 0 ? (
          <EmptyState title="暂无评测数据" description="运行问答后将自动生成评测记录。" />
        ) : (
          <div className="overflow-x-auto">
            <EvaluationTable records={evaluationsPagination.pageItems} />
            <TablePagination
              page={evaluationsPagination.page}
              pageSize={evaluationsPagination.pageSize}
              total={evaluationsPagination.total}
              pageCount={evaluationsPagination.pageCount}
              startIndex={evaluationsPagination.startIndex}
              endIndex={evaluationsPagination.endIndex}
              onPageChange={evaluationsPagination.setPage}
              onPageSizeChange={evaluationsPagination.setPageSize}
            />
          </div>
        )}
      </section>
    </div>
  );
}

function MetricTile({ label, value }: { label: string; value: string }) {
  return (
    <div className="relative overflow-hidden rounded-md border border-[#7C3AED]/24 bg-[radial-gradient(circle_at_88%_18%,rgba(124,58,237,0.16),transparent_36%),linear-gradient(135deg,#F4F0FF,#FFFFFF_58%)] p-5 shadow-[0_12px_28px_rgba(124,58,237,0.10)]">
      <p className="text-[12px] font-medium uppercase tracking-[0.08em] text-ink-tertiary">{label}</p>
      <p className="mt-2 font-mono text-[40px] font-bold text-ink-primary">{value}</p>
    </div>
  );
}

function EvaluationTable({ records }: { records: EvaluationRecord[] }) {
  return (
    <table className="w-full text-sm">
      <thead>
        <tr className="border-b border-[#DDD6FE]/80 bg-[linear-gradient(90deg,#F5F3FF,#FFF7FB)] text-[12px] font-medium uppercase tracking-[0.06em] text-[#6D28D9]">
          <th className="px-4 py-2.5 text-left">查询</th>
          <th className="px-4 py-2.5 text-right">相关性</th>
          <th className="px-4 py-2.5 text-right">忠实度</th>
          <th className="px-4 py-2.5 text-right">LLM 分</th>
          <th className="px-4 py-2.5 text-left">状态</th>
        </tr>
      </thead>
      <tbody className="divide-y divide-[#DDD6FE]/70">
        {records.map((record) => (
          <tr key={record.id} className="transition-colors hover:bg-[#F5F3FF]/70">
            <td className="max-w-xs px-4 py-3">
              <p className="truncate font-medium text-ink-primary">{record.query}</p>
              {record.failureReason && <p className="mt-0.5 truncate text-xs text-status-warning">{record.failureReason}</p>}
            </td>
            <td className={`px-4 py-3 text-right font-mono ${scoreClass(record.relevanceScore)}`}>{formatScore(record.relevanceScore)}</td>
            <td className={`px-4 py-3 text-right font-mono ${scoreClass(record.faithfulnessScore)}`}>{formatScore(record.faithfulnessScore)}</td>
            <td className={`px-4 py-3 text-right font-mono ${scoreClass(record.llmScore ?? null)}`}>{formatScore(record.llmScore ?? null)}</td>
            <td className="px-4 py-3">
              {record.cannotAnswer ? (
                <span className="inline-flex items-center gap-1 rounded-[999px] bg-[#FEF2F2] px-2 py-0.5 text-xs font-medium text-[#C2410C]">资料不足，未回答</span>
              ) : (
                <span className="inline-flex items-center gap-1 rounded-[999px] bg-[#F0FDF4] px-2 py-0.5 text-xs font-medium text-[#1F9D55]">已生成答案</span>
              )}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
