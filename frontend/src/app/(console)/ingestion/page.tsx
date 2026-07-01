"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ContextRail } from "@/components/layout/context-rail";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Modal } from "@/components/ui/modal";
import { StatusBadge } from "@/components/ui/status-badge";
import { TablePagination } from "@/components/ui/table-pagination";
import {
  deleteIngestionTask,
  getConsoleIngestionTasks,
  getIngestionTask,
  getKnowledgeBases,
  getLatestIngestionLog,
  uploadDocument,
} from "@/lib/api/client";
import { formatLatency, formatTimestamp } from "@/lib/formatters";
import { getStrategyLabel, getTaskStateLabel } from "@/lib/i18n/zh-cn";
import { formatKbId } from "@/lib/kb-id";
import type {
  ConsoleIngestionTask,
  ConsoleIngestionTasksPayload,
  IngestionTask,
  KnowledgeBase,
  LatestIngestionLogPayload,
  TaskState,
} from "@/lib/contracts/types";

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

const RUNNING_STATUSES = new Set<TaskState>(["pending", "running", "awaiting_confirmation"]);
const EMPTY_TASKS: ConsoleIngestionTasksPayload = { items: [], total: 0, page: 1, pageSize: 20, pageCount: 1 };

function stageFriendlyText(task: IngestionTask | ConsoleIngestionTask | null): string {
  if (!task) return "正在准备入库任务...";
  const currentStage = "currentStage" in task ? task.currentStage : "";
  const stage =
    task.stages.find((item) => item.status === "running") ??
    task.stages.find((item) => item.key === currentStage);
  const key = stage?.key ?? currentStage;
  const map: Record<string, string> = {
    upload: "文件正在上传中...",
    parse: "文件正在解析中，请稍待...",
    clean: "正在清洗文档内容...",
    chunk: "正在生成知识切片，并进行 LLM 增强...",
    quality: "正在进行切片质量检查...",
    embedding: "正在生成向量索引...",
    export: "正在写入知识库...",
  };
  return map[String(key)] ?? "入库任务正在执行，请稍待...";
}

