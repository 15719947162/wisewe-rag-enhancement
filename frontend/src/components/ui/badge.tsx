type BadgeVariant =
  | "success"
  | "warning"
  | "danger"
  | "info"
  | "pending"
  | "running"
  | "degraded"
  | "neutral";

const variantClasses: Record<BadgeVariant, string> = {
  success:  "border-[#10B981]/20 bg-[#E9FBF5] text-[#007F69]",
  warning:  "border-[#FF8A00]/20 bg-[#FFF5DD] text-[#B85F00]",
  danger:   "border-[#E11D48]/20 bg-[#FFF0F4] text-[#BE123C]",
  info:     "border-[#365DFF]/20 bg-[#EEF3FF] text-[#2447DB]",
  pending:  "border-[#94A3B8]/30 bg-[#F1F5F9] text-[#566274]",
  running:  "border-[#0EA5E9]/20 bg-[#ECF8FF] text-[#0369A1]",
  degraded: "border-[#F97316]/20 bg-[#FFF5DD] text-[#C2410C]",
  neutral:  "border-[#DDE3F0] bg-[#FAFBFF] text-ink-secondary",
};

type BadgeProps = {
  variant?: BadgeVariant;
  dot?: boolean;
  children: React.ReactNode;
  className?: string;
};

export function Badge({
  variant = "neutral",
  dot = false,
  children,
  className = "",
}: BadgeProps) {
  return (
    <span
      className={[
        "inline-flex items-center gap-1.5 rounded-[999px] border px-2 py-0.5 text-xs font-medium",
        variantClasses[variant],
        className,
      ].join(" ")}
    >
      {dot && (
        <span
          className={[
            "inline-block h-1.5 w-1.5 rounded-full bg-current",
            variant === "running" ? "animate-pulse" : "",
          ].join(" ")}
        />
      )}
      {children}
    </span>
  );
}

type CountBadgeProps = { count: number; className?: string };

export function CountBadge({ count, className = "" }: CountBadgeProps) {
  return (
    <span
      className={[
        "inline-flex h-4 min-w-4 items-center justify-center rounded-full bg-status-danger px-1 text-[10px] font-semibold text-ink-inverse",
        className,
      ].join(" ")}
    >
      {count > 99 ? "99+" : count}
    </span>
  );
}
