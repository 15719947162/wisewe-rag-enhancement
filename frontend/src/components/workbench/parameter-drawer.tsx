export function ParameterDrawer({
  title,
  children,
}: {
  title: string;
  children: React.ReactNode;
}) {
  return (
    <aside className="relative overflow-hidden rounded-lg border border-[#DDD6FE] bg-[linear-gradient(135deg,#F5F3FF_0%,#FFFFFF_58%,#FFF0F4_100%)] p-5 shadow-panel">
      <div className="relative text-[11px] font-semibold uppercase tracking-[0.18em] text-[#6D28D9]">参数抽屉</div>
      <h3 className="mt-1 text-lg font-semibold">{title}</h3>
      <div className="mt-4 space-y-3">{children}</div>
    </aside>
  );
}