export default function IngestionPage() {
  const [tasksPayload, setTasksPayload] = useState<ConsoleIngestionTasksPayload>(EMPTY_TASKS);
  const [latestLog, setLatestLog] = useState<LatestIngestionLogPayload | null>(null);
  const [kbs, setKbs] = useState<KnowledgeBase[]>([]);
  const [keyword, setKeyword] = useState("");
  const [draftKeyword, setDraftKeyword] = useState("");
  const [statusFilter, setStatusFilter] = useState("");
  const [strategyFilter, setStrategyFilter] = useState("");
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(20);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [uploadOpen, setUploadOpen] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [selectedKb, setSelectedKb] = useState("default");
  const [selectedStrategy, setSelectedStrategy] = useState("hierarchical");
  const [selectedSubject, setSelectedSubject] = useState("general");
  const [selectedLayout, setSelectedLayout] = useState("single_column");
  const [uploadError, setUploadError] = useState<string | null>(null);
  const [runningTask, setRunningTask] = useState<IngestionTask | null>(null);
  const [timingTask, setTimingTask] = useState<ConsoleIngestionTask | null>(null);
  const [deletingTaskId, setDeletingTaskId] = useState<string | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  const esRef = useRef<EventSource | null>(null);

  const loadTasks = useCallback(
    async (options: { quiet?: boolean } = {}) => {
      if (options.quiet) setRefreshing(true);
      else setLoading(true);
      try {
        const [taskData, logData] = await Promise.all([
          getConsoleIngestionTasks({
            keyword,
            status: statusFilter || undefined,
            strategy: strategyFilter || undefined,
            page,
            pageSize,
          }),
          getLatestIngestionLog(undefined, 500),
        ]);
        setTasksPayload(taskData);
        setLatestLog(logData);
        setError(null);
      } catch (err) {
        setError(err instanceof Error ? err.message : "加载入库任务失败");
      } finally {
        setLoading(false);
        setRefreshing(false);
      }
    },
    [keyword, statusFilter, strategyFilter, page, pageSize],
  );

  useEffect(() => {
    getKnowledgeBases().then((data) => {
      setKbs(data);
      if (data[0]) setSelectedKb(data[0].id);
    });
  }, []);

  useEffect(() => {
    void loadTasks();
  }, [loadTasks]);

  useEffect(
    () => () => {
      esRef.current?.close();
    },
    [],
  );

  function watchTask(taskId: string) {
    esRef.current?.close();
    const baseUrl = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://127.0.0.1:8001";
    const es = new EventSource(`${baseUrl}/api/ingestion/stream/${taskId}`, { withCredentials: true });
    esRef.current = es;

    es.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data);
        setRunningTask((prev) => {
          if (!prev) return prev;
          const stages = prev.stages.map((stage) =>
            stage.key === data.stage_key
              ? {
                  ...stage,
                  status: data.status as TaskState,
                  progress: typeof data.progress === "number" ? data.progress : stage.progress,
                  reason: typeof data.message === "string" ? data.message : stage.reason,
                }
              : stage,
          );
          return { ...prev, status: (data.task_status as TaskState) ?? prev.status, stages };
        });
      } catch {
        // Ignore malformed SSE payloads.
      }
    };

    es.addEventListener("done", async () => {
      es.close();
      esRef.current = null;
      setRunningTask(null);
      await loadTasks({ quiet: true });
    });

    es.onerror = async () => {
      es.close();
      esRef.current = null;
      try {
        const updated = await getIngestionTask(taskId);
        if (RUNNING_STATUSES.has(updated.status)) {
          setRunningTask(updated);
          setTimeout(() => watchTask(taskId), 3000);
        } else {
          setRunningTask(null);
          await loadTasks({ quiet: true });
        }
      } catch {
        setRunningTask(null);
      }
    };
  }

  async function handleUpload() {
    const file = fileRef.current?.files?.[0];
    if (!file) return;
    setUploading(true);
    setUploadError(null);
    try {
      const { task_id } = await uploadDocument(file, selectedKb, selectedStrategy, selectedSubject, selectedLayout);
      const task = await getIngestionTask(task_id);
      setRunningTask(task);
      setUploadOpen(false);
      watchTask(task_id);
      await loadTasks({ quiet: true });
    } catch (err) {
      setUploadError(err instanceof Error ? err.message : "上传失败，请检查后端服务是否已启动。");
    } finally {
      setUploading(false);
    }
  }

  async function handleDelete(taskId: string) {
    setDeletingTaskId(taskId);
    try {
      await deleteIngestionTask(taskId);
      await loadTasks({ quiet: true });
    } catch (err) {
      setError(err instanceof Error ? err.message : "删除任务失败");
    } finally {
      setDeletingTaskId(null);
    }
  }

  const activeRunning = runningTask && RUNNING_STATUSES.has(runningTask.status) ? runningTask : null;
  const tableRows = tasksPayload.items;
  const pageStart = tasksPayload.total === 0 ? 0 : (tasksPayload.page - 1) * tasksPayload.pageSize;
  const pageEnd = Math.min(tasksPayload.total, pageStart + tableRows.length);
  const summary = useMemo(() => {
    const running = tableRows.filter((task) => RUNNING_STATUSES.has(task.status)).length;
    const failed = tableRows.filter((task) => task.status === "failed").length;
    const success = tableRows.filter((task) => task.status === "success").length;
    return { running, failed, success };
  }, [tableRows]);

  return (
    <div className="space-y-5">
      <ContextRail
        title="入库管理"
        description="集中查看文档入库任务、阶段耗时和最新入库日志。主界面展示业务友好的执行状态，原始日志仅保留在日志区域用于排障。"
      />

      <section className="relative overflow-hidden rounded-lg border border-[#FF8A00]/24 bg-[radial-gradient(circle_at_88%_12%,rgba(255,138,0,0.14),transparent_34%),linear-gradient(135deg,#FFF7ED,#FFFFFF_62%)] p-5 shadow-panel">
        {activeRunning && <RunningOverlay task={activeRunning} />}
        <div className="flex flex-col gap-4 lg:flex-row lg:items-center lg:justify-between">
          <div>
            <div className="inline-flex items-center gap-2 rounded-md border border-[#FDBA74] bg-white/78 px-3 py-1 text-[12px] font-semibold text-[#C2410C]">
              <span className="h-2 w-2 rounded-full bg-[#FF8A00]" />
              入库任务台账
            </div>
            <h1 className="mt-3 text-[28px] font-semibold leading-tight text-ink-primary">文档入库管理</h1>
            <p className="mt-2 max-w-3xl text-sm leading-6 text-ink-secondary">
              支持按知识库名称、知识库 ID、文档名称检索；阶段耗时可展开查看每个环节的执行时间和关键指标。
            </p>
          </div>
          <Button variant="primary" onClick={() => setUploadOpen(true)}>
            <UploadIcon /> 上传文档
          </Button>
        </div>
        <div className="mt-5 grid gap-3 md:grid-cols-3">
          <SummaryTile label="运行中" value={summary.running} />
          <SummaryTile label="已完成" value={summary.success} />
          <SummaryTile label="失败" value={summary.failed} />
        </div>
      </section>

      <section className="rounded-lg border border-[#FF8A00]/20 bg-white/90 shadow-sm">
        <div className="flex flex-col gap-3 border-b border-[#FED7AA]/80 bg-[#FFF7ED] px-5 py-4 lg:flex-row lg:items-end lg:justify-between">
          <div className="grid flex-1 gap-3 md:grid-cols-[minmax(260px,1fr)_160px_160px]">
            <label className="block">
              <span className="mb-1.5 block text-[12px] font-medium text-ink-secondary">快速检索</span>
              <input
                value={draftKeyword}
                onChange={(event) => setDraftKeyword(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === "Enter") {
                    setKeyword(draftKeyword);
                    setPage(1);
                  }
                }}
                placeholder="知识库名称 / ID / 文档名称"
                className="h-9 w-full rounded-md border border-border-subtle bg-white px-3 text-sm text-ink-primary outline-none transition-colors focus:border-[#FF8A00] focus:ring-2 focus:ring-[#FF8A00]/15"
              />
            </label>
            <SelectFilter label="状态" value={statusFilter} onChange={setStatusFilter} options={[
              { value: "", label: "全部状态" },
              { value: "pending", label: "待处理" },
              { value: "running", label: "运行中" },
              { value: "success", label: "成功" },
              { value: "failed", label: "失败" },
            ]} />
            <SelectFilter label="切片策略" value={strategyFilter} onChange={setStrategyFilter} options={[{ value: "", label: "全部策略" }, ...STRATEGIES]} />
          </div>
          <div className="flex gap-2">
            <Button
              variant="secondary"
              onClick={() => {
                setKeyword(draftKeyword);
                setPage(1);
              }}
            >
              查询
            </Button>
            <Button variant="secondary" loading={refreshing} onClick={() => loadTasks({ quiet: true })}>
              刷新
            </Button>
          </div>
        </div>

        {error && <div className="border-b border-red-200 bg-red-50 px-5 py-3 text-sm text-red-700">{error}</div>}

        <div className="overflow-x-auto">
          <table className="w-full min-w-[1180px] text-sm">
            <thead>
              <tr className="border-b border-[#FED7AA]/80 bg-[#FFF7ED] text-[11px] font-semibold uppercase tracking-[0.08em] text-[#C2410C]">
                <th className="px-4 py-3 text-left">状态</th>
                <th className="px-4 py-3 text-left">知识库</th>
                <th className="px-4 py-3 text-left">文档信息</th>
                <th className="px-4 py-3 text-left">切片策略</th>
                <th className="px-4 py-3 text-left">创建时间</th>
                <th className="px-4 py-3 text-left">更新时间</th>
                <th className="px-4 py-3 text-left">操作用户</th>
                <th className="px-4 py-3 text-right">阶段耗时</th>
                <th className="px-4 py-3 text-right">操作</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border-subtle">
              {loading ? (
                Array.from({ length: 6 }).map((_, index) => (
                  <tr key={index}>
                    <td colSpan={9} className="px-4 py-3">
                      <div className="h-8 animate-pulse rounded bg-[#FFF7ED]" />
                    </td>
                  </tr>
                ))
              ) : tableRows.length === 0 ? (
                <tr>
                  <td colSpan={9} className="px-4 py-10 text-center text-sm text-ink-tertiary">
                    暂无入库任务
                  </td>
                </tr>
              ) : (
                tableRows.map((task) => (
                  <tr key={task.id} className="transition-colors hover:bg-[#FFF7ED]/55">
                    <td className="px-4 py-3 align-top">
                      <StatusBadge status={task.status}>{getTaskStateLabel(task.status)}</StatusBadge>
                    </td>
                    <td className="px-4 py-3 align-top">
                      <p className="max-w-[180px] truncate font-medium text-ink-primary">{task.kbName}</p>
                      <p className="mt-1 font-mono text-[11px] text-ink-tertiary">{formatKbId(task.kbId)}</p>
                    </td>
                    <td className="px-4 py-3 align-top">
                      <p className="max-w-[260px] truncate font-medium text-ink-primary">{task.documentName}</p>
                      <p className="mt-1 text-[11px] text-ink-tertiary">切片 {task.chunkCount} 个 / {task.parseMethod}</p>
                    </td>
                    <td className="px-4 py-3 align-top text-ink-secondary">{getStrategyLabel(task.strategy)}</td>
                    <td className="px-4 py-3 align-top font-mono text-xs text-ink-secondary">{formatTimestamp(task.createdAt)}</td>
                    <td className="px-4 py-3 align-top font-mono text-xs text-ink-secondary">{formatTimestamp(task.updatedAt)}</td>
                    <td className="px-4 py-3 align-top text-ink-secondary">{task.actorName || "未记录"}</td>
                    <td className="px-4 py-3 text-right align-top">
                      <button
                        type="button"
                        onClick={() => setTimingTask(task)}
                        className="font-medium text-[#C2410C] underline-offset-4 hover:underline"
                      >
                        查看
                      </button>
                      <p className="mt-1 font-mono text-[11px] text-ink-tertiary">{formatLatency(task.totalLatencyMs)}</p>
                    </td>
                    <td className="px-4 py-3 text-right align-top">
                      <Button
                        variant="danger-ghost"
                        size="sm"
                        disabled={RUNNING_STATUSES.has(task.status)}
                        loading={deletingTaskId === task.id}
                        onClick={() => handleDelete(task.id)}
                      >
                        删除
                      </Button>
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>

        <TablePagination
          page={tasksPayload.page}
          pageSize={tasksPayload.pageSize}
          total={tasksPayload.total}
          pageCount={tasksPayload.pageCount}
          startIndex={pageStart}
          endIndex={pageEnd}
          onPageChange={setPage}
          onPageSizeChange={(size) => {
            setPageSize(size);
            setPage(1);
          }}
        />
      </section>

      <LatestLogPanel payload={latestLog} onRefresh={() => loadTasks({ quiet: true })} />

      <UploadModal
        open={uploadOpen}
        uploading={uploading}
        uploadError={uploadError}
        kbs={kbs}
        selectedKb={selectedKb}
        selectedStrategy={selectedStrategy}
        selectedSubject={selectedSubject}
        selectedLayout={selectedLayout}
        fileRef={fileRef}
        onClose={() => setUploadOpen(false)}
        onUpload={handleUpload}
        onKbChange={setSelectedKb}
        onStrategyChange={setSelectedStrategy}
        onSubjectChange={setSelectedSubject}
        onLayoutChange={setSelectedLayout}
      />

      {uploading && <UploadProgressOverlay />}

      <TimingModal task={timingTask} onClose={() => setTimingTask(null)} />
    </div>
  );
}

function UploadProgressOverlay() {
  return (
    <div className="fixed inset-0 z-[60] flex items-center justify-center bg-white/78 backdrop-blur-sm">
      <div className="w-full max-w-sm rounded-lg border border-[#FDBA74] bg-white p-5 text-center shadow-[0_18px_44px_rgba(255,138,0,0.18)]">
        <p className="text-base font-semibold text-ink-primary">文件正在上传中...</p>
        <p className="mt-2 text-sm text-ink-secondary">请稍待，上传完成后会自动进入解析和入库流程。</p>
        <div className="mt-4 h-2 overflow-hidden rounded-full bg-[#FED7AA]">
          <div className="h-full w-2/3 animate-pulse rounded-full bg-[#FF8A00]" />
        </div>
      </div>
    </div>
  );
}

function RunningOverlay({ task }: { task: IngestionTask }) {
  const stage = task.stages.find((item) => item.status === "running");
  const progress = Math.max(0, Math.min(100, stage?.progress ?? 0));
  return (
    <div className="absolute inset-0 z-10 flex items-center justify-center bg-white/78 backdrop-blur-sm">
      <div className="w-full max-w-md rounded-lg border border-[#FDBA74] bg-white p-5 shadow-[0_18px_44px_rgba(255,138,0,0.16)]">
        <p className="text-base font-semibold text-ink-primary">{stageFriendlyText(task)}</p>
        <p className="mt-2 truncate text-sm text-ink-secondary">{task.documentName}</p>
        <div className="mt-4 h-2 overflow-hidden rounded-full bg-[#FED7AA]">
          <div className="h-full rounded-full bg-[#FF8A00] transition-all" style={{ width: `${progress}%` }} />
        </div>
        <div className="mt-2 flex items-center justify-between text-xs text-ink-tertiary">
          <span>{stage?.label ?? "准备中"}</span>
          <span className="font-mono">{progress}%</span>
        </div>
      </div>
    </div>
  );
}

function SummaryTile({ label, value }: { label: string; value: number }) {
  return (
    <div className="rounded-md border border-[#FDBA74]/70 bg-white/76 px-4 py-3">
      <p className="text-[11px] font-semibold uppercase tracking-[0.08em] text-ink-tertiary">{label}</p>
      <p className="mt-2 font-mono text-xl font-semibold text-ink-primary">{value}</p>
    </div>
  );
}

function SelectFilter({
  label,
  value,
  options,
  onChange,
}: {
  label: string;
  value: string;
  options: Array<{ value: string; label: string }>;
  onChange: (value: string) => void;
}) {
  return (
    <label className="block">
      <span className="mb-1.5 block text-[12px] font-medium text-ink-secondary">{label}</span>
      <select
        value={value}
        onChange={(event) => onChange(event.target.value)}
        className="h-9 w-full rounded-md border border-border-subtle bg-white px-3 text-sm text-ink-primary outline-none transition-colors focus:border-[#FF8A00] focus:ring-2 focus:ring-[#FF8A00]/15"
      >
        {options.map((item) => (
          <option key={item.value} value={item.value}>
            {item.label}
          </option>
        ))}
      </select>
    </label>
  );
}

function LatestLogPanel({ payload, onRefresh }: { payload: LatestIngestionLogPayload | null; onRefresh: () => void }) {
  return (
    <section className="overflow-hidden rounded-lg border border-[#FF8A00]/20 bg-white/90 shadow-sm">
      <div className="flex items-center justify-between border-b border-[#FED7AA]/80 bg-[#FFF7ED] px-5 py-4">
        <div>
          <p className="text-[11px] font-semibold uppercase tracking-[0.08em] text-[#C2410C]">入库日志管理</p>
          <h2 className="mt-1 text-base font-semibold text-ink-primary">最新一次入库日志</h2>
        </div>
        <Button variant="secondary" size="sm" onClick={onRefresh}>
          刷新
        </Button>
      </div>
      {!payload?.task ? (
        <div className="px-5 py-8 text-sm text-ink-tertiary">暂无入库日志</div>
      ) : (
        <>
          <div className="grid gap-3 border-b border-border-subtle px-5 py-4 text-sm md:grid-cols-3">
            <div>
              <p className="text-[11px] text-ink-tertiary">任务</p>
              <p className="mt-1 truncate font-medium text-ink-primary">{payload.task.documentName}</p>
            </div>
            <div>
              <p className="text-[11px] text-ink-tertiary">知识库</p>
              <p className="mt-1 font-mono text-ink-primary">{formatKbId(payload.task.kbId)}</p>
            </div>
            <div>
              <p className="text-[11px] text-ink-tertiary">日志行数</p>
              <p className="mt-1 font-mono text-ink-primary">{payload.lineCount}{payload.truncated ? "（仅展示末尾）" : ""}</p>
            </div>
          </div>
          <div className="max-h-80 overflow-y-auto border-t border-[#FED7AA]/70 bg-[linear-gradient(180deg,#FFFBF4,#FFFFFF)] px-4 py-3">
            {payload.lines.length === 0 ? (
              <p className="text-sm text-ink-tertiary">日志文件为空或尚未生成。</p>
            ) : (
              payload.lines.map((line, index) => (
                <p key={index} className="whitespace-pre-wrap break-all font-mono text-[11px] leading-5 text-[#334155]">
                  {line}
                </p>
              ))
            )}
          </div>
        </>
      )}
    </section>
  );
}

function TimingModal({ task, onClose }: { task: ConsoleIngestionTask | null; onClose: () => void }) {
  return (
    <Modal open={Boolean(task)} onClose={onClose} title="阶段耗时统计" size="lg">
      {!task ? null : (
        <div className="space-y-4">
          <div className="rounded-md border border-border-subtle bg-subtle px-4 py-3">
            <p className="font-medium text-ink-primary">{task.documentName}</p>
            <p className="mt-1 font-mono text-xs text-ink-tertiary">{task.id}</p>
          </div>
          <div className="overflow-x-auto">
            <table className="w-full min-w-[720px] text-sm">
              <thead>
                <tr className="border-b border-border-subtle text-[11px] font-semibold uppercase tracking-[0.08em] text-ink-tertiary">
                  <th className="px-3 py-2 text-left">环节</th>
                  <th className="px-3 py-2 text-left">状态</th>
                  <th className="px-3 py-2 text-right">输入</th>
                  <th className="px-3 py-2 text-right">输出</th>
                  <th className="px-3 py-2 text-right">耗时</th>
                  <th className="px-3 py-2 text-left">说明</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-border-subtle">
                {task.stages.map((stage) => (
                  <tr key={stage.key}>
                    <td className="px-3 py-2 font-medium text-ink-primary">{stage.label}</td>
                    <td className="px-3 py-2"><StatusBadge status={stage.status}>{getTaskStateLabel(stage.status)}</StatusBadge></td>
                    <td className="px-3 py-2 text-right font-mono">{stage.inputCount}</td>
                    <td className="px-3 py-2 text-right font-mono">{stage.outputCount}</td>
                    <td className="px-3 py-2 text-right font-mono">{formatLatency(stage.latencyMs)}</td>
                    <td className="max-w-[260px] px-3 py-2 text-ink-secondary">{stage.reason || "-"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <details className="rounded-md border border-border-subtle bg-white">
            <summary className="cursor-pointer px-4 py-3 text-sm font-medium text-ink-primary">查看关键 metrics</summary>
            <pre className="max-h-64 overflow-auto border-t border-border-subtle bg-[#F8FAFC] p-4 text-[11px] leading-5 text-ink-secondary">
              {JSON.stringify(task.stages.reduce((acc, stage) => ({ ...acc, [stage.key]: stage.metrics ?? {} }), {}), null, 2)}
            </pre>
          </details>
        </div>
      )}
    </Modal>
  );
}

function UploadModal({
  open,
  uploading,
  uploadError,
  kbs,
  selectedKb,
  selectedStrategy,
  selectedSubject,
  selectedLayout,
  fileRef,
  onClose,
  onUpload,
  onKbChange,
  onStrategyChange,
  onSubjectChange,
  onLayoutChange,
}: {
  open: boolean;
  uploading: boolean;
  uploadError: string | null;
  kbs: KnowledgeBase[];
  selectedKb: string;
  selectedStrategy: string;
  selectedSubject: string;
  selectedLayout: string;
  fileRef: React.RefObject<HTMLInputElement | null>;
  onClose: () => void;
  onUpload: () => void;
  onKbChange: (value: string) => void;
  onStrategyChange: (value: string) => void;
  onSubjectChange: (value: string) => void;
  onLayoutChange: (value: string) => void;
}) {
  return (
    <Modal
      open={open}
      onClose={onClose}
      title="上传文档"
      size="sm"
      footer={
        <>
          <Button variant="secondary" onClick={onClose}>取消</Button>
          <Button variant="primary" loading={uploading} onClick={onUpload}>开始入库</Button>
        </>
      }
    >
      <div className="space-y-4">
        {uploadError && <div className="rounded-sm border border-red-200 bg-red-50 px-3 py-2 text-[13px] text-red-700">{uploadError}</div>}
        <div>
          <label className="mb-1.5 block text-[13px] font-medium text-ink-primary">选择 PDF 文件</label>
          <input
            ref={fileRef}
            type="file"
            accept=".pdf"
            className="w-full rounded-sm border border-border-subtle bg-panel px-3 py-2 text-[13px] text-ink-primary file:mr-3 file:rounded file:border-0 file:bg-active file:px-2 file:py-1 file:text-xs file:font-medium file:text-brand-primary"
          />
        </div>
        <ModalSelect label="目标知识库" value={selectedKb} options={kbs.map((kb) => ({ value: kb.id, label: kb.name }))} onChange={onKbChange} />
        <ModalSelect label="切片策略" value={selectedStrategy} options={STRATEGIES} onChange={onStrategyChange} />
        <ModalSelect label="教材类型" value={selectedSubject} options={SUBJECT_TYPES} onChange={onSubjectChange} />
        <ModalSelect label="排版类型" value={selectedLayout} options={LAYOUT_TYPES} onChange={onLayoutChange} />
      </div>
    </Modal>
  );
}

function ModalSelect({ label, value, options, onChange }: { label: string; value: string; options: Array<{ value: string; label: string }>; onChange: (value: string) => void }) {
  return (
    <div>
      <label className="mb-1.5 block text-[13px] font-medium text-ink-primary">{label}</label>
      <select
        value={value}
        onChange={(event) => onChange(event.target.value)}
        className="h-[34px] w-full rounded-sm border border-border-subtle bg-panel px-3 text-[13px] text-ink-primary focus:outline-none focus:ring-2 focus:ring-border-focus"
      >
        {options.map((item) => (
          <option key={item.value} value={item.value}>{item.label}</option>
        ))}
      </select>
    </div>
  );
}

function UploadIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
      <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
      <polyline points="17 8 12 3 7 8" />
      <line x1="12" y1="3" x2="12" y2="15" />
    </svg>
  );
}
