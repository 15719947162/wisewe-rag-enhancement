"use client";

import Image from "next/image";
import { useEffect, useRef, useState } from "react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { TablePagination, useClientPagination } from "@/components/ui/table-pagination";
import { ProgressBar } from "@/components/ui/progress";
import { Skeleton } from "@/components/ui/skeleton";
import { StatusBadge } from "@/components/ui/status-badge";
import { getBaseUrl, getKnowledgeBases } from "@/lib/api/client";
import { getIdentityHeaders } from "@/lib/auth/identity";
import { getRetrievalChannelLabel } from "@/lib/i18n/zh-cn";
import { formatKbId } from "@/lib/kb-id";
import type { AnswerResult, KnowledgeBase } from "@/lib/contracts/types";

const HISTORY_KEY = "rag_query_history";
const HISTORY_LIMIT = 100;
const BASE_URL = getBaseUrl();

function scoreColor(v: number) {
  if (v >= 0.9) return "text-status-success";
  if (v >= 0.7) return "text-status-warning";
  return "text-status-danger";
}

function loadHistory(): string[] {
  try {
    const parsed = JSON.parse(localStorage.getItem(HISTORY_KEY) ?? "[]");
    return Array.isArray(parsed) ? parsed.filter((item): item is string => typeof item === "string") : [];
  } catch {
    return [];
  }
}

function saveHistory(query: string) {
  const prev = loadHistory().filter((q) => q !== query);
  localStorage.setItem(HISTORY_KEY, JSON.stringify([query, ...prev].slice(0, HISTORY_LIMIT)));
}

function toApiAssetUrl(url: string): string {
  if (url.startsWith("http://") || url.startsWith("https://")) return url;
  return `${BASE_URL}${url.startsWith("/") ? "" : "/"}${url}`;
}

type QueryWorkspaceProps = {
  kbId?: string;
  fixedKnowledgeBase?: boolean;
};

