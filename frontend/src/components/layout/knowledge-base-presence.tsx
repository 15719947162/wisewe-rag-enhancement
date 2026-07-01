"use client";

import { useEffect } from "react";
import { decodeKbId } from "@/lib/kb-id";
import { setCurrentKnowledgeBaseId } from "@/lib/knowledge-base-context";

export function KnowledgeBasePresence({ kbId }: { kbId: string }) {
  useEffect(() => {
    setCurrentKnowledgeBaseId(decodeKbId(kbId));
  }, [kbId]);

  return null;
}
