import type { Citation } from "@/lib/contracts/types";

export function CitationCard({ citation }: { citation: Citation }) {
  return (
    <article className="relative overflow-hidden rounded-lg border border-[#7C3AED]/24 bg-[radial-gradient(circle_at_88%_18%,rgba(124,58,237,0.16),transparent_36%),linear-gradient(135deg,#F4F0FF,#FFFFFF_58%)] p-4 shadow-[0_12px_28px_rgba(124,58,237,0.10)] transition-colors hover:border-[#7C3AED]/45">
      <span className="pointer-events-none absolute -bottom-8 -right-8 h-20 w-20 rounded-full bg-[#7C3AED]/14" />
      <div className="flex items-center justify-between gap-3">
        <span className="rounded-md bg-[linear-gradient(90deg,#7C3AED,#EC4899)] px-2.5 py-1 font-mono text-[11px] font-semibold uppercase tracking-[0.18em] text-white">
          [{citation.index}]
        </span>
        <span className="font-mono text-xs text-ink-tertiary">第 {citation.page} ҳ</span>
      </div>
      <div className="mt-3 text-sm font-semibold text-ink-primary">{citation.source}</div>
      <p className="mt-2 text-sm leading-6 text-ink-secondary">{citation.snippet}</p>
      <div className="mt-3 font-mono text-xs text-ink-tertiary">切块 {citation.chunkId}</div>
    </article>
  );
}
