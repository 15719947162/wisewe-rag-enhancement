"use client";

const CURRENT_KB_KEY = "current_knowledge_base_id";

export function getCurrentKnowledgeBaseId(): string | null {
  if (typeof window === "undefined") {
    return null;
  }
  try {
    return localStorage.getItem(CURRENT_KB_KEY);
  } catch {
    return null;
  }
}

export function setCurrentKnowledgeBaseId(kbId: string): void {
  if (typeof window === "undefined") {
    return;
  }
  try {
    localStorage.setItem(CURRENT_KB_KEY, kbId);
  } catch {
    // Ignore storage failures and keep UI usable.
  }
}
