import Link from "next/link";
import { getDocuments, getEvaluations, getKnowledgeBaseGraph } from "@/lib/api/client";
import { DocumentsPanel } from "@/components/knowledge-base/documents-panel";
import { formatNumber, formatScore, formatTimestamp } from "@/lib/formatters";
import { buildKnowledgeBasePath, decodeKbId } from "@/lib/kb-id";
import type { DocumentGraphPayload, DocumentRecord, EvaluationRecord } from "@/lib/contracts/types";

function buildStatusSummary(documents: DocumentRecord[]) {
  return {
    success: documents.filter((doc) => doc.status === "success").length,
    running: documents.filter((doc) => doc.status === "running").length,
    pending: documents.filter((doc) => doc.status === "pending").length,
    failed: documents.filter((doc) => doc.status === "failed" || doc.status === "degraded").length,
  };
}

function buildEvaluationSummary(evaluations: EvaluationRecord[]) {
  if (evaluations.length === 0) {
    return {
      total: 0,
      avgRelevance: null,
      avgFaithfulness: null,
      cannotAnswerRate: null,
    };
  }

  const total = evaluations.length;
  const avgRelevance = evaluations.reduce((sum, item) => sum + item.relevanceScore, 0) / total;
  const avgFaithfulness = evaluations.reduce((sum, item) => sum + item.faithfulnessScore, 0) / total;
  const cannotAnswerRate = evaluations.filter((item) => item.cannotAnswer).length / total;

  return { total, avgRelevance, avgFaithfulness, cannotAnswerRate };
}

function emptyKnowledgeBaseGraph(kbId: string): DocumentGraphPayload {
  return {
    documentId: `kb:${kbId}`,
    kbId,
    scope: "knowledge_base",
    nodes: [],
    edges: [],
    stats: {
      nodeCount: 0,
      edgeCount: 0,
      chunkCount: 0,
      entityCount: 0,
      tripleCount: 0,
      truncated: false,
      documentCount: 0,
      totalChunkCount: 0,
      selectedChunkCount: 0,
    },
  };
}

function settledValue<T>(result: PromiseSettledResult<T>, fallback: T): T {
  return result.status === "fulfilled" ? result.value : fallback;
}

