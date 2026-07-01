type ProgressBarProps = {
  value: number;
  max?: number;
  colorClass?: string;
  className?: string;
};

export function ProgressBar({
  value,
  max = 100,
  colorClass = "bg-brand-primary",
  className = "",
}: ProgressBarProps) {
  const pct = Math.min(100, Math.max(0, (value / max) * 100));
  return (
    <div className={`h-2 w-full overflow-hidden rounded-full bg-[#E6ECF6] ${className}`}>
      <div
        className={`h-full rounded-full transition-[width] duration-[250ms] ease-out ${colorClass}`}
        style={{ width: `${pct}%` }}
      />
    </div>
  );
}

type StepStatus = "pending" | "running" | "success" | "failed";

type Step = {
  key: string;
  label: string;
  status: StepStatus;
};

const stepColors: Record<StepStatus, string> = {
  pending: "border-[#DDE3F0] bg-[#FAFBFF] text-ink-tertiary",
  running: "border-[#0EA5E9] bg-[#0EA5E9] text-ink-inverse shadow-[0_8px_18px_rgba(14,165,233,0.22)]",
  success: "border-[#10B981] bg-[#10B981] text-ink-inverse",
  failed: "border-[#E11D48] bg-[#E11D48] text-ink-inverse",
};

const lineColors: Record<StepStatus, string> = {
  pending: "bg-border-subtle",
  running: "bg-border-subtle",
  success: "bg-[#10B981]",
  failed: "bg-[#E11D48]",
};

export function StepProgress({ steps }: { steps: Step[] }) {
  return (
    <div className="flex items-center gap-0">
      {steps.map((step, i) => (
        <div key={step.key} className="flex items-center">
          <div className="flex flex-col items-center gap-1">
            <div
              className={[
                "flex h-8 w-8 items-center justify-center rounded-lg border-2 text-xs font-semibold",
                stepColors[step.status],
                step.status === "running" ? "animate-pulse" : "",
              ].join(" ")}
              title={step.label}
            >
              {step.status === "success" ? (
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5">
                  <polyline points="20 6 9 17 4 12" />
                </svg>
              ) : step.status === "failed" ? (
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5">
                  <line x1="18" y1="6" x2="6" y2="18" /><line x1="6" y1="6" x2="18" y2="18" />
                </svg>
              ) : (
                <span>{i + 1}</span>
              )}
            </div>
            <span className="max-w-[56px] text-center text-[10px] leading-tight text-ink-tertiary">
              {step.label}
            </span>
          </div>
          {i < steps.length - 1 && (
            <div className={`mx-1 h-0.5 w-6 shrink-0 ${lineColors[step.status]}`} />
          )}
        </div>
      ))}
    </div>
  );
}
