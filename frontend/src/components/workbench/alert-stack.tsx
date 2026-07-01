import type { AlertItem } from "@/lib/contracts/types";
import { StatusBadge } from "@/components/ui/status-badge";
import { getAreaLabel } from "@/lib/i18n/zh-cn";

export function AlertStack({ alerts }: { alerts: AlertItem[] }) {
  return (
    <div className="relative overflow-hidden rounded-lg border border-[#FECDD3] bg-[linear-gradient(135deg,#FFF0F4_0%,#FFFFFF_58%,#FFF7ED_100%)] p-5 shadow-panel">
      <div className="relative text-[11px] font-semibold uppercase tracking-[0.18em] text-[#BE123C]">告警栈</div>
      <div className="mt-4 space-y-3">
        {alerts.map((alert) => (
          <article key={alert.id} className="rounded-lg border border-[#FED7AA] bg-white p-4 shadow-sm transition-colors hover:border-[#FB923C]">
            <div className="flex items-start justify-between gap-3">
              <div>
                <h3 className="text-base font-semibold">{alert.title}</h3>
                <p className="mt-2 text-sm leading-6 text-ink-secondary">{alert.description}</p>
                <div className="mt-3 text-xs uppercase tracking-[0.16em] text-ink-tertiary">{getAreaLabel(alert.area)}</div>
              </div>
              <StatusBadge status={alert.severity} />
            </div>
          </article>
        ))}
      </div>
    </div>
  );
}