export function QueryWorkspace({ kbId: initialKbId, fixedKnowledgeBase = false }: QueryWorkspaceProps) {
  const [kbs, setKbs] = useState<KnowledgeBase[]>([]);
  const [kbId, setKbId] = useState(initialKbId ?? "default");
  const [query, setQuery] = useState("");
  const [topK, setTopK] = useState(8);
  const [minScore, setMinScore] = useState(0.3);
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState<AnswerResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [kbLoadError, setKbLoadError] = useState<string | null>(null);
  const [history, setHistory] = useState<string[]>([]);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const historyPagination = useClientPagination(history, 20);

  useEffect(() => {
    getKnowledgeBases()
      .then((data) => {
        setKbs(data);
        if (!initialKbId && data[0]) setKbId(data[0].id);
        setKbLoadError(null);
      })
      .catch((err) => {
        setKbs([]);
        setKbLoadError(err instanceof Error ? err.message : "加载知识库失败");
      });
    setHistory(loadHistory());
  }, [initialKbId]);

  useEffect(() => {
    if (initialKbId) setKbId(initialKbId);
  }, [initialKbId]);

  async function handleSubmit() {
    if (!query.trim()) return;
    setLoading(true);
    setError(null);
    setResult(null);
    try {
      const res = await fetch(`${BASE_URL}/api/rag/query`, {
        method: "POST",
        headers: { "Content-Type": "application/json", ...getIdentityHeaders() },
        body: JSON.stringify({
          query,
          kb_id: kbId,
          top_k: topK,
          min_score: minScore,
          use_llm_check: false,
          use_llm_score: true,
        }),
        credentials: "include",
        cache: "no-store",
      });
      if (!res.ok) throw new Error(`查询请求失败：${res.status}`);
      const data: AnswerResult = await res.json();
      setResult(data);
      saveHistory(query);
      setHistory(loadHistory());
    } catch (e) {
      setError(e instanceof Error ? e.message : "未知错误");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="space-y-6">
      <div className="relative overflow-hidden rounded-lg border border-[#7C3AED]/24 bg-[radial-gradient(circle_at_88%_12%,rgba(124,58,237,0.16),transparent_34%),linear-gradient(135deg,#F4F0FF,#FFFFFF_58%)] px-6 py-5 shadow-panel">
        <div>
          <p className="text-[11px] font-semibold uppercase tracking-[0.14em] text-[#6D28D9]">RAG Validation Lab</p>
          <h1 className="text-[30px] font-bold leading-tight text-ink-primary">知识问答</h1>
          <p className="mt-1 text-sm text-ink-secondary">可解释的检索、重排与答案生成工作台。</p>
        </div>
        {result && (
          <StatusBadge status={result.cannotAnswer ? "failed" : "success"}>
            {result.cannotAnswer ? "无法回答" : "答案已生成"}
          </StatusBadge>
        )}
      </div>

      <section className="grid gap-4 xl:grid-cols-[280px_minmax(0,1fr)_340px]">
        <div className="space-y-3">
          {kbLoadError && <div className="rounded-sm border border-status-danger bg-[#FEF2F2] px-3 py-2 text-sm text-status-danger">{kbLoadError}</div>}

          <div className="rounded-lg border border-[#DDD6FE] bg-[linear-gradient(135deg,#F5F3FF,#FFFFFF)] p-4 shadow-sm">
            <label className="mb-1.5 block text-[11px] font-semibold uppercase tracking-[0.08em] text-ink-tertiary">知识库</label>
            {fixedKnowledgeBase ? (
              <div className="rounded-md border border-[#7C3AED]/20 bg-[#F4F0FF]/55 px-3 py-2 text-[13px] text-ink-primary">
                {kbs.find((kb) => kb.id === kbId)?.name ?? formatKbId(kbId)}
              </div>
            ) : (
              <select
                value={kbId}
                onChange={(e) => setKbId(e.target.value)}
                className="h-9 w-full rounded-md border border-[#DDD6FE] bg-white px-3 text-[13px] text-ink-primary shadow-sm hover:border-[#7C3AED]/45 focus:outline-none focus:ring-2 focus:ring-[#7C3AED]/25"
              >
                {kbs.map((kb) => (
                  <option key={kb.id} value={kb.id}>{kb.name}</option>
                ))}
              </select>
            )}
          </div>

          <div className="rounded-lg border border-[#DDD6FE] bg-[linear-gradient(135deg,#FFFFFF,#FFF7FB)] p-4 shadow-sm">
            <label className="mb-1.5 block text-[11px] font-semibold uppercase tracking-[0.08em] text-ink-tertiary">查询问题</label>
            <textarea
              ref={textareaRef}
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey) {
                  e.preventDefault();
                  handleSubmit();
                }
              }}
              placeholder="输入问题，Enter 提交，Shift+Enter 换行"
              rows={4}
              className="w-full resize-none rounded-md border border-[#DDD6FE] bg-white px-3 py-2 text-[13px] text-ink-primary placeholder:text-ink-tertiary shadow-sm hover:border-[#7C3AED]/45 focus:outline-none focus:ring-2 focus:ring-[#7C3AED]/25"
            />
            <Button variant="primary" className="mt-2 w-full" loading={loading} onClick={handleSubmit}>执行查询</Button>
          </div>

          <div className="rounded-lg border border-[#DDD6FE] bg-[linear-gradient(135deg,#F5F3FF,#FFFFFF)] p-4 shadow-sm">
            <p className="mb-3 text-[11px] font-semibold uppercase tracking-[0.08em] text-ink-tertiary">参数</p>
            <div className="space-y-3">
              <Slider label="召回候选" value={topK} min={1} max={20} step={1} onChange={setTopK} />
              <Slider label="最低分" value={minScore} min={0} max={1} step={0.1} onChange={setMinScore} decimals={1} accent="#EC4899" />
            </div>
          </div>

          {history.length > 0 && (
            <div className="overflow-hidden rounded-lg border border-[#DDD6FE] bg-[radial-gradient(circle_at_92%_18%,rgba(124,58,237,0.12),transparent_34%),linear-gradient(135deg,#F4F0FF,#FFFFFF_62%)] shadow-sm">
              <div className="flex items-center justify-between gap-3 px-4 pt-4">
                <p className="text-[11px] font-semibold uppercase tracking-[0.08em] text-ink-tertiary">历史问题</p>
                <span className="font-mono text-[11px] text-ink-tertiary">{history.length}/{HISTORY_LIMIT}</span>
              </div>
              <div className="space-y-1 px-4 py-3">
                {historyPagination.pageItems.map((q, i) => (
                  <button key={`${historyPagination.startIndex + i}-${q}`} onClick={() => setQuery(q)} className="w-full truncate rounded-md px-2 py-1.5 text-left text-xs text-ink-secondary transition-colors hover:bg-[#F5F3FF] hover:text-[#6D28D9]">
                    {q}
                  </button>
                ))}
              </div>
              <TablePagination
                page={historyPagination.page}
                pageSize={historyPagination.pageSize}
                total={historyPagination.total}
                pageCount={historyPagination.pageCount}
                startIndex={historyPagination.startIndex}
                endIndex={historyPagination.endIndex}
                onPageChange={historyPagination.setPage}
                onPageSizeChange={historyPagination.setPageSize}
                itemLabel="条问题"
                variant="compact"
              />
            </div>
          )}
        </div>

        <CandidatePanel loading={loading} result={result} />
        <AnswerPanel loading={loading} error={error} result={result} />
      </section>
    </div>
  );
}

