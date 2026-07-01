import Link from "next/link";
import { getDocuments, getKnowledgeBaseGraph } from "@/lib/api/client";
import { DocumentGraphView } from "@/components/knowledge-base/document-graph-view";
import { formatNumber } from "@/lib/formatters";
import { buildKnowledgeBasePath, decodeKbId } from "@/lib/kb-id";
import type { DocumentGraphPayload, DocumentRecord } from "@/lib/contracts/types";

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

export default async function KnowledgeBaseGraphPage({
  params,
}: {
  params: Promise<{ kbId: string }>;
}) {
  const { kbId: routeKbId } = await params;
  const kbId = decodeKbId(routeKbId);
  const [documentsResult, graphResult] = await Promise.allSettled([getDocuments(kbId), getKnowledgeBaseGraph(kbId)]);
  const documents = settledValue(documentsResult, [] as DocumentRecord[]);
  const graph = settledValue(graphResult, emptyKnowledgeBaseGraph(kbId));

  return (
    <div className="space-y-6">
      <section className="relative overflow-hidden rounded-lg border border-[#00A889]/20 bg-gradient-to-br from-white to-[#E9FBF5] p-6 shadow-panel">
        <div className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
          <div className="max-w-3xl">
            <p className="text-[11px] font-semibold uppercase tracking-[0.12em] text-[#00A889]">Knowledge Graph</p>
            <h2 className="mt-2 text-[28px] font-bold leading-tight text-ink-primary">知识图谱</h2>
            <p className="mt-3 text-sm leading-6 text-ink-secondary">
              查看当前知识库的切片、实体、关系和三元组网络，用于理解文档结构、证据路径和图谱检索覆盖情况。
            </p>
          </div>
          <Link
            href={buildKnowledgeBasePath(kbId)}
            className="inline-flex items-center justify-center rounded-md border border-[#00A889]/20 bg-white px-3 py-2 text-sm font-medium text-ink-secondary shadow-sm transition-colors hover:border-[#00A889]/40 hover:bg-[#E9FBF5] hover:text-[#007F69]"
          >
            返回总览
          </Link>
        </div>

        <div className="mt-6 grid gap-3 sm:grid-cols-2 xl:grid-cols-6">
          <GraphMetric label="文档" value={formatNumber(graph.stats.documentCount ?? documents.length)} />
          <GraphMetric label="节点" value={formatNumber(graph.stats.nodeCount)} />
          <GraphMetric label="关系" value={formatNumber(graph.stats.edgeCount)} />
          <GraphMetric label="切片" value={formatNumber(graph.stats.chunkCount)} />
          <GraphMetric label="实体" value={formatNumber(graph.stats.entityCount)} />
          <GraphMetric label="三元组" value={formatNumber(graph.stats.tripleCount)} />
        </div>

        <div className="mt-4 rounded-md border border-[#00A889]/20 bg-white/82 px-4 py-3 text-xs leading-5 text-ink-secondary">
          当前知识库：{kbId}，预览切片 {formatNumber(graph.stats.selectedChunkCount ?? graph.stats.chunkCount)}
          {graph.stats.totalChunkCount ? ` / ${formatNumber(graph.stats.totalChunkCount)}` : ""}
        </div>
      </section>

      <section className="rounded-lg border border-[#00A889]/20 bg-white p-5 shadow-panel">
        <DocumentGraphView graph={graph} canvasClassName="h-[min(72vh,760px)] min-h-[560px]" />
      </section>
    </div>
  );
}

function GraphMetric({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-md border border-[#00A889]/20 bg-white/82 px-4 py-3 shadow-sm">
      <p className="text-[11px] font-semibold uppercase tracking-[0.12em] text-[#007F69]">{label}</p>
      <p className="mt-2 font-mono text-lg font-semibold text-ink-primary">{value}</p>
    </div>
  );
}
