function tryDecodeURIComponent(value: string): string | null {
  try {
    return decodeURIComponent(value);
  } catch {
    return null;
  }
}

export function decodeKbId(rawKbId: string): string {
  let current = rawKbId;

  for (let i = 0; i < 5; i += 1) {
    const decoded = tryDecodeURIComponent(current);
    if (!decoded || decoded === current) {
      break;
    }
    current = decoded;
  }

  return current;
}

export function formatKbId(rawKbId: string): string {
  return decodeKbId(rawKbId);
}

export function encodeKbIdForPath(kbId: string): string {
  return encodeURIComponent(decodeKbId(kbId));
}

export function buildKnowledgeBasePath(kbId: string, suffix = ""): string {
  return `/knowledge-bases/${encodeKbIdForPath(kbId)}${suffix}`;
}
