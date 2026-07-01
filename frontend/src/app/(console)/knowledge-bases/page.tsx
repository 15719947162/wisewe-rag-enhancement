"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { EmptyState } from "@/components/ui/empty-state";
import { Input } from "@/components/ui/input";
import { Modal } from "@/components/ui/modal";
import { LoadingOverlay, Skeleton } from "@/components/ui/skeleton";
import {
  createKnowledgeBase,
  deleteKnowledgeBase,
  getKnowledgeBases,
  updateKnowledgeBase,
} from "@/lib/api/client";
import { formatNumber, formatTimestamp } from "@/lib/formatters";
import { buildKnowledgeBasePath, formatKbId } from "@/lib/kb-id";
import type { KnowledgeBase } from "@/lib/contracts/types";

const STRATEGIES = [
  { value: "paragraph", label: "段落切片" },
  { value: "fixed_length", label: "固定长度" },
  { value: "semantic", label: "语义切片" },
  { value: "separator", label: "分隔符切片" },
  { value: "llm", label: "LLM 切片" },
  { value: "hierarchical", label: "三层切片" },
];

const workspaceLinks = [
  { key: "documents", label: "文档", getHref: (kbId: string) => buildKnowledgeBasePath(kbId, "/documents") },
  { key: "ingestion", label: "入库", getHref: (kbId: string) => buildKnowledgeBasePath(kbId, "/ingestion") },
  { key: "query", label: "问答", getHref: (kbId: string) => buildKnowledgeBasePath(kbId, "/query") },
  { key: "evaluation", label: "评测", getHref: (kbId: string) => buildKnowledgeBasePath(kbId, "/evaluation") },
];

