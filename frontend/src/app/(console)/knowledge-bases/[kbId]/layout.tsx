import { KnowledgeBaseTabs } from "@/components/layout/knowledge-base-tabs";
import { KnowledgeBasePresence } from "@/components/layout/knowledge-base-presence";
import { decodeKbId, formatKbId } from "@/lib/kb-id";

export default async function KnowledgeBaseWorkspaceLayout({
  children,
  params,
}: Readonly<{
  children: React.ReactNode;
  params: Promise<{ kbId: string }>;
}>) {
  const { kbId: routeKbId } = await params;
  const kbId = decodeKbId(routeKbId);

  return (
    <div className="space-y-6">
      <KnowledgeBasePresence kbId={kbId} />

      <div className="relative overflow-hidden rounded-lg border border-[#00A889]/20 bg-gradient-to-br from-white via-[#F7FFFC] to-[#EEF3FF] p-6 shadow-panel">
        <p className="text-[11px] font-semibold uppercase tracking-[0.12em] text-[#00A889]">Knowledge Base Workspace</p>
        <div className="mt-3 flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
          <div>
            <h1 className="text-[30px] font-bold leading-tight text-ink-primary">{formatKbId(kbId)}</h1>
            <p className="mt-1 text-sm text-ink-secondary">
              围绕当前知识库管理文档、入库、问答、评测和图谱能力。
            </p>
          </div>

          <div className="grid gap-3 sm:grid-cols-2">
            <div className="rounded-lg border border-[#00A889]/20 bg-white/82 px-4 py-3 text-left shadow-sm">
              <p className="text-[11px] font-semibold uppercase tracking-[0.12em] text-ink-tertiary">工作区模式</p>
              <p className="mt-2 text-sm font-medium text-ink-primary">单库闭环操作</p>
              <p className="mt-1 text-xs leading-5 text-ink-secondary">聚焦当前知识库的数据、检索、评测和治理状态。</p>
            </div>

            <div className="rounded-lg border border-[#365DFF]/20 bg-white/82 px-4 py-3 text-left shadow-sm">
              <p className="text-[11px] font-semibold uppercase tracking-[0.12em] text-ink-tertiary">知识库 ID</p>
              <p className="mt-2 font-mono text-xs text-ink-primary">{formatKbId(kbId)}</p>
            </div>
          </div>
        </div>
      </div>

      <KnowledgeBaseTabs kbId={kbId} />
      {children}
    </div>
  );
}
