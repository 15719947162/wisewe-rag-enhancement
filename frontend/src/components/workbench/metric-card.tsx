import type { OverviewMetric } from "@/lib/contracts/types";

export function MetricCard({ metric }: { metric: OverviewMetric }) {
  const toneMap = {
    good: {
      border: "border-[#00A889]/26",
      bg: "bg-[radial-gradient(circle_at_88%_18%,rgba(0,168,137,0.18),transparent_36%),linear-gradient(135deg,#E9FBF5,#FFFFFF_58%)]",
      label: "text-[#007F69]",
      glow: "after:bg-[#00A889]/18",
      delta: "bg-[#E9FBF5] text-[#007F69]",
      shadow: "shadow-[0_14px_34px_rgba(0,168,137,0.10)]",
    },
    warning: {
      border: "border-[#FF8A00]/28",
      bg: "bg-[radial-gradient(circle_at_88%_18%,rgba(255,138,0,0.20),transparent_36%),linear-gradient(135deg,#FFF5DD,#FFFFFF_58%)]",
      label: "text-[#B85F00]",
      glow: "after:bg-[#FF8A00]/18",
      delta: "bg-[#FFF5DD] text-[#B85F00]",
      shadow: "shadow-[0_14px_34px_rgba(255,138,0,0.10)]",
    },
    neutral: {
      border: "border-[#365DFF]/24",
      bg: "bg-[radial-gradient(circle_at_88%_18%,rgba(54,93,255,0.16),transparent_36%),linear-gradient(135deg,#EEF3FF,#FFFFFF_58%)]",
      label: "text-[#2447DB]",
      glow: "after:bg-[#365DFF]/14",
      delta: "bg-[#EEF3FF] text-[#2447DB]",
      shadow: "shadow-[0_14px_34px_rgba(54,93,255,0.10)]",
    },
  } as const;
  const style = toneMap[metric.tone] ?? toneMap.neutral;

  return (
    <article
      className={[
        "relative min-h-[128px] overflow-hidden rounded-lg border p-5 after:pointer-events-none after:absolute after:-bottom-10 after:-right-8 after:h-24 after:w-24 after:rounded-full after:content-['']",
        style.border,
        style.bg,
        style.glow,
        style.shadow,
      ].join(" ")}
    >
      <div className={["relative text-[11px] font-extrabold uppercase tracking-[0.11em]", style.label].join(" ")}>{metric.label}</div>
      <div className="mt-4 flex items-end justify-between gap-3">
        <div>
          <div className="font-mono text-4xl font-semibold text-ink-primary">
            {metric.value}
          </div>
          <div className="mt-1 text-sm text-ink-secondary">{metric.helper}</div>
        </div>
        <div
          className={[
            "rounded-full px-2.5 py-1 text-[11px] font-semibold uppercase tracking-[0.12em]",
            style.delta,
          ].join(" ")}
        >
          {metric.delta}
        </div>
      </div>
    </article>
  );
}
