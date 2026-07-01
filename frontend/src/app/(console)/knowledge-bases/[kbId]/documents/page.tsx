import { DocumentsPanel } from "@/components/knowledge-base/documents-panel";
import { decodeKbId } from "@/lib/kb-id";

export default async function KnowledgeBaseDocumentsPage({
  params,
}: {
  params: Promise<{ kbId: string }>;
}) {
  const { kbId: routeKbId } = await params;
  return <DocumentsPanel kbId={decodeKbId(routeKbId)} />;
}
