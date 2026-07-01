type SkeletonProps = { className?: string };

export function Skeleton({ className = "" }: SkeletonProps) {
  return (
    <div
      className={[
        "loading-shimmer rounded bg-[#E8EDF2]",
        className,
      ].join(" ")}
    />
  );
}

export function SkeletonCard() {
  return (
    <div className="rounded-md border border-border-subtle bg-panel p-5 space-y-3">
      <Skeleton className="h-3 w-24" />
      <Skeleton className="h-8 w-32" />
      <Skeleton className="h-3 w-16" />
    </div>
  );
}

export function SkeletonRow() {
  return (
    <div className="flex items-center gap-4 px-4 py-3">
      <Skeleton className="h-4 w-48" />
      <Skeleton className="h-4 w-16 ml-auto" />
      <Skeleton className="h-5 w-14 rounded-full" />
    </div>
  );
}

type LoadingOverlayProps = {
  active: boolean;
  label?: string;
  tone?: "blue" | "teal" | "amber" | "violet" | "rose";
};

const toneClasses = {
  blue: {
    bar: "from-[#365DFF] via-[#06B6D4] to-[#00A889]",
    text: "text-[#2447DB]",
    dot: "bg-[#365DFF]",
  },
  teal: {
    bar: "from-[#00A889] via-[#10B981] to-[#365DFF]",
    text: "text-[#047857]",
    dot: "bg-[#00A889]",
  },
  amber: {
    bar: "from-[#FF8A00] via-[#FACC15] to-[#00A889]",
    text: "text-[#C2410C]",
    dot: "bg-[#FF8A00]",
  },
  violet: {
    bar: "from-[#7C3AED] via-[#EC4899] to-[#F97316]",
    text: "text-[#6D28D9]",
    dot: "bg-[#7C3AED]",
  },
  rose: {
    bar: "from-[#E11D48] via-[#F97316] to-[#FF8A00]",
    text: "text-[#BE123C]",
    dot: "bg-[#E11D48]",
  },
} as const;

export function LoadingOverlay({
  active,
  label = "正在加载数据",
  tone = "blue",
}: LoadingOverlayProps) {
  if (!active) return null;
  const style = toneClasses[tone];

  return (
    <div className="pointer-events-none absolute inset-0 z-20 overflow-hidden rounded-[inherit] bg-white/64 backdrop-blur-[1px] animate-fade-in">
      <div className={`h-[3px] w-full loading-bar bg-gradient-to-r ${style.bar}`} />
      <div className="absolute right-4 top-4 flex items-center gap-2 rounded-md border border-border-subtle bg-white/92 px-3 py-1.5 text-xs font-medium shadow-sm">
        <span className={`h-2 w-2 rounded-full ${style.dot} animate-pulse`} />
        <span className={style.text}>{label}</span>
      </div>
    </div>
  );
}

export function LoadingRows({ rows = 5 }: { rows?: number }) {
  return (
    <div className="divide-y divide-border-subtle">
      {Array.from({ length: rows }).map((_, index) => (
        <div key={index} className="px-5 py-4">
          <Skeleton className="h-4 w-72 max-w-full" />
          <Skeleton className="mt-2 h-4 w-full max-w-3xl" />
        </div>
      ))}
    </div>
  );
}

export function LoadingCards({ count = 4, className = "" }: { count?: number; className?: string }) {
  return (
    <div className={className || "grid gap-4 md:grid-cols-2 xl:grid-cols-4"}>
      {Array.from({ length: count }).map((_, index) => (
        <Skeleton key={index} className="h-28 rounded-md" />
      ))}
    </div>
  );
}
