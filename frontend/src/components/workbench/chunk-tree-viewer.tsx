import type { ChunkRecord } from "@/lib/contracts/types";
import { getChunkLayerLabel } from "@/lib/i18n/zh-cn";

export function ChunkTreeViewer({ chunks }: { chunks: ChunkRecord[] }) {
  return (
    <div className="relative overflow-hidden rounded-lg border border-[#A7F3D0] bg-[linear-gradient(135deg,#ECFDF5_0%,#FFFFFF_58%,#F5F3FF_100%)] p-5 shadow-panel">
      <div className="relative text-[11px] font-semibold uppercase tracking-[0.18em] text-[#047857]">切片层级</div>
      <h3 className="mt-1 text-lg font-semibold">父块 / 子块 / 增强块结构</h3>
      <div className="mt-4 space-y-3">
        {chunks.map((chunk) => (
          <article
            key={chunk.id}
            className={[
              "rounded-lg border border-[#D1FAE5] bg-white p-4 shadow-sm",
              chunk.layer === "child" && "ml-4",
              chunk.layer === "enhanced" && "ml-8 border-dashed",
            ]
              .filter(Boolean)
              .join(" ")}
          >
            <div className="flex items-center gap-3">
              <span className="rounded-md bg-[#ECFDF5] px-2.5 py-1 text-[11px] font-semibold uppercase tracking-[0.16em] text-[#047857]">
                {getChunkLayerLabel(chunk.layer)}
              </span>
              <span className="font-mono text-xs text-ink-tertiary">{chunk.id}</span>
            </div>
            <h4 className="mt-3 text-sm font-semibold text-ink-primary">{chunk.title ?? "未命名切片"}</h4>
            <p className="mt-2 text-sm leading-6 text-ink-secondary">{chunk.content}</p>
          </article>
        ))}
      </div>
    </div>
  );
}
