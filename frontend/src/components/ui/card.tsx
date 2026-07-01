type CardProps = {
  children: React.ReactNode;
  className?: string;
  hover?: boolean;
  padding?: "none" | "sm" | "md";
  accent?: "command" | "knowledge" | "ingestion" | "rag" | "governance" | "observe" | "neutral";
};

const accentStyles = {
  command: {
    border: "border-[#365DFF]/24",
    surface:
      "bg-[radial-gradient(circle_at_100%_100%,rgba(54,93,255,0.08),transparent_34%),linear-gradient(135deg,rgba(54,93,255,0.05),rgba(255,255,255,0.97)_62%)]",
    glow: "after:bg-[#365DFF]/8",
    shadow: "shadow-[0_10px_24px_rgba(54,93,255,0.055)]",
    icon: "bg-[#EEF3FF] text-[#2447DB]",
  },
  knowledge: {
    border: "border-[#00A889]/26",
    surface:
      "bg-[radial-gradient(circle_at_100%_100%,rgba(0,168,137,0.08),transparent_34%),linear-gradient(135deg,rgba(0,168,137,0.05),rgba(255,255,255,0.97)_62%)]",
    glow: "after:bg-[#00A889]/8",
    shadow: "shadow-[0_10px_24px_rgba(0,168,137,0.055)]",
    icon: "bg-[#E9FBF5] text-[#007F69]",
  },
  ingestion: {
    border: "border-[#FF8A00]/28",
    surface:
      "bg-[radial-gradient(circle_at_100%_100%,rgba(255,138,0,0.09),transparent_34%),linear-gradient(135deg,rgba(255,138,0,0.05),rgba(255,255,255,0.97)_62%)]",
    glow: "after:bg-[#FF8A00]/8",
    shadow: "shadow-[0_10px_24px_rgba(255,138,0,0.055)]",
    icon: "bg-[#FFF5DD] text-[#B85F00]",
  },
  rag: {
    border: "border-[#7C3AED]/26",
    surface:
      "bg-[radial-gradient(circle_at_100%_100%,rgba(124,58,237,0.08),transparent_34%),linear-gradient(135deg,rgba(124,58,237,0.05),rgba(255,255,255,0.97)_62%)]",
    glow: "after:bg-[#7C3AED]/8",
    shadow: "shadow-[0_10px_24px_rgba(124,58,237,0.055)]",
    icon: "bg-[#F4F0FF] text-[#6D28D9]",
  },
  governance: {
    border: "border-[#0EA5E9]/26",
    surface:
      "bg-[radial-gradient(circle_at_100%_100%,rgba(14,165,233,0.08),transparent_34%),linear-gradient(135deg,rgba(14,165,233,0.05),rgba(255,255,255,0.97)_62%)]",
    glow: "after:bg-[#0EA5E9]/8",
    shadow: "shadow-[0_10px_24px_rgba(14,165,233,0.055)]",
    icon: "bg-[#ECF8FF] text-[#0369A1]",
  },
  observe: {
    border: "border-[#E11D48]/26",
    surface:
      "bg-[radial-gradient(circle_at_100%_100%,rgba(225,29,72,0.08),transparent_34%),linear-gradient(135deg,rgba(225,29,72,0.05),rgba(255,255,255,0.97)_62%)]",
    glow: "after:bg-[#E11D48]/8",
    shadow: "shadow-[0_10px_24px_rgba(225,29,72,0.05)]",
    icon: "bg-[#FFF0F4] text-[#BE123C]",
  },
  neutral: {
    border: "border-border-subtle",
    surface:
      "bg-[radial-gradient(circle_at_100%_100%,rgba(54,93,255,0.045),transparent_34%),linear-gradient(135deg,rgba(250,251,255,0.98),rgba(255,255,255,0.98)_62%)]",
    glow: "after:bg-[#365DFF]/5",
    shadow: "shadow-sm",
    icon: "bg-[#EEF3FF] text-[#2447DB]",
  },
} as const;

export function Card({
  children,
  className = "",
  hover = false,
  padding = "md",
  accent = "neutral",
}: CardProps) {
  const padClass = padding === "none" ? "" : padding === "sm" ? "p-4" : "p-5";
  const style = accentStyles[accent];

  return (
    <div
      className={[
        "relative overflow-hidden rounded-lg border",
        "after:pointer-events-none after:absolute after:-bottom-10 after:-right-8 after:h-24 after:w-24 after:rounded-full after:content-['']",
        style.border,
        style.surface,
        style.glow,
        style.shadow,
        hover
          ? "cursor-pointer transition-[border-color,background-color,box-shadow,filter] duration-200 hover:shadow-panel hover:brightness-[0.995]"
          : "",
        padClass,
        className,
      ]
        .filter(Boolean)
        .join(" ")}
    >
      {children}
    </div>
  );
}

type MetricCardV3Props = {
  label: string;
  value: string | number;
  icon?: React.ReactNode;
  trend?: "up" | "down" | "neutral";
  trendLabel?: string;
  accent?: CardProps["accent"];
};

export function MetricCardV3({
  label,
  value,
  icon,
  trend,
  trendLabel,
  accent = "command",
}: MetricCardV3Props) {
  const style = accentStyles[accent ?? "command"];
  const trendColor =
    trend === "up"
      ? "text-status-success"
      : trend === "down"
        ? "text-status-danger"
        : "text-ink-tertiary";

  return (
    <Card accent={accent} className="min-h-[128px]">
      <div className="relative z-10 flex items-start justify-between gap-2">
        <p className="text-[12px] font-medium uppercase tracking-[0.08em] text-ink-tertiary">
          {label}
        </p>
        {icon && <span className={["rounded-md p-1.5", style.icon].join(" ")}>{icon}</span>}
      </div>
      <p className="relative z-10 mt-4 font-mono text-[36px] font-bold leading-none text-ink-primary">
        {value}
      </p>
      {trendLabel && (
        <p className={`relative z-10 mt-1.5 text-xs ${trendColor}`}>{trendLabel}</p>
      )}
    </Card>
  );
}
