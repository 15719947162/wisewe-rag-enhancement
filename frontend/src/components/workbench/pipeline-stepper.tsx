import type { IngestionStage } from "@/lib/contracts/types";
import { formatLatency } from "@/lib/formatters";
import { StatusBadge } from "@/components/ui/status-badge";

const stageColor: Record<IngestionStage["key"], string> = {
  upload: "bg-pipeline-upload",
  parse: "bg-pipeline-parse",
  clean: "bg-pipeline-clean",
  chunk: "bg-pipeline-chunk",
  quality: "bg-pipeline-quality",
  embedding: "bg-pipeline-embedding",
  export: "bg-pipeline-export",
  retrieval: "bg-pipeline-retrieval",
  rerank: "bg-pipeline-rerank",
  generate: "bg-pipeline-generate",
  score: "bg-pipeline-score",
};

export function PipelineStepper({ stages }: { stages: IngestionStage[] }) {
  return (
    <div className="relative overflow-hidden rounded-lg border border-[#FF8A00]/28 bg-[radial-gradient(circle_at_88%_12%,rgba(255,138,0,0.18),transparent_34%),linear-gradient(135deg,#FFF5DD_0%,#FFFFFF_58%,#FEF9C3_100%)] p-5 shadow-panel after:pointer-events-none after:absolute after:-bottom-10 after:-right-8 after:h-24 after:w-24 after:rounded-full after:bg-[#FF8A00]/14 after:content-['']">
      <div className="relative text-[11px] font-semibold uppercase tracking-[0.18em] text-[#C2410C]">流程状态</div>
      <div className="mt-4 grid gap-3 lg:grid-cols-2 xl:grid-cols-3">
        {stages.map((stage) => (
          <article key={stage.key} className="relative z-10 rounded-lg border border-[#FED7AA] bg-white/78 p-4 shadow-sm transition-colors hover:border-[#FB923C] hover:bg-white">
            <div className="flex items-start justify-between gap-3">
              <div className="flex items-start gap-3">
                <span className={`mt-1 block h-3 w-3 rounded-full ${stageColor[stage.key]}`} />
                <div>
                  <h3 className="text-base font-semibold">{stage.label}</h3>
                  <p className="mt-1 text-sm leading-6 text-ink-secondary">{stage.reason}</p>
                </div>
              </div>
              <StatusBadge status={stage.status} />
            </div>

            <dl className="mt-4 grid grid-cols-3 gap-3 text-xs text-ink-secondary">
              <div>
                <dt className="uppercase tracking-[0.14em] text-ink-tertiary">输入</dt>
                <dd className="mt-1 font-mono text-sm text-ink-primary">{stage.inputCount}</dd>
              </div>
              <div>
                <dt className="uppercase tracking-[0.14em] text-ink-tertiary">输出</dt>
                <dd className="mt-1 font-mono text-sm text-ink-primary">{stage.outputCount}</dd>
              </div>
              <div>
                <dt className="uppercase tracking-[0.14em] text-ink-tertiary">耗时</dt>
                <dd className="mt-1 font-mono text-sm text-ink-primary">{formatLatency(stage.latencyMs)}</dd>
              </div>
            </dl>
          </article>
        ))}
      </div>
    </div>
  );
}
