import { EvaluationWorkspace } from "@/components/knowledge-base/evaluation-workspace";
import { decodeKbId } from "@/lib/kb-id";

export default async function KnowledgeBaseEvaluationPage({
  params,
}: {
  params: Promise<{ kbId: string }>;
}) {
  const { kbId: routeKbId } = await params;
  return <EvaluationWorkspace kbId={decodeKbId(routeKbId)} />;
}
