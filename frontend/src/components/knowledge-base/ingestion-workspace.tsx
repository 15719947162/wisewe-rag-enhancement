"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Modal } from "@/components/ui/modal";
import { ParseKeyMetricsPanel } from "@/components/knowledge-base/parse-key-metrics-panel";
import { ProgressBar, StepProgress } from "@/components/ui/progress";
import { StatusBadge } from "@/components/ui/status-badge";
import { TablePagination, useClientPagination } from "@/components/ui/table-pagination";
import {
  confirmChunkDrafts,
  deleteChunkDraft,
  deleteIngestionTask,
  getChunkDrafts,
  getIngestionTask,
  getIngestionTasks,
  mergeChunkDrafts,
  updateChunkDraft,
  uploadDocument,
} from "@/lib/api/client";
import { formatTimestamp } from "@/lib/formatters";
import { getStrategyLabel, getTaskStateLabel } from "@/lib/i18n/zh-cn";
import { formatKbId } from "@/lib/kb-id";
import type { ChunkDraftRecord, IngestionTask, TaskState } from "@/lib/contracts/types";

const OFFLINE_STAGES = ["upload", "parse", "clean", "chunk", "quality", "embedding", "export"] as const;
const STAGE_LABELS: Record<string, string> = {
  upload: "上传",
  parse: "解析",
  clean: "清洗",
  chunk: "切片",
  quality: "质检",
  embedding: "向量化",
  export: "入库",
};
const STRATEGIES = [
  { value: "paragraph", label: "段落切片" },
  { value: "fixed_length", label: "固定长度" },
  { value: "semantic", label: "语义切片" },
  { value: "separator", label: "分隔符切片" },
  { value: "llm", label: "LLM 切片" },
  { value: "hierarchical", label: "三层切片" },
];
const SUBJECT_TYPES = [
  { value: "general", label: "通用（默认）" },
  { value: "medical", label: "医学教材" },
  { value: "stem", label: "理工教材" },
  { value: "humanities", label: "文史教材" },
];
const LAYOUT_TYPES = [
  { value: "single_column", label: "单栏（默认）" },
  { value: "double_column", label: "双栏排版" },
  { value: "mixed", label: "图文混排" },
];
type StepStatus = "pending" | "running" | "success" | "failed";

function stateToStep(s: TaskState): StepStatus {
  if (s === "success") return "success";
  if (s === "running" || s === "awaiting_confirmation") return "running";
  if (s === "failed" || s === "degraded") return "failed";
  return "pending";
}

type IngestionWorkspaceProps = {
  kbId: string;
  kbName?: string;
  defaultStrategy?: string;
};

