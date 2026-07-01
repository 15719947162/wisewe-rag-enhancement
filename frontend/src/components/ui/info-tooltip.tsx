type InfoTooltipProps = {
  content: string;
  label?: string;
  className?: string;
};

export function InfoTooltip({
  content,
  label = "查看说明",
  className = "",
}: InfoTooltipProps) {
  if (!content) return null;

  return (
    <span className={`group relative inline-flex ${className}`}>
      <button
        type="button"
        aria-label={label}
        className="inline-flex h-5 w-5 cursor-help items-center justify-center rounded-full border border-border-subtle bg-white/86 text-[12px] font-semibold leading-none text-ink-tertiary shadow-sm transition-colors hover:border-[#365DFF]/35 hover:bg-[#EEF3FF] hover:text-[#2447DB] focus:border-[#365DFF]/45 focus:bg-[#EEF3FF] focus:text-[#2447DB] focus:outline-none focus:ring-2 focus:ring-[#365DFF]/18"
      >
        i
      </button>
      <span
        role="tooltip"
        className="pointer-events-none absolute left-1/2 top-7 z-30 hidden w-max max-w-[280px] -translate-x-1/2 rounded-md border border-border-subtle bg-white px-3 py-2 text-left text-xs font-normal leading-5 text-ink-secondary shadow-[0_12px_28px_rgba(36,48,86,0.12)] group-hover:block group-focus-within:block"
      >
        {content}
      </span>
    </span>
  );
}
