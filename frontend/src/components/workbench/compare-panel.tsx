export function ComparePanel({
  title,
  leftTitle,
  rightTitle,
  leftContent,
  rightContent,
}: {
  title: string;
  leftTitle: string;
  rightTitle: string;
  leftContent: React.ReactNode;
  rightContent: React.ReactNode;
}) {
  return (
    <div className="relative overflow-hidden rounded-lg border border-[#DDD6FE] bg-[linear-gradient(135deg,#F5F3FF_0%,#FFFFFF_58%,#ECFDF5_100%)] p-5 shadow-panel">
      <div className="relative text-[11px] font-semibold uppercase tracking-[0.18em] text-[#6D28D9]">对比面板</div>
      <h3 className="mt-1 text-lg font-semibold">{title}</h3>
      <div className="mt-4 grid gap-4 xl:grid-cols-2">
        <article className="rounded-lg border border-[#DDD6FE] bg-[#F5F3FF]/45 p-4 shadow-sm">
          <div className="mb-3 text-xs font-semibold uppercase tracking-[0.16em] text-ink-tertiary">{leftTitle}</div>
          <div className="text-sm leading-6 text-ink-secondary">{leftContent}</div>
        </article>
        <article className="rounded-lg border border-[#A7F3D0] bg-[#ECFDF5]/45 p-4 shadow-sm">
          <div className="mb-3 text-xs font-semibold uppercase tracking-[0.16em] text-ink-tertiary">{rightTitle}</div>
          <div className="text-sm leading-6 text-ink-secondary">{rightContent}</div>
        </article>
      </div>
    </div>
  );
}
