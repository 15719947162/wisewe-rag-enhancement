import { formatLatency, formatNumber } from "@/lib/formatters";
import type { IngestionStage } from "@/lib/contracts/types";

type ParseKeyMetricRow = {
  alias: string;
  calls: number;
  successes: number;
  failures: number;
  throttles: number;
  totalMs: number;
};

type ParseKeyMetricSummary = {
  poolSize: number;
  maxInflightPerKey: number;
  throttleCount: number;
  retryCount: number;
  cooldownCount: number;
  rows: ParseKeyMetricRow[];
};

const KEY_METRIC_PATTERN = /^parseKey\.([^.]+)\.(calls|successes|failures|throttles|totalMs)$/;

function numberMetric(metrics: Record<string, number> | undefined, key: string): number {
  const value = metrics?.[key];
  return typeof value === "number" && Number.isFinite(value) ? value : 0;
}

function buildParseKeySummary(stage?: IngestionStage): ParseKeyMetricSummary | null {
  const metrics = stage?.metrics;
  if (!metrics) {
    return null;
  }

  const hasParseKeyMetrics = Object.keys(metrics).some(
    (key) => key === "parseKeyPoolSize" || key.startsWith("parseKey."),
  );
  if (!hasParseKeyMetrics) {
    return null;
  }

  const rowsByAlias = new Map<string, ParseKeyMetricRow>();
  for (const [key, value] of Object.entries(metrics)) {
    if (typeof value !== "number" || !Number.isFinite(value)) {
      continue;
    }

    const match = KEY_METRIC_PATTERN.exec(key);
    if (!match) {
      continue;
    }

    const [, alias, metricName] = match;
    const row =
      rowsByAlias.get(alias) ??
      ({
        alias,
        calls: 0,
        successes: 0,
        failures: 0,
        throttles: 0,
        totalMs: 0,
      } satisfies ParseKeyMetricRow);
    row[metricName as keyof Omit<ParseKeyMetricRow, "alias">] = value;
    rowsByAlias.set(alias, row);
  }

  const rows = Array.from(rowsByAlias.values()).sort((a, b) =>
    a.alias.localeCompare(b.alias, "zh-CN", { numeric: true }),
  );

  return {
    poolSize: numberMetric(metrics, "parseKeyPoolSize"),
    maxInflightPerKey: numberMetric(metrics, "parseKeyMaxInflightPerKey"),
    throttleCount: numberMetric(metrics, "parseKeyThrottleCount"),
    retryCount: numberMetric(metrics, "parseKeyRetryCount"),
    cooldownCount: numberMetric(metrics, "parseKeyCooldownCount"),
    rows,
  };
}

export function ParseKeyMetricsPanel({ stage }: { stage?: IngestionStage }) {
  const summary = buildParseKeySummary(stage);
  if (!summary) {
    return null;
  }

  const statusLabel = summary.poolSize > 1 ? `已启用 ${summary.poolSize} 组` : "单组凭证";

  return (
    <div className="rounded-lg border border-[#FED7AA] bg-[linear-gradient(135deg,#FFF7ED,#FFFFFF)] p-4 shadow-sm">
      <div className="flex items-center justify-between gap-3">
        <p className="text-[11px] font-semibold uppercase tracking-[0.08em] text-[#C2410C]">解析凭证池</p>
        <span className="shrink-0 rounded-md border border-[#FDBA74] bg-white px-2 py-1 text-[11px] font-medium text-[#C2410C]">
          {statusLabel}
        </span>
      </div>

      <div className="mt-3 grid grid-cols-2 gap-x-5 gap-y-3 text-sm">
        <MetricText label="凭证池" value={summary.poolSize > 0 ? `${summary.poolSize} 组` : "--"} />
        <MetricText
          label="单 Key 并发"
          value={summary.maxInflightPerKey > 0 ? `${summary.maxInflightPerKey}` : "--"}
        />
        <MetricText label="限流" value={formatNumber(summary.throttleCount)} />
        <MetricText label="重试 / 冷却" value={`${formatNumber(summary.retryCount)} / ${formatNumber(summary.cooldownCount)}`} />
      </div>

      {summary.rows.length > 0 && (
        <div className="mt-4 divide-y divide-[#FED7AA]/80 border-t border-[#FED7AA]/80">
          {summary.rows.map((row) => (
            <div key={row.alias} className="grid grid-cols-[minmax(70px,1fr)_auto] gap-3 py-2 text-[12px]">
              <div>
                <p className="font-mono text-ink-primary">{row.alias}</p>
                <p className="mt-1 text-ink-tertiary">
                  调用 {formatNumber(row.calls)} · 成功 {formatNumber(row.successes)}
                </p>
              </div>
              <div className="text-right font-mono text-ink-secondary">
                <p>{row.totalMs > 0 ? formatLatency(row.totalMs) : "--"}</p>
                <p className="mt-1 text-ink-tertiary">
                  失败 {formatNumber(row.failures)} / 限流 {formatNumber(row.throttles)}
                </p>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function MetricText({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <p className="text-[11px] text-ink-tertiary">{label}</p>
      <p className="mt-1 font-mono text-ink-primary">{value}</p>
    </div>
  );
}
