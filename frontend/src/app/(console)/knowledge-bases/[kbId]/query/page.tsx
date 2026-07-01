import { QueryWorkspace } from "@/components/knowledge-base/query-workspace";
import { decodeKbId } from "@/lib/kb-id";

export default async function KnowledgeBaseQueryPage({
  params,
}: {
  params: Promise<{ kbId: string }>;
}) {
  const { kbId: routeKbId } = await params;
  return <QueryWorkspace kbId={decodeKbId(routeKbId)} fixedKnowledgeBase />;
}