export default async function KnowledgeBaseOverviewPage({
  params,
}: {
  params: Promise<{ kbId: string }>;
}) {
  const { kbId: routeKbId } = await params;
  const kbId = decodeKbId(routeKbId);
  const [documentsResult, evaluationsResult, graphResult] = await Promise.allSettled([
    getDocuments(kbId),
    getEvaluations(kbId),
    getKnowledgeBaseGraph(kbId),
  ]);
  const documents = settledValue(documentsResult, [] as DocumentRecord[]);
  const evaluations = settledValue(evaluationsResult, [] as EvaluationRecord[]);
  const graph = settledValue(graphResult, emptyKnowledgeBaseGraph(kbId));
  const recentDocuments = [...documents]
    .sort((a, b) => new Date(b.updatedAt).getTime() - new Date(a.updatedAt).getTime())
    .slice(0, 5);
  const statusSummary = buildStatusSummary(documents);
  const evaluationSummary = buildEvaluationSummary(evaluations);
  const latestDocument = recentDocuments[0];
  const averageChunks = documents.length > 0
    ? Math.round(documents.reduce((sum, doc) => sum + doc.chunkCount, 0) / documents.length)
    : 0;

  return (
    <div className="space-y-6">
      <section className="relative overflow-hidden rounded-lg border border-[#00A889]/20 bg-white p-6 shadow-panel">
        <div className="flex flex-col gap-6 xl:flex-row xl:items-start xl:justify-between">
          <div className="max-w-2xl">
            <p className="text-[11px] font-semibold uppercase tracking-[0.12em] text-[#00A889]">Knowledge Base Dashboard</p>
            <h2 className="mt-2 text-[28px] font-bold leading-tight text-ink-primary">单库总览</h2>
            <p className="mt-3 text-sm leading-6 text-ink-secondary">
              汇总当前知识库的文档、切片、图谱和评测情况，帮助你快速判断入库质量、检索覆盖和后续治理重点。
            </p>
          </div>

          <div className="grid gap-3 sm:grid-cols-2">
            <HighlightCard
              label="最近更新"
              value={latestDocument ? formatTimestamp(latestDocument.updatedAt) : "暂无"}
              hint="取当前知识库最近文档的更新时间。"
            />
            <HighlightCard
              label="当前知识库 ID"
              value={kbId}
              hint="随机 ID 作为稳定路由标识，不依赖知识库名称或拼音。"
            />
          </div>
        </div>

        <div className="mt-6 grid gap-4 md:grid-cols-2 xl:grid-cols-4">
          <MetricCard label="文档总数" value={String(documents.length)} hint="当前知识库内已入库的文档数量。" />
          <MetricCard label="切块总数" value={formatNumber(documents.reduce((sum, doc) => sum + doc.chunkCount, 0))} hint="用于检索和生成答案的切片数量。" />
          <MetricCard label="平均切片数" value={formatNumber(averageChunks)} hint="每份文档平均产生的切片数量。" />
          <MetricCard
            label="最近文档"
            value={latestDocument?.filename ?? "暂无"}
            hint={latestDocument ? `更新时间 ${formatTimestamp(latestDocument.updatedAt)}` : "上传 PDF 后会显示最近文档。"}
          />
        </div>
      </section>

      <section className="grid gap-6 xl:grid-cols-[1.4fr_1fr]">
        <div className="space-y-6">
          <div className="rounded-lg border border-[#00A889]/20 bg-white p-5 shadow-panel">
            <div>
              <h3 className="text-base font-semibold text-ink-primary">入库状态分布</h3>
              <p className="mt-1 text-sm text-ink-secondary">按文档当前状态统计，帮助定位运行中、待处理或异常任务。</p>
            </div>

            <div className="mt-5 grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
              <StatusTile label="成功" value={statusSummary.success} tone="success" />
              <StatusTile label="运行中" value={statusSummary.running} tone="running" />
              <StatusTile label="待处理" value={statusSummary.pending} tone="pending" />
              <StatusTile label="异常 / 降级" value={statusSummary.failed} tone="failed" />
            </div>
          </div>

          <div className="rounded-lg border border-[#7C3AED]/20 bg-white p-5 shadow-panel">
            <div className="mb-4 flex flex-col gap-2 sm:flex-row sm:items-start sm:justify-between">
              <div>
                <h3 className="text-base font-semibold text-ink-primary">知识图谱摘要</h3>
                <p className="mt-1 text-sm text-ink-secondary">展示当前知识库的节点、关系、实体和三元组规模。</p>
              </div>
              <div className="text-xs leading-5 text-ink-tertiary sm:text-right">
                <p>文档 {formatNumber(graph.stats.documentCount ?? documents.length)}</p>
                <p>
                  预览切片 {formatNumber(graph.stats.selectedChunkCount ?? graph.stats.chunkCount)}
                  {graph.stats.totalChunkCount ? ` / ${formatNumber(graph.stats.totalChunkCount)}` : ""}
                </p>
              </div>
            </div>
            <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
              <GraphMetric label="图谱节点" value={formatNumber(graph.stats.nodeCount)} />
              <GraphMetric label="关系边" value={formatNumber(graph.stats.edgeCount)} />
              <GraphMetric label="实体" value={formatNumber(graph.stats.entityCount)} />
              <GraphMetric label="三元组" value={formatNumber(graph.stats.tripleCount)} />
            </div>
            <div className="mt-4 flex flex-col gap-3 rounded-md border border-[#7C3AED]/20 bg-[#F4F0FF]/55 px-4 py-4 sm:flex-row sm:items-center sm:justify-between">
              <div>
                <p className="text-sm font-semibold text-ink-primary">图谱工作台</p>
                <p className="mt-1 text-xs leading-5 text-ink-secondary">进入独立图谱页查看节点、关系、密度和图例控制。</p>
              </div>
              <Link
                href={buildKnowledgeBasePath(kbId, "/graph")}
                className="inline-flex items-center justify-center rounded-sm bg-brand-primary px-3 py-2 text-sm font-medium text-white transition-colors hover:bg-brand-primary-hover"
              >
                打开知识图谱
              </Link>
            </div>
          </div>

          <div className="rounded-lg border border-[#7C3AED]/20 bg-white p-5 shadow-panel">
            <div className="flex items-center justify-between">
              <div>
                <h3 className="text-base font-semibold text-ink-primary">评测摘要</h3>
                <p className="mt-1 text-sm text-ink-secondary">基于当前知识库的问答评测记录聚合相关性、忠实度和资料不足比例。</p>
              </div>
              <Link href={buildKnowledgeBasePath(kbId, "/evaluation")} className="text-sm font-medium text-brand-primary hover:text-brand-primary-hover">
                进入评测记录
              </Link>
            </div>

            <div className="mt-5 grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
              <MetricCard label="评测样本" value={formatNumber(evaluationSummary.total)} hint="当前知识库累计评测记录数。" />
              <MetricCard label="平均相关性" value={formatScore(evaluationSummary.avgRelevance)} hint="召回证据与问题的匹配程度。" />
              <MetricCard label="平均忠实度" value={formatScore(evaluationSummary.avgFaithfulness)} hint="答案是否忠实于引用证据。" />
              <MetricCard
                label="资料不足率"
                value={evaluationSummary.cannotAnswerRate === null ? "暂无" : `${(evaluationSummary.cannotAnswerRate * 100).toFixed(1)}%`}
                hint="系统判断资料不足、未回答的比例。"
              />
            </div>
          </div>

          <RecentDocuments documents={recentDocuments} />
        </div>

        <div className="space-y-6">
          <QuickLinks kbId={kbId} />
          <BoundaryCard />
        </div>
      </section>

      <DocumentsPanel kbId={kbId} title="文档列表" />
    </div>
  );
}

