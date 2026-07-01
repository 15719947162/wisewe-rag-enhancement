export function MiniTrend({
  title,
  values,
}: {
  title: string;
  values: number[];
}) {
  const max = Math.max(...values, 1);

  return (
    <div className="relative overflow-hidden rounded-lg border border-[#BAE6FD] bg-[linear-gradient(135deg,#ECF8FF_0%,#FFFFFF_55%,#EEF3FF_100%)] p-5 shadow-panel">
      <div className="relative text-[11px] font-semibold uppercase tracking-[0.18em] text-[#0369A1]">趋势</div>
      <h3 className="mt-1 text-lg font-semibold">{title}</h3>
      <div className="mt-4 flex h-24 items-end gap-2">
        {values.map((value, index) => (
          <div key={`${title}-${index}`} className="flex-1 rounded-t-md bg-[#365DFF]/15">
            <div
              className="rounded-t-md bg-[linear-gradient(180deg,#06B6D4,#365DFF)]"
              style={{ height: `${Math.max((value / max) * 100, 8)}%` }}
            />
          </div>
        ))}
      </div>
    </div>
  );
}
