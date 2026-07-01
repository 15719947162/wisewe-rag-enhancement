"use client";

type Tab = { key: string; label: string; count?: number };

type TabsProps = {
  tabs: Tab[];
  active: string;
  onChange: (key: string) => void;
  variant?: "line" | "pill";
};

export function Tabs({ tabs, active, onChange, variant = "line" }: TabsProps) {
  if (variant === "pill") {
    return (
      <div className="flex gap-1">
        {tabs.map((tab) => (
          <button
            key={tab.key}
            onClick={() => onChange(tab.key)}
            className={[
              "inline-flex items-center gap-1.5 rounded-sm px-3 py-1.5 text-[13px] font-medium transition-colors duration-150",
              active === tab.key
                ? "bg-active text-brand-primary"
                : "text-ink-tertiary hover:text-ink-secondary",
            ].join(" ")}
          >
            {tab.label}
            {tab.count !== undefined && (
              <span className="font-mono text-xs">{tab.count}</span>
            )}
          </button>
        ))}
      </div>
    );
  }

  return (
    <div className="flex border-b border-border-subtle">
      {tabs.map((tab) => (
        <button
          key={tab.key}
          onClick={() => onChange(tab.key)}
          className={[
            "inline-flex items-center gap-1.5 px-4 py-2 text-[13px] font-medium transition-colors duration-150",
            "border-b-2 -mb-px",
            active === tab.key
              ? "border-brand-primary text-ink-primary"
              : "border-transparent text-ink-tertiary hover:text-ink-secondary",
          ].join(" ")}
        >
          {tab.label}
          {tab.count !== undefined && (
            <span className="font-mono text-xs text-ink-tertiary">{tab.count}</span>
          )}
        </button>
      ))}
    </div>
  );
}