function RecentDocuments({ documents }: { documents: DocumentRecord[] }) {
  return (
    <div className="rounded-lg border border-[#FF8A00]/20 bg-white p-5 shadow-panel">
      <div>
        <h3 className="text-base font-semibold text-ink-primary">最近文档</h3>
        <p className="mt-1 text-sm text-ink-secondary">最近 5 份更新的文档。</p>
      </div>

      {documents.length === 0 ? (
        <div className="mt-5 rounded-lg border border-dashed border-[#A7F3D0] bg-[#ECFDF5]/50 px-4 py-8 text-center text-sm text-[#047857]">
          暂无文档，上传 PDF 后会显示在这里。
        </div>
      ) : (
        <div className="mt-5 space-y-3">
          {documents.map((doc) => (
            <div key={doc.id} className="rounded-lg border border-[#00A889]/15 bg-[#E9FBF5]/35 px-4 py-4">
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0">
                  <p className="truncate text-sm font-semibold text-ink-primary">{doc.filename}</p>
                  <p className="mt-1 text-xs text-ink-secondary">
                    {formatNumber(doc.chunkCount)} 个切片 · 更新于 {formatTimestamp(doc.updatedAt)}
                  </p>
                </div>
                <StatusPill status={doc.status} />
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function QuickLinks({ kbId }: { kbId: string }) {
  const links = [
    { label: "管理文档", href: buildKnowledgeBasePath(kbId, "/documents"), hint: "查看文档、切片、详情和导出入口。" },
    { label: "继续入库", href: buildKnowledgeBasePath(kbId, "/ingestion"), hint: "上传 PDF 并跟踪解析、切片和向量化进度。" },
    { label: "调试问答", href: buildKnowledgeBasePath(kbId, "/query"), hint: "测试召回候选、引用证据和答案评分。" },
    { label: "查看评测", href: buildKnowledgeBasePath(kbId, "/evaluation"), hint: "查看相关性、忠实度和 LLM 评分诊断。" },
  ];

  return (
    <div className="rounded-lg border border-[#00A889]/20 bg-white p-5 shadow-panel">
      <h3 className="text-base font-semibold text-ink-primary">快捷入口</h3>
      <p className="mt-1 text-sm text-ink-secondary">围绕当前知识库继续完成常用操作。</p>

      <div className="mt-5 grid gap-3">
        {links.map((item) => (
          <Link key={item.href} href={item.href} className="rounded-lg border border-[#00A889]/15 bg-[#E9FBF5]/35 px-4 py-4 transition-colors hover:border-[#00A889]/40 hover:bg-[#E9FBF5]">
            <div className="flex items-center justify-between gap-3">
              <div>
                <p className="text-sm font-semibold text-ink-primary">{item.label}</p>
                <p className="mt-1 text-xs leading-5 text-ink-secondary">{item.hint}</p>
              </div>
              <ArrowRightIcon />
            </div>
          </Link>
        ))}
      </div>
    </div>
  );
}

function BoundaryCard() {
  return (
    <div className="relative overflow-hidden rounded-lg border border-[#BAE6FD] bg-[linear-gradient(135deg,#ECF8FF,#FFFFFF)] p-5 shadow-panel">
      <p className="text-[11px] font-semibold uppercase tracking-[0.1em] text-[#0369A1]">Governance Boundary</p>
      <h3 className="mt-1 text-base font-semibold text-ink-primary">当前边界说明</h3>
      <div className="mt-4 space-y-3 text-sm leading-6 text-ink-secondary">
        <p>本页展示当前知识库内已落库的数据和评测聚合，不展示完整 prompt、完整答案或文档原文。</p>
        <p>权限范围由当前身份决定；超级管理员可查看全局数据，普通用户按租户和授权范围过滤。</p>
        <p>图表类统计属于后续增强，本期以明细表和指标卡为主。</p>
      </div>
    </div>
  );
}

function MetricCard({ label, value, hint }: { label: string; value: string; hint: string }) {
  return (
    <div className="rounded-lg border border-[#00A889]/15 bg-[#E9FBF5]/35 px-5 py-4">
      <p className="text-[11px] font-semibold uppercase tracking-[0.12em] text-ink-tertiary">{label}</p>
      <p className="mt-2 text-lg font-semibold text-ink-primary">{value}</p>
      <p className="mt-2 text-xs leading-5 text-ink-secondary">{hint}</p>
    </div>
  );
}

function HighlightCard({ label, value, hint }: { label: string; value: string; hint: string }) {
  return (
    <div className="rounded-lg border border-[#365DFF]/15 bg-[#EEF3FF]/55 px-4 py-4">
      <p className="text-[11px] font-semibold uppercase tracking-[0.12em] text-ink-tertiary">{label}</p>
      <p className="mt-2 text-sm font-semibold text-ink-primary">{value}</p>
      <p className="mt-2 text-xs leading-5 text-ink-secondary">{hint}</p>
    </div>
  );
}

function GraphMetric({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-lg border border-[#C4B5FD] bg-[#F5F3FF]/65 px-4 py-3">
      <p className="text-[11px] font-semibold uppercase tracking-[0.12em] text-[#6D28D9]">{label}</p>
      <p className="mt-2 font-mono text-lg font-semibold text-ink-primary">{value}</p>
    </div>
  );
}

function StatusTile({ label, value, tone }: { label: string; value: number; tone: "success" | "running" | "pending" | "failed" }) {
  const toneMap = {
    success: "border-status-success/25 bg-status-success/10 text-status-success",
    running: "border-status-info/25 bg-status-info/10 text-status-info",
    pending: "border-status-warning/25 bg-status-warning/10 text-status-warning",
    failed: "border-status-danger/25 bg-status-danger/10 text-status-danger",
  } as const;

  return (
    <div className={`rounded-lg border px-4 py-4 shadow-sm ${toneMap[tone]}`}>
      <p className="text-[11px] font-semibold uppercase tracking-[0.12em]">{label}</p>
      <p className="mt-2 text-2xl font-bold">{value}</p>
    </div>
  );
}

function StatusPill({ status }: { status: string }) {
  const map = {
    success: "bg-status-success/10 text-status-success",
    running: "bg-status-info/10 text-status-info",
    pending: "bg-status-warning/10 text-status-warning",
    failed: "bg-status-danger/10 text-status-danger",
    degraded: "bg-status-danger/10 text-status-danger",
  } as const;

  return (
    <span className={`rounded-full px-2.5 py-1 text-xs font-medium ${map[status as keyof typeof map] ?? "bg-subtle text-ink-tertiary"}`}>
      {status}
    </span>
  );
}

function ArrowRightIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
      <path d="m9 18 6-6-6-6" />
    </svg>
  );
}