export function IngestionWorkspace({ kbId, kbName, defaultStrategy = "hierarchical" }: IngestionWorkspaceProps) {
  const [tasks, setTasks] = useState<IngestionTask[]>([]);
  const [activeIdx, setActiveIdx] = useState(0);
  const [uploadOpen, setUploadOpen] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [selectedStrategy, setSelectedStrategy] = useState(defaultStrategy);
  const [selectedSubject, setSelectedSubject] = useState("general");
  const [selectedLayout, setSelectedLayout] = useState("single_column");
  const [uploadError, setUploadError] = useState<string | null>(null);
  const [taskDeleteError, setTaskDeleteError] = useState<string | null>(null);
  const [deletingTaskId, setDeletingTaskId] = useState<string | null>(null);
  const [drafts, setDrafts] = useState<Record<string, ChunkDraftRecord[]>>({});
  const [selectedDraftIds, setSelectedDraftIds] = useState<string[]>([]);
  const [editingDraftId, setEditingDraftId] = useState<string | null>(null);
  const [editingContent, setEditingContent] = useState("");
  const [confirming, setConfirming] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);
  const esRef = useRef<EventSource | null>(null);
  const retryRef = useRef(0);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  useEffect(() => {
    setSelectedStrategy(defaultStrategy);
  }, [defaultStrategy, kbId]);

  useEffect(() => {
    getIngestionTasks(kbId)
      .then((data) => {
        setTasks(data);
        setActiveIdx(0);
      })
      .catch(() => {
        setTasks([]);
      });
  }, [kbId]);

  useEffect(() => {
    const currentTask = tasks[activeIdx];
    if (!currentTask || currentTask.status !== "awaiting_confirmation") {
      setSelectedDraftIds([]);
      return;
    }

    getChunkDrafts(currentTask.id)
      .then((result) => {
        setDrafts((prev) => ({ ...prev, [currentTask.id]: result.items }));
      })
      .catch(() => {
        setDrafts((prev) => ({ ...prev, [currentTask.id]: [] }));
      });
  }, [activeIdx, tasks]);

  const startSSE = useCallback((taskId: string) => {
    const baseUrl = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://127.0.0.1:8001";

    function connect() {
      const es = new EventSource(`${baseUrl}/api/ingestion/stream/${taskId}`, { withCredentials: true });
      esRef.current = es;

      es.onmessage = (e) => {
        try {
          const data = JSON.parse(e.data);
          setTasks((prev) =>
            prev.map((t) => {
              if (t.id !== taskId) return t;
              const stages = t.stages.map((s) =>
                s.key === data.stage_key
                  ? {
                      ...s,
                      status: data.status as TaskState,
                      progress: typeof data.progress === "number" ? data.progress : s.progress,
                      reason: typeof data.message === "string" ? data.message : s.reason,
                    }
                  : s
              );
              return { ...t, status: (data.task_status as TaskState) ?? t.status, stages };
            })
          );
        } catch {
          // Ignore malformed SSE payloads.
        }
      };

      es.addEventListener("done", () => {
        es.close();
        retryRef.current = 0;
        getIngestionTask(taskId).then((updated) =>
          setTasks((prev) => prev.map((t) => (t.id === taskId ? updated : t)))
        );
      });

      es.onerror = () => {
        es.close();
        if (retryRef.current < 3) {
          retryRef.current += 1;
          setTimeout(connect, 2000);
        } else {
          pollRef.current = setInterval(async () => {
            const updated = await getIngestionTask(taskId);
            setTasks((prev) => prev.map((t) => (t.id === taskId ? updated : t)));
            if (updated.status === "success" || updated.status === "failed") {
              clearInterval(pollRef.current!);
            }
          }, 5000);
        }
      };
    }

    connect();
  }, []);

  useEffect(
    () => () => {
      esRef.current?.close();
      if (pollRef.current) {
        clearInterval(pollRef.current);
      }
    },
    []
  );

  async function handleUpload() {
    const file = fileRef.current?.files?.[0];
    if (!file) return;
    setUploading(true);
    setUploadError(null);
    try {
      const { task_id } = await uploadDocument(file, kbId, selectedStrategy, selectedSubject, selectedLayout);
      const newTask = await getIngestionTask(task_id);
      setTasks((prev) => [newTask, ...prev]);
      setActiveIdx(0);
      setUploadOpen(false);
      startSSE(task_id);
    } catch (err) {
      setUploadError(err instanceof Error ? err.message : "上传失败，请检查后端服务是否已启动。");
    } finally {
      setUploading(false);
    }
  }

  async function handleDeleteTask(taskId: string) {
    setDeletingTaskId(taskId);
    setTaskDeleteError(null);
    try {
      await deleteIngestionTask(taskId);
      setTasks((prev) => {
        const next = prev.filter((task) => task.id !== taskId);
        setActiveIdx((idx) => Math.min(idx, Math.max(next.length - 1, 0)));
        return next;
      });
      setDrafts((prev) => {
        const next = { ...prev };
        delete next[taskId];
        return next;
      });
      setSelectedDraftIds([]);
    } catch (err) {
      setTaskDeleteError(err instanceof Error ? err.message : "删除任务失败，请稍后重试。");
    } finally {
      setDeletingTaskId(null);
    }
  }

  async function refreshDrafts(taskId: string) {
    const result = await getChunkDrafts(taskId);
    setDrafts((prev) => ({ ...prev, [taskId]: result.items }));
  }

  async function handleSaveDraft(taskId: string) {
    if (!editingDraftId) return;
    const updated = await updateChunkDraft(editingDraftId, editingContent);
    setDrafts((prev) => ({
      ...prev,
      [taskId]: (prev[taskId] ?? []).map((draft) => (draft.id === updated.id ? updated : draft)),
    }));
    setEditingDraftId(null);
    setEditingContent("");
  }

  async function handleDeleteDraft(taskId: string, draftId: string) {
    await deleteChunkDraft(draftId);
    await refreshDrafts(taskId);
    setSelectedDraftIds((prev) => prev.filter((id) => id !== draftId));
  }

  async function handleDeleteSelectedDrafts(taskId: string) {
    if (selectedDraftIds.length === 0) return;
    await Promise.all(selectedDraftIds.map((draftId) => deleteChunkDraft(draftId)));
    await refreshDrafts(taskId);
    setSelectedDraftIds([]);
  }

  async function handleMergeDrafts(taskId: string) {
    if (selectedDraftIds.length < 2) return;
    await mergeChunkDrafts(taskId, selectedDraftIds);
    await refreshDrafts(taskId);
    setSelectedDraftIds([]);
  }

  async function handleConfirmDrafts(taskId: string) {
    setConfirming(true);
    try {
      const updated = await confirmChunkDrafts(taskId);
      setTasks((prev) => prev.map((task) => (task.id === taskId ? updated : task)));
      setDrafts((prev) => ({ ...prev, [taskId]: [] }));
      startSSE(taskId);
    } finally {
      setConfirming(false);
    }
  }

  const current = tasks[activeIdx];
  const tasksPagination = useClientPagination(tasks, 20);
  const currentDrafts = current ? drafts[current.id] ?? [] : [];
  const activeDrafts = currentDrafts.filter((draft) => !draft.isDeleted);
  const draftsPagination = useClientPagination(activeDrafts, 20);
  const steps = current
    ? OFFLINE_STAGES.map((key) => {
        const stage = current.stages.find((s) => s.key === key);
        return {
          key,
          label: STAGE_LABELS[key],
          status: stage ? stateToStep(stage.status) : ("pending" as StepStatus),
        };
      })
    : [];
  const activeStage = current
    ? current.stages.find((stage) => stage.status === "running") ??
      current.stages.find((stage) => stage.status === "failed") ??
      [...current.stages].reverse().find((stage) => stage.status === "success")
    : undefined;
  const activeStageProgress = Math.min(100, Math.max(0, activeStage?.progress ?? 0));

  return (
    <div className="space-y-6">
      <div className="relative overflow-hidden rounded-lg border border-[#FED7AA] bg-[linear-gradient(135deg,#FFF7ED_0%,#FFFFFF_50%,#FEF9C3_100%)] p-6 shadow-[0_18px_44px_rgba(255,138,0,0.12)]">
        <div className="relative flex flex-col gap-4 lg:flex-row lg:items-center lg:justify-between">
          <div>
            <div className="inline-flex items-center gap-2 rounded-md border border-[#FDBA74] bg-white/75 px-3 py-1 text-[12px] font-semibold text-[#C2410C] shadow-sm">
              <span className="h-2 w-2 rounded-full bg-[#FF8A00]" />
              单库入库生产线
            </div>
            <h1 className="mt-4 text-[30px] font-bold leading-tight text-ink-primary">文档入库</h1>
            <p className="mt-2 max-w-2xl text-sm leading-6 text-ink-secondary">
              {kbName ? `当前知识库：${kbName}` : "面向当前知识库的入库流水线监控。"}
            </p>
          </div>
          <Button variant="primary" onClick={() => setUploadOpen(true)}>
            <UploadIcon /> 上传文档
          </Button>
        </div>
      </div>

      <section className="grid gap-4 xl:grid-cols-[240px_minmax(0,1fr)_260px]">
        <div className="overflow-hidden rounded-lg border border-[#FF8A00]/24 bg-[linear-gradient(135deg,rgba(255,138,0,0.08),rgba(255,255,255,0.90))] shadow-panel">
          <div className="border-b border-[#FED7AA]/80 bg-[#FFF7ED] px-4 py-3">
            <p className="text-[11px] font-semibold uppercase tracking-[0.08em] text-[#C2410C]">任务队列</p>
          </div>
          {taskDeleteError && (
            <div className="border-b border-border-subtle bg-red-50 px-4 py-2 text-xs text-status-danger">
              {taskDeleteError}
            </div>
          )}
          {tasks.length === 0 ? (
            <p className="px-4 py-6 text-center text-sm text-ink-tertiary">暂无任务</p>
          ) : (
            <div className="divide-y divide-border-subtle">
              {tasksPagination.pageItems.map((task, i) => {
                const taskIndex = tasksPagination.startIndex + i;
                const deleteDisabled = task.status === "pending" || task.status === "running";
                return (
                  <article
                    key={task.id}
                    onClick={() => setActiveIdx(taskIndex)}
                    className={[
                      "flex cursor-pointer items-start justify-between gap-2 px-4 py-3 transition-colors",
                      taskIndex === activeIdx
                        ? "border-l-[3px] border-[#FF8A00] bg-[#FFF7ED] pl-[13px]"
                        : "border-l-[3px] border-transparent hover:bg-[#FFF7ED]/70",
                    ].join(" ")}
                  >
                    <div className="min-w-0">
                      <p className="truncate text-[13px] font-medium text-ink-primary">{task.documentName}</p>
                      <p className="mt-0.5 text-[11px] text-ink-tertiary">{getStrategyLabel(task.strategy)}</p>
                    </div>
                    <div className="flex shrink-0 items-center gap-1.5">
                      <StatusBadge status={task.status} />
                      <Button
                        variant="danger-ghost"
                        size="sm"
                        iconOnly
                        disabled={deleteDisabled}
                        loading={deletingTaskId === task.id}
                        onClick={(e) => {
                          e.stopPropagation();
                          void handleDeleteTask(task.id);
                        }}
                        title={deleteDisabled ? "任务仍在运行，结束后可删除" : "删除任务并释放文件"}
                        aria-label={`删除 ${task.documentName}`}
                      >
                        <TrashIcon />
                      </Button>
                    </div>
                  </article>
                );
              })}
            </div>
          )}
          {tasks.length > 0 && (
            <TablePagination
              page={tasksPagination.page}
              pageSize={tasksPagination.pageSize}
              total={tasksPagination.total}
              pageCount={tasksPagination.pageCount}
              startIndex={tasksPagination.startIndex}
              endIndex={tasksPagination.endIndex}
              onPageChange={tasksPagination.setPage}
              onPageSizeChange={tasksPagination.setPageSize}
              variant="compact"
            />
          )}
        </div>

        <div className="space-y-4">
          {current ? (
            <>
              <div className="rounded-lg border border-[#FF8A00]/24 bg-[radial-gradient(circle_at_88%_16%,rgba(255,138,0,0.14),transparent_34%),linear-gradient(135deg,#FFF7ED,#FFFFFF_62%)] p-5 shadow-panel">
                <div className="flex items-center justify-between gap-4">
                  <div>
                    <p className="text-[11px] font-semibold uppercase tracking-[0.08em] text-ink-tertiary">当前任务</p>
                    <h2 className="mt-1 text-base font-semibold text-ink-primary">{current.documentName}</h2>
                    <p className="mt-0.5 text-xs text-ink-tertiary">
                      {formatKbId(current.kbId)} / {getStrategyLabel(current.strategy)}
                    </p>
                  </div>
                  <StatusBadge status={current.status}>{getTaskStateLabel(current.status)}</StatusBadge>
                </div>
                <div className="mt-6 overflow-x-auto pb-2">
                  <StepProgress steps={steps} />
                </div>
                {activeStage && (
                  <div className="mt-4 rounded-sm border border-border-subtle bg-subtle px-3 py-2">
                    <div className="flex items-center justify-between gap-3 text-xs">
                      <span className="font-medium text-ink-secondary">
                        {STAGE_LABELS[activeStage.key] ?? activeStage.label}
                      </span>
                      <span className="font-mono text-ink-tertiary">{activeStageProgress}%</span>
                    </div>
                    <ProgressBar value={activeStageProgress} className="mt-2" />
                    {activeStage.reason && (
                      <p className="mt-2 line-clamp-2 text-xs text-ink-tertiary">{activeStage.reason}</p>
                    )}
                  </div>
                )}
              </div>

              {current.blocks.length > 0 && (
                <div className="overflow-hidden rounded-lg border border-[#FF8A00]/24 bg-white/86 shadow-sm">
                  <div className="border-b border-[#FED7AA]/80 bg-[#FFF7ED] px-5 py-3">
                    <p className="text-[13px] font-semibold text-ink-primary">解析块预览</p>
                  </div>
                  <div className="divide-y divide-border-subtle">
                    {current.blocks.slice(0, 6).map((block) => (
                      <div key={block.id} className="px-5 py-3">
                        <div className="flex items-center gap-2">
                          <Badge variant="info">{block.type}</Badge>
                          <span className="font-mono text-xs text-ink-tertiary">P.{block.page}</span>
                        </div>
                        <p className="mt-1.5 line-clamp-2 text-sm text-ink-secondary">
                          {block.text || "[表格或图片载荷]"}
                        </p>
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {current.status === "awaiting_confirmation" && (
                <div className="overflow-hidden rounded-lg border border-[#FF8A00]/24 bg-white/86 shadow-sm">
                  <div className="flex items-center justify-between gap-3 border-b border-[#FED7AA]/80 bg-[#FFF7ED] px-5 py-3">
                    <div>
                      <p className="text-[13px] font-semibold text-ink-primary">待确认切片</p>
                      <p className="mt-1 text-xs text-ink-tertiary">
                        支持编辑、删除、相邻合并，确认后继续质检、向量化和写库。
                      </p>
                      <p className="mt-1 text-xs text-ink-tertiary">
                        已显示 {draftsPagination.startIndex + 1} - {draftsPagination.endIndex} / {activeDrafts.length} 条草稿
                      </p>
                    </div>
                    <div className="flex gap-2">
                      <Button
                        variant="danger-ghost"
                        size="sm"
                        disabled={selectedDraftIds.length === 0}
                        onClick={() => handleDeleteSelectedDrafts(current.id)}
                      >
                        删除已选
                      </Button>
                      <Button
                        variant="secondary"
                        size="sm"
                        disabled={selectedDraftIds.length < 2}
                        onClick={() => handleMergeDrafts(current.id)}
                      >
                        合并所选
                      </Button>
                      <Button
                        variant="primary"
                        size="sm"
                        loading={confirming}
                        onClick={() => handleConfirmDrafts(current.id)}
                      >
                        确认继续入库
                      </Button>
                    </div>
                  </div>
                  <div className="divide-y divide-border-subtle">
                    {activeDrafts.length === 0 ? (
                      <div className="px-5 py-6 text-sm text-ink-tertiary">当前没有可确认的切片草稿。</div>
                    ) : (
                      draftsPagination.pageItems.map((draft) => {
                        const selected = selectedDraftIds.includes(draft.id);
                        const editing = editingDraftId === draft.id;

                        return (
                          <div key={draft.id} className="px-5 py-4">
                            <div className="flex items-start gap-3">
                              <input
                                type="checkbox"
                                checked={selected}
                                onChange={(e) =>
                                  setSelectedDraftIds((prev) =>
                                    e.target.checked ? [...prev, draft.id] : prev.filter((id) => id !== draft.id)
                                  )
                                }
                                className="mt-1"
                              />
                              <div className="min-w-0 flex-1">
                                <div className="flex flex-wrap items-center gap-2">
                                  <Badge variant="neutral">#{draft.chunkIndex + 1}</Badge>
                                  <Badge variant="info">{draft.layer}</Badge>
                                  <span className="font-mono text-xs text-ink-tertiary">P.{draft.page}</span>
                                </div>

                                {editing ? (
                                  <div className="mt-3 space-y-2">
                                    <textarea
                                      value={editingContent}
                                      onChange={(e) => setEditingContent(e.target.value)}
                                      rows={6}
                                      className="w-full rounded-md border border-[#FDBA74] bg-white px-3 py-2 text-[13px] text-ink-primary shadow-sm focus:outline-none focus:ring-2 focus:ring-[#FF8A00]/25"
                                    />
                                    <div className="flex gap-2">
                                      <Button size="sm" variant="primary" onClick={() => handleSaveDraft(current.id)}>
                                        保存
                                      </Button>
                                      <Button
                                        size="sm"
                                        variant="secondary"
                                        onClick={() => {
                                          setEditingDraftId(null);
                                          setEditingContent("");
                                        }}
                                      >
                                        取消
                                      </Button>
                                    </div>
                                  </div>
                                ) : (
                                  <>
                                    <p className="mt-2 whitespace-pre-wrap text-sm text-ink-secondary">{draft.content}</p>
                                    <div className="mt-3 flex gap-2">
                                      <Button
                                        size="sm"
                                        variant="secondary"
                                        onClick={() => {
                                          setEditingDraftId(draft.id);
                                          setEditingContent(draft.content);
                                        }}
                                      >
                                        编辑
                                      </Button>
                                      <Button
                                        size="sm"
                                        variant="danger-ghost"
                                        onClick={() => handleDeleteDraft(current.id, draft.id)}
                                      >
                                        删除
                                      </Button>
                                    </div>
                                  </>
                                )}
                              </div>
                            </div>
                          </div>
                        );
                      })
                    )}
                  </div>
                  {activeDrafts.length > 0 && (
                    <TablePagination
                      page={draftsPagination.page}
                      pageSize={draftsPagination.pageSize}
                      total={draftsPagination.total}
                      pageCount={draftsPagination.pageCount}
                      startIndex={draftsPagination.startIndex}
                      endIndex={draftsPagination.endIndex}
                      onPageChange={draftsPagination.setPage}
                      onPageSizeChange={draftsPagination.setPageSize}
                    />
                  )}
                </div>
              )}

            </>
          ) : (
            <div className="flex h-48 items-center justify-center rounded-lg border border-dashed border-[#FDBA74] bg-[#FFF7ED]/60 text-sm text-[#9A3412]">
              上传文档后，在这里查看流水线进度。
            </div>
          )}
        </div>

        {current && (
          <div className="space-y-4">
            {[
              { label: "文档信息", value: current.documentName },
              { label: "目标知识库", value: formatKbId(current.kbId), mono: true },
              { label: "切片策略", value: getStrategyLabel(current.strategy) },
              { label: "更新时间", value: formatTimestamp(current.updatedAt), mono: true },
            ].map((item) => (
              <div key={item.label} className="rounded-lg border border-[#FF8A00]/24 bg-[radial-gradient(circle_at_92%_16%,rgba(255,138,0,0.12),transparent_34%),linear-gradient(135deg,#FFF7ED,#FFFFFF_62%)] p-4 shadow-sm">
                <p className="text-[11px] font-semibold uppercase tracking-[0.08em] text-ink-tertiary">{item.label}</p>
                <p className={`mt-2 text-sm text-ink-primary ${item.mono ? "font-mono" : ""}`}>{item.value}</p>
              </div>
            ))}
            <div className="rounded-lg border border-[#FF8A00]/24 bg-[radial-gradient(circle_at_92%_16%,rgba(255,138,0,0.12),transparent_34%),linear-gradient(135deg,#FFF7ED,#FFFFFF_62%)] p-4 shadow-sm">
              <p className="text-[11px] font-semibold uppercase tracking-[0.08em] text-ink-tertiary">阶段耗时</p>
              <div className="mt-3 space-y-2">
                {current.stages.map((stage) => (
                  <div key={stage.key} className="flex items-center justify-between text-sm">
                    <span className="text-ink-secondary">{STAGE_LABELS[stage.key] ?? stage.key}</span>
                    <span className="font-mono text-ink-primary">
                      {stage.latencyMs > 0 ? `${stage.latencyMs}ms` : "--"}
                    </span>
                  </div>
                ))}
              </div>
            </div>
            <ParseKeyMetricsPanel stage={current.stages.find((stage) => stage.key === "parse")} />
          </div>
        )}
      </section>

      <Modal
        open={uploadOpen}
        onClose={() => setUploadOpen(false)}
        title="上传文档"
        size="sm"
        footer={
          <>
            <Button variant="secondary" onClick={() => setUploadOpen(false)}>
              取消
            </Button>
            <Button variant="primary" loading={uploading} onClick={handleUpload}>
              开始入库
            </Button>
          </>
        }
      >
        <div className="space-y-4">
          {uploadError && (
            <div className="rounded-sm border border-red-200 bg-red-50 px-3 py-2 text-[13px] text-red-700">
              {uploadError}
            </div>
          )}
          <div>
            <label className="mb-1.5 block text-[13px] font-medium text-ink-primary">选择 PDF 文件</label>
            <Input
              ref={fileRef}
              type="file"
              accept=".pdf"
              className="w-full rounded-sm border border-border-subtle bg-panel px-3 py-2 text-[13px] text-ink-primary file:mr-3 file:rounded file:border-0 file:bg-active file:px-2 file:py-1 file:text-xs file:font-medium file:text-brand-primary"
            />
          </div>
          <div>
            <label className="mb-1.5 block text-[13px] font-medium text-ink-primary">切片策略</label>
            <select
              value={selectedStrategy}
              onChange={(e) => setSelectedStrategy(e.target.value)}
              className="h-[34px] w-full rounded-sm border border-border-subtle bg-panel px-3 text-[13px] text-ink-primary focus:outline-none focus:ring-2 focus:ring-border-focus"
            >
              {STRATEGIES.map((s) => (
                <option key={s.value} value={s.value}>
                  {s.label}
                </option>
              ))}
            </select>
          </div>
          <div>
            <label className="mb-1.5 block text-[13px] font-medium text-ink-primary">教材类型</label>
            <select
              value={selectedSubject}
              onChange={(e) => setSelectedSubject(e.target.value)}
              className="h-[34px] w-full rounded-sm border border-border-subtle bg-panel px-3 text-[13px] text-ink-primary focus:outline-none focus:ring-2 focus:ring-border-focus"
            >
              {SUBJECT_TYPES.map((s) => (
                <option key={s.value} value={s.value}>
                  {s.label}
                </option>
              ))}
            </select>
          </div>
          <div>
            <label className="mb-1.5 block text-[13px] font-medium text-ink-primary">排版类型</label>
            <select
              value={selectedLayout}
              onChange={(e) => setSelectedLayout(e.target.value)}
              className="h-[34px] w-full rounded-sm border border-border-subtle bg-panel px-3 text-[13px] text-ink-primary focus:outline-none focus:ring-2 focus:ring-border-focus"
            >
              {LAYOUT_TYPES.map((s) => (
                <option key={s.value} value={s.value}>
                  {s.label}
                </option>
              ))}
            </select>
          </div>
        </div>
      </Modal>
    </div>
  );
}

function UploadIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
      <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
      <polyline points="17 8 12 3 7 8" />
      <line x1="12" y1="3" x2="12" y2="15" />
    </svg>
  );
}

function TrashIcon() {
  return (
    <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
      <path d="M3 6h18" />
      <path d="M8 6V4h8v2" />
      <path d="M19 6l-1 14H6L5 6" />
      <path d="M10 11v6" />
      <path d="M14 11v6" />
    </svg>
  );
}
