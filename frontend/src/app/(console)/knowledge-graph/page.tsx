"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";

import { EmptyState } from "@/components/ui/empty-state";
import { getKnowledgeBases } from "@/lib/api/client";
import type { KnowledgeBase } from "@/lib/contracts/types";
import { formatNumber, formatTimestamp } from "@/lib/formatters";
import { buildKnowledgeBasePath, formatKbId } from "@/lib/kb-id";

export default function KnowledgeGraphIndexPage() {
  const [knowledgeBases, setKnowledgeBases] = useState<KnowledgeBase[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;

    async function loadKnowledgeBases() {
      setLoading(true);
      setError(null);
      try {
        const data = await getKnowledgeBases();
        if (!cancelled) setKnowledgeBases(data);
      } catch (err) {
        if (!cancelled) {
          setKnowledgeBases([]);
          setError(err instanceof Error ? err.message : "知识库列表加载失败");
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    }

    loadKnowledgeBases();
    return () => {
      cancelled = true;
    };
  }, []);

  const { totalDocs, totalChunks, activeGraphCount } = useMemo(() => {
    return {
      totalDocs: knowledgeBases.reduce((sum, kb) => sum + kb.docCount, 0),
      totalChunks: knowledgeBases.reduce((sum, kb) => sum + kb.chunkCount, 0),
      activeGraphCount: knowledgeBases.filter((kb) => kb.chunkCount > 0).length,
    };
  }, [knowledgeBases]);

  return (
    <div className="space-y-6">
      <section className="relative overflow-hidden rounded-lg border border-[#00A889]/20 bg-gradient-to-br from-white via-[#F7FFFC] to-[#F4F0FF] p-6 shadow-panel">
        <div className="flex flex-col gap-6 xl:flex-row xl:items-end xl:justify-between">
          <div className="max-w-3xl">
            <p className="text-[11px] font-semibold uppercase tracking-[0.14em] text-[#00A889]">Knowledge Graph</p>
            <h1 className="mt-2 text-[32px] font-bold leading-tight text-ink-primary">知识图谱工作台</h1>
            <p className="mt-3 text-sm leading-6 text-ink-secondary">
              先选择知识库，再进入对应的图谱画布，查看切片关系、实体关系和三元组预览。
            </p>
          </div>
          <Link
            href="/knowledge-bases"
            className="inline-flex h-9 cursor-pointer items-center justify-center rounded-md border border-[#00A889]/20 bg-white px-3 text-sm font-medium text-ink-secondary transition-colors hover:border-[#00A889]/40 hover:bg-[#E9FBF5] hover:text-[#007F69]"
          >
            返回知识库列表
          </Link>
        </div>

        <div className="mt-6 grid gap-3 md:grid-cols-3">
          <SummaryCard label="可选知识库" value={formatNumber(knowledgeBases.length)} hint="当前身份可访问的知识库范围。" tone="knowledge" />
          <SummaryCard label="已有图谱数据" value={formatNumber(activeGraphCount)} hint="存在切块数据的知识库可进入图谱画布。" tone="rag" />
          <SummaryCard label="累计切块" value={formatNumber(totalChunks)} hint={`${formatNumber(totalDocs)} 篇文档形成的图谱基础。`} tone="ingestion" />
        </div>
      </section>

      {error ? (
        <div className="rounded-md border border-status-danger bg-[#FEF2F2] px-4 py-3 text-sm text-status-danger">
          {error}
        </div>
      ) : null}

      <section className="space-y-4">
        <div>
          <h2 className="text-lg font-semibold text-ink-primary">选择知识库</h2>
          <p className="mt-1 text-sm text-ink-secondary">点击“打开图谱”后，会进入该知识库原有的图谱功能页。</p>
        </div>

        {loading ? (
          <div className="grid gap-4 xl:grid-cols-2">
            {[1, 2, 3, 4].map((item) => (
              <div
                key={item}
                className="h-[240px] animate-pulse rounded-lg border border-[#00A889]/12 bg-white/72"
              />
            ))}
          </div>
        ) : knowledgeBases.length === 0 ? (
          <div className="rounded-lg border border-dashed border-[#00A889]/24 bg-white">
            <EmptyState
              icon={<GraphEmptyIcon />}
              title="还没有可查看的知识库"
              description="先创建知识库并完成文档入库后，这里会出现对应的图谱入口。"
              action={
                <Link
                  href="/knowledge-bases"
                  className="inline-flex h-9 items-center rounded-md bg-brand-primary px-3 text-sm font-medium text-white transition-colors hover:bg-brand-primary-hover"
                >
                  新建或查看知识库
                </Link>
              }
            />
          </div>
        ) : (
          <div className="grid gap-4 xl:grid-cols-2">
            {knowledgeBases.map((kb) => (
              <article
                key={kb.id}
                className="relative overflow-hidden rounded-lg border border-[#00A889]/20 bg-[radial-gradient(circle_at_100%_100%,rgba(0,168,137,0.16),transparent_34%),linear-gradient(135deg,rgba(255,255,255,0.96),rgba(249,251,255,0.96)_62%)] p-5 shadow-[0_12px_30px_rgba(0,168,137,0.08)]"
              >
                <div className="relative z-10 flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
                  <div className="min-w-0">
                    <h3 className="truncate text-lg font-semibold text-ink-primary">{kb.name}</h3>
                    <p className="mt-2 line-clamp-2 text-sm leading-6 text-ink-secondary">
                      {kb.description || "暂无描述。进入图谱后可查看该知识库已入库内容形成的关系网络。"}
                    </p>
                    <p className="mt-3 font-mono text-xs text-ink-tertiary">{formatKbId(kb.id)}</p>
                  </div>
                  <GraphStatus ready={kb.chunkCount > 0} />
                </div>

                <div className="relative z-10 mt-5 grid gap-3 sm:grid-cols-3">
                  <MiniMetric label="文档" value={formatNumber(kb.docCount)} />
                  <MiniMetric label="切块" value={formatNumber(kb.chunkCount)} />
                  <MiniMetric label="更新" value={formatTimestamp(kb.lastUpdated)} mono />
                </div>

                <div className="relative z-10 mt-5 flex flex-wrap gap-2">
                  <Link
                    href={buildKnowledgeBasePath(kb.id, "/graph")}
                    className="inline-flex h-9 cursor-pointer items-center rounded-md bg-gradient-to-r from-[#00A889] to-[#365DFF] px-3 text-sm font-medium text-white shadow-[0_10px_24px_rgba(0,168,137,0.16)] transition-colors hover:brightness-95"
                  >
                    打开图谱
                  </Link>
                  <Link
                    href={buildKnowledgeBasePath(kb.id)}
                    className="inline-flex h-9 cursor-pointer items-center rounded-md border border-[#00A889]/20 bg-white px-3 text-sm font-medium text-ink-secondary transition-colors hover:border-[#00A889]/40 hover:bg-[#E9FBF5] hover:text-[#007F69]"
                  >
                    单库总览
                  </Link>
                </div>
              </article>
            ))}
          </div>
        )}
      </section>
    </div>
  );
}

function SummaryCard({
  label,
  value,
  hint,
  tone,
}: {
  label: string;
  value: string;
  hint: string;
  tone: "knowledge" | "ingestion" | "rag";
}) {
  const toneMap = {
    knowledge: "border-[#00A889]/28 bg-[#E9FBF5]/58 text-[#007F69]",
    ingestion: "border-[#FF8A00]/28 bg-[#FFF5DD]/64 text-[#B85F00]",
    rag: "border-[#7C3AED]/28 bg-[#F4F0FF]/64 text-[#6D28D9]",
  } as const;

  return (
    <div className={["min-h-[118px] rounded-lg border bg-white/82 px-5 py-4 shadow-sm", toneMap[tone]].join(" ")}>
      <p className="text-[11px] font-semibold uppercase tracking-[0.12em]">{label}</p>
      <p className="mt-4 font-mono text-[32px] font-bold leading-none text-ink-primary">{value}</p>
      <p className="mt-3 text-[13px] leading-5 text-ink-secondary">{hint}</p>
    </div>
  );
}

function MiniMetric({ label, value, mono = false }: { label: string; value: string; mono?: boolean }) {
  return (
    <div className="rounded-md border border-[#00A889]/14 bg-white/78 px-4 py-3">
      <p className="text-[11px] font-semibold uppercase tracking-[0.12em] text-[#007F69]">{label}</p>
      <p className={["mt-2 truncate text-sm font-semibold text-ink-primary", mono ? "font-mono" : ""].join(" ")}>
        {value}
      </p>
    </div>
  );
}

function GraphStatus({ ready }: { ready: boolean }) {
  return (
    <span
      className={[
        "inline-flex shrink-0 items-center rounded-full border px-2.5 py-1 text-xs font-medium",
        ready
          ? "border-[#00A889]/24 bg-[#E9FBF5] text-[#007F69]"
          : "border-[#94A3B8]/24 bg-[#F8FAFC] text-ink-tertiary",
      ].join(" ")}
    >
      {ready ? "可查看" : "待入库"}
    </span>
  );
}

function GraphEmptyIcon() {
  return (
    <svg width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round">
      <circle cx="5" cy="12" r="3" />
      <circle cx="19" cy="5" r="3" />
      <circle cx="19" cy="19" r="3" />
      <path d="M7.7 10.7 16.3 6.3" />
      <path d="M7.7 13.3 16.3 17.7" />
    </svg>
  );
}
