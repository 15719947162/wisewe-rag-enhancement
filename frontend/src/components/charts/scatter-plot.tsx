type ScatterPoint = {
  x: number;
  y: number;
  label: string;
};

export function ScatterPlot({
  title,
  points,
}: {
  title: string;
  points: ScatterPoint[];
}) {
  return (
    <div className="relative overflow-hidden rounded-lg border border-[#DDD6FE] bg-[linear-gradient(135deg,#F5F3FF_0%,#FFFFFF_58%,#ECFDF5_100%)] p-5 shadow-panel">
      <div className="relative text-[11px] font-semibold uppercase tracking-[0.18em] text-[#6D28D9]">鏁ｇ偣鍥</div>
      <h3 className="mt-1 text-lg font-semibold">{title}</h3>
      <div className="relative mt-5 h-72 rounded-lg border border-[#DDD6FE] bg-[radial-gradient(circle_at_top_left,#F5F3FF,#FFFFFF_52%,#ECFDF5_100%)] shadow-inner">
        {points.map((point) => (
          <div
            key={point.label}
            className="absolute flex h-3 w-3 -translate-x-1/2 -translate-y-1/2 items-center justify-center rounded-full bg-brand-accent ring-4 ring-brand-accent/15"
            style={{ left: `${point.x * 100}%`, bottom: `${point.y * 100}%` }}
            aria-label={point.label}
            title={point.label}
          />
        ))}
        <div className="absolute inset-x-4 bottom-4 flex justify-between text-xs text-ink-tertiary">
          <span>鐩稿叧鎬</span>
          <span>蹇犲疄搴</span>
        </div>
      </div>
    </div>
  );
}