export default function KnowledgeBasesPage() {
  const router = useRouter();
  const [kbs, setKbs] = useState<KnowledgeBase[]>([]);
  const [loading, setLoading] = useState(true);
  const [createOpen, setCreateOpen] = useState(false);
  const [editTarget, setEditTarget] = useState<KnowledgeBase | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<KnowledgeBase | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const nameRef = useRef<HTMLInputElement>(null);
  const descRef = useRef<HTMLInputElement>(null);
  const [strategy, setStrategy] = useState("hierarchical");
  const [editName, setEditName] = useState("");
  const [editDescription, setEditDescription] = useState("");
  const [editStrategy, setEditStrategy] = useState("hierarchical");

  function getStrategyLabel(value: string) {
    return STRATEGIES.find((item) => item.value === value)?.label ?? value;
  }

  function openEdit(kb: KnowledgeBase) {
    setEditTarget(kb);
    setEditName(kb.name);
    setEditDescription(kb.description);
    setEditStrategy(kb.strategy || "hierarchical");
  }

  async function reload() {
    setLoading(true);
    setError(null);
    try {
      const data = await getKnowledgeBases();
      setKbs(data);
    } catch (err) {
      setKbs([]);
      setError(err instanceof Error ? err.message : "加载知识库失败");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    reload();
  }, []);

  useEffect(() => {
    if (new URLSearchParams(window.location.search).get("create") === "1") {
      setCreateOpen(true);
    }
  }, []);

  async function handleCreate() {
    const name = nameRef.current?.value.trim() ?? "";
    if (!name) return;
    setSubmitting(true);
    try {
      await createKnowledgeBase({
        name,
        description: descRef.current?.value ?? "",
        strategy,
      });
      setCreateOpen(false);
      await reload();
    } catch (err) {
      setError(err instanceof Error ? err.message : "创建知识库失败");
    } finally {
      setSubmitting(false);
    }
  }

  async function handleDelete() {
    if (!deleteTarget) return;
    setSubmitting(true);
    try {
      await deleteKnowledgeBase(deleteTarget.id);
      setDeleteTarget(null);
      await reload();
    } catch (err) {
      setError(err instanceof Error ? err.message : "删除知识库失败");
    } finally {
      setSubmitting(false);
    }
  }

  async function handleUpdate() {
    if (!editTarget || !editName.trim()) return;
    setSubmitting(true);
    try {
      await updateKnowledgeBase(editTarget.id, {
        name: editName.trim(),
        description: editDescription,
        strategy: editStrategy,
      });
      setEditTarget(null);
      await reload();
    } catch (err) {
      setError(err instanceof Error ? err.message : "更新知识库失败");
    } finally {
      setSubmitting(false);
    }
  }

  const totalDocs = kbs.reduce((sum, kb) => sum + kb.docCount, 0);
  const totalChunks = kbs.reduce((sum, kb) => sum + kb.chunkCount, 0);
  const hasLoaded = !loading || kbs.length > 0 || Boolean(error);

  return (
    <div className="space-y-6">
      <section className="preview-panel p-6 [--panel-tone:#00A889]">
        <div className="flex flex-col gap-5 lg:flex-row lg:items-end lg:justify-between">
          <div className="max-w-2xl">
            <p className="preview-eyebrow text-[#00A889]">Knowledge Base First</p>
            <h1 className="mt-2 text-[32px] font-extrabold leading-tight text-ink-primary">知识库工作台</h1>
            <p className="mt-3 text-sm leading-6 text-ink-secondary">
              先选择知识库，再进入单库工作台处理具体任务。
            </p>
          </div>
          <div className="flex items-center gap-3">
            <Button variant="primary" onClick={() => setCreateOpen(true)}>
              <PlusIcon /> 新建知识库
            </Button>
          </div>
        </div>

        <div className="relative z-10 mt-6 grid gap-3 md:grid-cols-3">
          <SummaryCard label="知识库总数" value={String(kbs.length)} hint="可进入单库工作台继续处理。" tone="knowledge" />
          <SummaryCard label="文档总数" value={formatNumber(totalDocs)} hint="跨库统计，仅作全局巡检使用。" tone="ingestion" />
          <SummaryCard label="切块总数" value={formatNumber(totalChunks)} hint="反映当前向量化知识规模。" tone="rag" />
        </div>
      </section>

      {error && (
        <div className="rounded-md border border-status-danger bg-[#FEF2F2] px-4 py-3 text-sm text-status-danger">
          {error}
        </div>
      )}

      <section id="knowledge-base-list" className="space-y-4 scroll-mt-24">
        <div className="flex items-center justify-between">
          <div>
            <h2 className="text-lg font-semibold text-ink-primary">知识库列表</h2>
            <p className="mt-1 text-sm text-ink-secondary">
              当前 {kbs.length} 个知识库，累计 {formatNumber(totalDocs)} 篇文档、{formatNumber(totalChunks)} 个切块。
            </p>
          </div>
        </div>

        <div className="relative">
          <LoadingOverlay active={loading && hasLoaded} tone="teal" label="正在刷新知识库" />
          {loading && !hasLoaded ? (
            <div className="grid gap-4 xl:grid-cols-2">
              {[1, 2, 3, 4].map((i) => (
                <Skeleton key={i} className="h-72 rounded-lg" />
              ))}
            </div>
          ) : kbs.length === 0 ? (
            <EmptyState
              title="还没有知识库"
              description="先创建一个知识库，再上传文档并进入单库工作台。"
              icon={<DatabaseEmptyIcon />}
            />
          ) : (
            <div className="grid gap-4 xl:grid-cols-2 animate-data-enter">
              {kbs.map((kb) => (
                <Card key={kb.id} padding="none" accent="knowledge" hover>
                  <div className="relative z-10 border-b border-[#00A889]/14 bg-[linear-gradient(90deg,rgba(0,168,137,0.10),rgba(255,255,255,0.78))] px-6 py-5 pt-6">
                    <div className="flex items-start justify-between gap-3">
                      <div className="min-w-0">
                        <div className="flex items-center gap-2">
                          <h3 className="truncate text-lg font-semibold text-ink-primary">{kb.name}</h3>
                          <Badge variant="success">{kb.docCount} 篇文档</Badge>
                        </div>
                        <p className="mt-2 line-clamp-2 text-sm leading-6 text-ink-secondary">
                          {kb.description || "暂无描述，可进入工作台后继续上传文档、执行问答和查看评测。"}
                        </p>
                        <p className="mt-3 font-mono text-xs text-ink-tertiary">{formatKbId(kb.id)}</p>
                      </div>
                      <div className="flex shrink-0 items-center gap-1">
                        <button
                          type="button"
                          onClick={() => openEdit(kb)}
                          className="rounded-md p-2 text-ink-tertiary transition-colors hover:bg-active hover:text-brand-primary"
                          aria-label={`编辑 ${kb.name}`}
                        >
                          <EditIcon />
                        </button>
                        <button
                          type="button"
                          onClick={() => setDeleteTarget(kb)}
                          className="rounded-md p-2 text-ink-tertiary transition-colors hover:bg-[#FEF2F2] hover:text-status-danger"
                          aria-label={`删除 ${kb.name}`}
                        >
                          <TrashIcon />
                        </button>
                      </div>
                    </div>
                  </div>

                  <div className="relative z-10 grid grid-cols-3 gap-px bg-[#00A889]/14">
                    <MetricBlock label="切块总数" value={formatNumber(kb.chunkCount)} tone="knowledge" />
                    <MetricBlock label="默认切片" value={getStrategyLabel(kb.strategy || "hierarchical")} tone="rag" />
                    <MetricBlock label="最后更新" value={formatTimestamp(kb.lastUpdated)} tone="governance" mono />
                  </div>

                  <div className="relative z-10 px-6 py-5">
                    <div className="grid gap-2 sm:grid-cols-2">
                      {workspaceLinks.map((item) => (
                        <Link
                          key={item.key}
                          href={item.getHref(kb.id)}
                          className="flex items-center justify-between rounded-lg border border-[#00A889]/15 bg-[#E9FBF5]/45 px-4 py-3 text-sm font-medium text-ink-primary transition-colors hover:border-[#00A889]/40 hover:bg-[#E9FBF5]"
                        >
                          <span>{item.label}</span>
                          <ArrowRightIcon />
                        </Link>
                      ))}
                    </div>

                    <div className="mt-4 flex flex-wrap gap-2">
                      <Link
                        href={buildKnowledgeBasePath(kb.id)}
                        className="inline-flex items-center rounded-md bg-gradient-to-r from-[#00A889] to-[#365DFF] px-3 py-2 text-sm font-medium text-ink-inverse shadow-[0_10px_24px_rgba(0,168,137,0.18)] transition-colors hover:brightness-95"
                      >
                        进入知识库总览
                      </Link>
                    </div>
                  </div>
                </Card>
              ))}
            </div>
          )}
        </div>
      </section>

      <Modal
        open={createOpen}
        onClose={() => {
          setCreateOpen(false);
          router.replace("/knowledge-bases");
        }}
        title="新建知识库"
        size="sm"
        footer={
          <>
            <Button variant="secondary" onClick={() => setCreateOpen(false)}>取消</Button>
            <Button variant="primary" loading={submitting} onClick={handleCreate}>创建</Button>
          </>
        }
      >
        <KnowledgeBaseForm nameRef={nameRef} descRef={descRef} strategy={strategy} setStrategy={setStrategy} />
      </Modal>

      <Modal
        open={!!editTarget}
        onClose={() => setEditTarget(null)}
        title="编辑知识库"
        size="sm"
        footer={
          <>
            <Button variant="secondary" onClick={() => setEditTarget(null)}>取消</Button>
            <Button variant="primary" loading={submitting} onClick={handleUpdate}>保存</Button>
          </>
        }
      >
        <div className="space-y-4">
          <div>
            <label className="mb-1.5 block text-[13px] font-medium text-ink-primary">名称 *</label>
            <Input value={editName} onChange={(e) => setEditName(e.target.value)} placeholder="例如：教材知识库" />
          </div>
          <div>
            <label className="mb-1.5 block text-[13px] font-medium text-ink-primary">描述</label>
            <Input value={editDescription} onChange={(e) => setEditDescription(e.target.value)} placeholder="可选描述" />
          </div>
          <div>
            <label className="mb-1.5 block text-[13px] font-medium text-ink-primary">默认切片策略</label>
            <StrategySelect value={editStrategy} onChange={setEditStrategy} />
          </div>
        </div>
      </Modal>

      <Modal
        open={!!deleteTarget}
        onClose={() => setDeleteTarget(null)}
        title="删除知识库"
        size="sm"
        footer={
          <>
            <Button variant="secondary" onClick={() => setDeleteTarget(null)}>取消</Button>
            <Button variant="danger" loading={submitting} onClick={handleDelete}>确认删除</Button>
          </>
        }
      >
        <p className="text-sm leading-6 text-ink-secondary">
          确定要删除知识库 <span className="font-semibold text-ink-primary">{deleteTarget?.name}</span> 吗？
          此操作会移除其下所有文档与向量数据，且不可恢复。
        </p>
      </Modal>
    </div>
  );
}

