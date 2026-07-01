import { formatScore } from "@/lib/formatters";

export function ScoreBadge({
  label,
  value,
}: {
  label: string;
  value: number | null | undefined;
}) {
  const numeric = value ?? null;
  const tone =
    numeric === null
      ? "border-[#94A3B8]/30 bg-[#F1F5F9] text-[#566274]"
      : numeric >= 0.75
        ? "border-[#10B981]/24 bg-[#E9FBF5] text-[#007F69]"
        : numeric >= 0.5
          ? "border-[#FF8A00]/24 bg-[#FFF5DD] text-[#B85F00]"
          : "border-[#E11D48]/24 bg-[#FFF0F4] text-[#BE123C]";

  return (
    <div className={`relative overflow-hidden rounded-lg border px-4 py-3 shadow-sm ${tone}`}>
      <div className="text-[11px] font-semibold uppercase tracking-[0.16em]">{label}</div>
      <div className="mt-2 font-mono text-2xl font-semibold">{formatScore(value)}</div>
    </div>
  );
}
