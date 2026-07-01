import Link from "next/link";
import { ContextRail } from "@/components/layout/context-rail";
import { Badge } from "@/components/ui/badge";

type GovernancePlaceholderProps = {
  title: string;
  domain: string;
  priority: "P0" | "P1";
  description: string;
  delivered: string[];
  pending: string[];
  links?: Array<{ href: string; label: string }>;
};

export function GovernancePlaceholder({
  title,
  domain,
  priority,
  description,
  delivered,
  pending,
  links = [],
}: GovernancePlaceholderProps) {
  return (
    <div className="space-y-6">
      <ContextRail title={title} description={description} showGlobalHint={false} />

      <div className="relative overflow-hidden rounded-lg border border-[#BAE6FD] bg-[linear-gradient(135deg,#ECF8FF_0%,#FFFFFF_55%,#EEF3FF_100%)] p-6 shadow-[0_18px_44px_rgba(14,165,233,0.12)]">
        <div className="relative flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
          <div>
            <div className="flex flex-wrap items-center gap-2">
              <h1 className="text-[30px] font-bold leading-tight text-ink-primary">{title}</h1>
              <Badge variant={priority === "P0" ? "info" : "neutral"}>{priority}</Badge>
            </div>
            <p className="mt-2 text-sm text-ink-secondary">{domain}</p>
          </div>
          {links.length > 0 && (
            <div className="flex flex-wrap gap-2">
              {links.map((link) => (
                <Link
                  key={link.href}
                  href={link.href}
                  className="inline-flex h-9 items-center rounded-md border border-[#BAE6FD] bg-white/85 px-3 text-[13px] font-medium text-[#075985] shadow-sm transition-colors hover:border-[#0EA5E9] hover:bg-[#ECF8FF]"
                >
                  {link.label}
                </Link>
              ))}
            </div>
          )}
        </div>
      </div>

      <section className="grid gap-5 xl:grid-cols-2">
        <GovernanceList title="已落地的底座" tone="done" items={delivered} />
        <GovernanceList title="待补齐能力" tone="pending" items={pending} />
      </section>

      <section className="rounded-lg border border-[#FF8A00]/24 bg-[linear-gradient(135deg,rgba(255,138,0,0.10),rgba(255,255,255,0.92))] px-5 py-4 shadow-panel">
        <div className="flex items-start gap-3">
          <div className="mt-1 h-2.5 w-2.5 rounded-full bg-status-warning" />
          <div>
            <h2 className="text-base font-semibold text-ink-primary">当前入口说明</h2>
            <p className="mt-1 text-sm leading-6 text-ink-secondary">
              该页面先用于承接 BRD 的功能域入口，展示当前阶段已经落地的能力和后续待补齐项。后续真实功能接入后，可直接替换为业务工作台。
            </p>
          </div>
        </div>
      </section>
    </div>
  );
}

function GovernanceList({
  title,
  tone,
  items,
}: {
  title: string;
  tone: "done" | "pending";
  items: string[];
}) {
  return (
    <div className="overflow-hidden rounded-lg border border-[#0EA5E9]/24 bg-white/86 shadow-panel">
      <div className="border-b border-[#BAE6FD]/80 bg-[linear-gradient(90deg,rgba(14,165,233,0.12),rgba(79,70,229,0.08),rgba(255,255,255,0.76))] px-5 py-4">
        <h2 className="text-base font-semibold text-ink-primary">{title}</h2>
      </div>
      <ul className="divide-y divide-border-subtle">
        {items.map((item) => (
          <li key={item} className="flex gap-3 px-5 py-3 text-sm leading-6 text-ink-secondary">
            <span className={["mt-2 h-2 w-2 shrink-0 rounded-full", tone === "done" ? "bg-status-success" : "bg-status-pending"].join(" ")} />
            <span>{item}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}