function KnowledgeBaseForm({
  nameRef,
  descRef,
  strategy,
  setStrategy,
}: {
  nameRef: React.RefObject<HTMLInputElement | null>;
  descRef: React.RefObject<HTMLInputElement | null>;
  strategy: string;
  setStrategy: (value: string) => void;
}) {
  return (
    <div className="space-y-4">
      <div>
        <label className="mb-1.5 block text-[13px] font-medium text-ink-primary">名称 *</label>
        <Input ref={nameRef} placeholder="例如：教材知识库" />
      </div>
      <div>
        <label className="mb-1.5 block text-[13px] font-medium text-ink-primary">描述</label>
        <Input ref={descRef} placeholder="可选描述" />
      </div>
      <div>
        <label className="mb-1.5 block text-[13px] font-medium text-ink-primary">默认切片策略</label>
        <StrategySelect value={strategy} onChange={setStrategy} />
      </div>
    </div>
  );
}

function StrategySelect({ value, onChange }: { value: string; onChange: (value: string) => void }) {
  return (
    <select
      value={value}
      onChange={(e) => onChange(e.target.value)}
      className="h-[34px] w-full rounded-sm border border-border-subtle bg-panel px-3 text-[13px] text-ink-primary focus:outline-none focus:ring-2 focus:ring-border-focus"
    >
      {STRATEGIES.map((item) => (
        <option key={item.value} value={item.value}>
          {item.label}
        </option>
      ))}
    </select>
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
    knowledge: {
      border: "border-[#00A889]/30",
      bg: "bg-[radial-gradient(circle_at_88%_18%,rgba(0,168,137,0.20),transparent_36%),linear-gradient(135deg,#DFFAF1_0%,#FFFFFF_58%,#E9FBF5_100%)]",
      label: "text-[#047857]",
      value: "text-[#071B18]",
      circle: "bg-[#00A889]/24",
      glow: "shadow-[0_16px_36px_rgba(0,168,137,0.12)]",
    },
    ingestion: {
      border: "border-[#FF8A00]/32",
      bg: "bg-[radial-gradient(circle_at_88%_18%,rgba(255,138,0,0.22),transparent_36%),linear-gradient(135deg,#FFF1D0_0%,#FFFFFF_58%,#FFF5DD_100%)]",
      label: "text-[#C2410C]",
      value: "text-[#1F1308]",
      circle: "bg-[#FF8A00]/24",
      glow: "shadow-[0_16px_36px_rgba(255,138,0,0.12)]",
    },
    rag: {
      border: "border-[#7C3AED]/30",
      bg: "bg-[radial-gradient(circle_at_88%_18%,rgba(124,58,237,0.20),transparent_36%),linear-gradient(135deg,#EEE6FF_0%,#FFFFFF_58%,#F4F0FF_100%)]",
      label: "text-[#6D28D9]",
      value: "text-[#160B2D]",
      circle: "bg-[#7C3AED]/24",
      glow: "shadow-[0_16px_36px_rgba(124,58,237,0.12)]",
    },
  } as const;
  const style = toneMap[tone];

  return (
    <div className={["relative min-h-[128px] overflow-hidden rounded-lg border px-5 py-4", style.border, style.bg, style.glow].join(" ")}>
      <span className={["absolute -bottom-8 -right-5 h-24 w-24 rounded-full", style.circle].join(" ")} />
      <div className="relative">
        <p className={["text-[11px] font-extrabold uppercase tracking-[0.11em]", style.label].join(" ")}>{label}</p>
        <p className={["mt-4 font-mono text-[32px] font-bold leading-none", style.value].join(" ")}>{value}</p>
        <p className="mt-3 text-[13px] leading-5 text-ink-secondary">{hint}</p>
      </div>
    </div>
  );
}

