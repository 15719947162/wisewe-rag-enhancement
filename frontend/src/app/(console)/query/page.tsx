import { ContextRail } from "@/components/layout/context-rail";
import { QueryWorkspace } from "@/components/knowledge-base/query-workspace";

export default function QueryPage() {
  return (
    <div className="space-y-6">
      <ContextRail
        title="召回管理"
        description="用于跨知识库统一调试普通 RAG、Graph RAG、候选证据、答案和引用表现。若你正在处理某个具体知识库，建议返回单库工作台继续操作。"
      />
      <QueryWorkspace />
    </div>
  );
}
