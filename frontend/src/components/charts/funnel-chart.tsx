export function FunnelChart({
  title,
  steps,
}: {
  title: string;
  steps: Array<{ label: string; value: number; colorClass: string }>;
}) {
  const max = Math.max(...steps.map((step) => step.value), 1);

  return (
    <div className="relative overflow-hidden rounded-lg border border-[#FED7AA] bg-[linear-gradient(135deg,#FFF7ED_0%,#FFFFFF_58%,#FEF9C3_100%)] p-5 shadow-panel">
      <div className="relative text-[11px] font-semibold uppercase tracking-[0.18em] text-[#C2410C]">婕忔枟鍥</div>
      <h3 className="mt-1 text-lg font-semibold">{title}</h3>
      <div className="mt-5 space-y-3">
        {steps.map((step) => (
          <div key={step.label}>
            <div className="mb-1 flex items-center justify-between text-xs uppercase tracking-[0.14em] text-ink-tertiary">
              <span>{step.label}</span>
              <span className="font-mono text-ink-primary">{step.value}</span>
            </div>
            <div className="h-3 overflow-hidden rounded-md bg-white/80 shadow-inner">
              <div
                className={`h-full rounded-full ${step.colorClass}`}
                style={{ width: `${Math.max((step.value / max) * 100, 8)}%` }}
              />
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