function Slider({
  label,
  value,
  min,
  max,
  step,
  decimals = 0,
  accent = "#7C3AED",
  onChange,
}: {
  label: string;
  value: number;
  min: number;
  max: number;
  step: number;
  decimals?: number;
  accent?: string;
  onChange: (value: number) => void;
}) {
  return (
    <div>
      <div className="flex items-center justify-between text-xs">
        <span className="text-ink-secondary">{label}</span>
        <span className="font-mono text-ink-primary">{value.toFixed(decimals)}</span>
      </div>
      <input type="range" min={min} max={max} step={step} value={value} onChange={(e) => onChange(Number(e.target.value))} className="mt-1 w-full" style={{ accentColor: accent }} />
    </div>
  );
}

function CandidatePanel({ loading, result }: { loading: boolean; result: AnswerResult | null }) {
  if (loading) {
    return (
      <div className="space-y-3">
        {[1, 2, 3].map((i) => (
          <div key={i} className="space-y-2 rounded-lg border border-[#DDD6FE] bg-white p-4 shadow-sm">
            <Skeleton className="h-4 w-32" />
            <Skeleton className="h-3 w-full" />
            <Skeleton className="h-3 w-3/4" />
          </div>
        ))}
      </div>
    );
  }

  if (!result) {
    return (
      <div className="flex h-48 items-center justify-center rounded-lg border border-dashed border-[#C4B5FD] bg-[#F5F3FF]/65 text-sm text-[#6D28D9]">
        执行查询后，这里会展示召回候选。
      </div>
    );
  }

  return (
    <div className="overflow-hidden rounded-lg border border-[#7C3AED]/24 bg-white/86 shadow-panel">
      <div className="border-b border-[#DDD6FE]/80 bg-[linear-gradient(90deg,rgba(124,58,237,0.12),rgba(236,72,153,0.08),rgba(255,255,255,0.76))] px-5 py-3">
        <p className="text-[13px] font-semibold text-ink-primary">召回候选 {result.candidates.length} 条</p>
      </div>
      <div className="divide-y divide-[#DDD6FE]/70 bg-white/70">
        {result.candidates.map((c, i) => (
          <article key={c.id} className="px-5 py-4 transition-colors hover:bg-[#F5F3FF]/70">
            <div className="flex items-start justify-between gap-3">
              <div className="flex items-center gap-2">
                <span className="font-mono text-xs text-ink-tertiary">#{i + 1}</span>
                <span className={`font-mono text-sm font-semibold ${scoreColor(c.score)}`}>{c.score.toFixed(3)}</span>
                <Badge variant="neutral">{c.strategy ?? c.layer}</Badge>
              </div>
              <div className="text-right">
                <p className="text-xs text-ink-tertiary">{c.documentName ?? c.source}</p>
                <p className="font-mono text-xs text-ink-tertiary">{c.location ?? `P.${c.page}${c.chunkIndex !== undefined ? ` · #${c.chunkIndex + 1}` : ""}`}</p>
              </div>
            </div>
            {c.isImageChunk && c.imageUrl ? (
              <div className="mt-3 overflow-hidden rounded-lg border border-[#DDD6FE] bg-[#F5F3FF]">
                <Image src={toApiAssetUrl(c.imageUrl)} alt={c.title || c.content || `召回图片 ${i + 1}`} width={900} height={560} className="max-h-[360px] w-full object-contain" unoptimized />
              </div>
            ) : null}
            <p className="mt-2 line-clamp-3 text-sm leading-relaxed text-ink-secondary">{c.content}</p>
          </article>
        ))}
      </div>
    </div>
  );
}

function AnswerPanel({ loading, error, result }: { loading: boolean; error: string | null; result: AnswerResult | null }) {
  if (loading) {
    return (
      <>
        <div className="space-y-2 rounded-lg border border-[#DDD6FE] bg-white p-4 shadow-sm">
          <Skeleton className="h-3 w-20" />
          <Skeleton className="h-4 w-full" />
          <Skeleton className="h-4 w-5/6" />
          <Skeleton className="h-4 w-4/6" />
        </div>
        <div className="space-y-3 rounded-lg border border-[#DDD6FE] bg-white p-4 shadow-sm">
          {[1, 2, 3].map((i) => <Skeleton key={i} className="h-6 w-full rounded" />)}
        </div>
      </>
    );
  }

  if (!result) {
    return (
      <div className="space-y-3">
        {error && <div className="rounded-sm border border-status-warning bg-[#FFFBEB] p-3 text-sm text-[#D97706]">{error}</div>}
        <div className="flex h-48 items-center justify-center rounded-lg border border-dashed border-[#C4B5FD] bg-[#F5F3FF]/65 text-sm text-[#6D28D9]">
          答案生成后会显示在这里。
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-3">
      {error && <div className="rounded-sm border border-status-warning bg-[#FFFBEB] p-3 text-sm text-[#D97706]">{error}</div>}
      <div className="rounded-lg border border-[#7C3AED]/24 bg-[radial-gradient(circle_at_92%_18%,rgba(124,58,237,0.12),transparent_34%),linear-gradient(135deg,#F4F0FF,#FFFFFF_62%)] p-4 shadow-sm">
        <p className="text-[11px] font-semibold uppercase tracking-[0.08em] text-ink-tertiary">生成答案</p>
        {result.cannotAnswer ? (
          <div className="mt-3 rounded-md border border-[#FED7AA] bg-[#FFF7ED] p-3 text-sm text-[#C2410C]">当前资料不足，系统未生成确定答案。</div>
        ) : (
          <p className="mt-2 text-sm leading-relaxed text-ink-primary">{result.answer}</p>
        )}
      </div>

      {result.citations.length > 0 && (
        <div className="rounded-lg border border-[#7C3AED]/24 bg-[radial-gradient(circle_at_92%_18%,rgba(124,58,237,0.12),transparent_34%),linear-gradient(135deg,#F4F0FF,#FFFFFF_62%)] p-4 shadow-sm">
          <p className="text-[11px] font-semibold uppercase tracking-[0.08em] text-ink-tertiary">引用来源</p>
          <div className="mt-3 space-y-2">
            {result.citations.map((c) => (
              <div key={c.chunkId} className="rounded-lg border border-[#DDD6FE] bg-[#F5F3FF]/70 p-3">
                <div className="flex items-center gap-2">
                  <span className="font-mono text-xs font-semibold text-brand-primary">[{c.index}]</span>
                  <span className="text-xs text-ink-tertiary">{c.documentName ?? c.source} {c.location ?? `P.${c.page}`}</span>
                </div>
                <p className="mt-1 line-clamp-2 text-xs text-ink-secondary">{c.snippet}</p>
              </div>
            ))}
          </div>
        </div>
      )}

      <div className="rounded-lg border border-[#FBCFE8] bg-[linear-gradient(135deg,#FFF7FB,#FFFFFF)] p-4 shadow-sm">
        <p className="text-[11px] font-semibold uppercase tracking-[0.08em] text-ink-tertiary">答案评分</p>
        <div className="mt-3 space-y-3">
          {[
            { label: "相关性", value: result.scores.relevanceScore },
            { label: "忠实度", value: result.scores.faithfulnessScore },
            { label: "LLM 分", value: result.scores.llmScore ?? null },
          ].map((s) => (
            <div key={s.label}>
              <div className="flex items-center justify-between text-xs">
                <span className="text-ink-secondary">{s.label}</span>
                <span className={`font-mono font-semibold ${s.value === null ? "text-ink-tertiary" : scoreColor(s.value)}`}>
                  {s.value === null ? "未评分" : s.value.toFixed(1)}
                </span>
              </div>
              <ProgressBar value={s.value ?? 0} max={1} colorClass="bg-brand-accent" className="mt-1" />
            </div>
          ))}
        </div>
        <div className="mt-4 border-t border-[#FBCFE8] pt-3 text-center">
          <p className="font-mono text-[40px] font-bold text-ink-primary">
            {formatOverallScore(result.scores.relevanceScore, result.scores.faithfulnessScore, result.scores.llmScore ?? null)}
          </p>
          <p className="text-xs text-ink-tertiary">综合分 / 5.0</p>
        </div>
      </div>

      {result.recallChannels.length > 0 && (
        <div className="rounded-lg border border-[#7C3AED]/24 bg-[radial-gradient(circle_at_92%_18%,rgba(124,58,237,0.12),transparent_34%),linear-gradient(135deg,#F4F0FF,#FFFFFF_62%)] p-4 shadow-sm">
          <p className="text-[11px] font-semibold uppercase tracking-[0.08em] text-ink-tertiary">召回通道</p>
          <div className="mt-3 space-y-2">
            {result.recallChannels.map((ch) => (
              <div key={ch.channel} className="flex items-center justify-between text-sm">
                <span className="text-ink-secondary">{getRetrievalChannelLabel(ch.channel)}</span>
                <span className="font-mono text-ink-primary">{ch.count}</span>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

function formatOverallScore(relevance: number, faithfulness: number, llmScore: number | null) {
  const values = [relevance, faithfulness, llmScore].filter((value): value is number => typeof value === "number");
  if (values.length === 0) return "0.0";
  return (((values.reduce((sum, value) => sum + value, 0) / values.length) * 5)).toFixed(1);
}
