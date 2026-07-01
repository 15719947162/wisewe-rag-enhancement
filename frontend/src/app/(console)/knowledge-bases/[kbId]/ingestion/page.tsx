import { IngestionWorkspace } from "@/components/knowledge-base/ingestion-workspace";
import { decodeKbId } from "@/lib/kb-id";

export default async function KnowledgeBaseIngestionPage({
  params,
}: {
  params: Promise<{ kbId: string }>;
}) {
  const { kbId: routeKbId } = await params;
  return <IngestionWorkspace kbId={decodeKbId(routeKbId)} />;
}