function MetricBlock({
  label,
  value,
  tone,
  mono = false,
}: {
  label: string;
  value: string;
  tone: "knowledge" | "rag" | "governance";
  mono?: boolean;
}) {
  const toneMap = {
    knowledge: "bg-[#E9FBF5]/72 text-[#007F69]",
    rag: "bg-[#F4F0FF]/72 text-[#6D28D9]",
    governance: "bg-[#ECF8FF]/72 text-[#0369A1]",
  } as const;

  return (
    <div className="bg-white/76 px-5 py-4">
      <p className={["text-[11px] font-semibold uppercase tracking-[0.12em]", toneMap[tone]].join(" ")}>{label}</p>
      <p className={`mt-2 text-sm font-semibold text-ink-primary ${mono ? "font-mono" : ""}`}>{value}</p>
    </div>
  );
}

function PlusIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
      <path d="M12 5v14" />
      <path d="M5 12h14" />
    </svg>
  );
}

function TrashIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
      <path d="M3 6h18" />
      <path d="M8 6V4h8v2" />
      <path d="M19 6l-1 14H6L5 6" />
      <path d="M10 11v6" />
      <path d="M14 11v6" />
    </svg>
  );
}

function EditIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
      <path d="M12 20h9" />
      <path d="M16.5 3.5a2.12 2.12 0 0 1 3 3L7 19l-4 1 1-4Z" />
    </svg>
  );
}

function ArrowRightIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
      <path d="m9 18 6-6-6-6" />
    </svg>
  );
}

function DatabaseEmptyIcon() {
  return (
    <svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
      <ellipse cx="12" cy="5" rx="8" ry="3" />
      <path d="M4 5v14c0 1.66 3.58 3 8 3s8-1.34 8-3V5" />
      <path d="M4 12c0 1.66 3.58 3 8 3s8-1.34 8-3" />
    </svg>
  );
}
