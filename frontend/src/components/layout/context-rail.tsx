"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { buildKnowledgeBasePath, formatKbId } from "@/lib/kb-id";
import { getCurrentKnowledgeBaseId } from "@/lib/knowledge-base-context";
import { InfoTooltip } from "@/components/ui/info-tooltip";

type ContextRailProps = {
  title: string;
  description: string;
  showGlobalHint?: boolean;
};

export function ContextRail({
  title,
  description,
  showGlobalHint = true,
}: ContextRailProps) {
  const [currentKbId, setCurrentKbId] = useState<string | null>(null);
  const tooltipContent = showGlobalHint
    ? `${description} 当前页面是跨知识库的全局视图；如需回到单个知识库上下文，可使用右侧快捷入口。`
    : description;

  useEffect(() => {
    setCurrentKbId(getCurrentKnowledgeBaseId());
  }, []);

  return (
    <div className="preview-panel p-5 [--panel-tone:#365DFF]">
      <div className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
        <div className="max-w-3xl">
          <p className="preview-eyebrow text-[#365DFF]">Global Workspace</p>
          <div className="mt-2 flex flex-wrap items-center gap-2">
            <h1 className="text-[28px] font-bold leading-tight text-ink-primary">{title}</h1>
            <InfoTooltip content={tooltipContent} />
          </div>
        </div>

        <div className="grid gap-3 sm:grid-cols-2">
          <Link
            href="/knowledge-bases"
            className="relative overflow-hidden rounded-lg border border-[#00A889]/24 bg-[linear-gradient(135deg,#E9FBF5,#FFFFFF_60%)] px-4 py-3 text-sm font-medium text-ink-primary shadow-[0_12px_26px_rgba(0,168,137,0.10)] transition-colors hover:border-[#00A889]/45"
          >
            <p>返回知识库列表</p>
            <p className="mt-1 text-xs leading-5 text-ink-secondary">重新选择知识库，并进入单库工作台。</p>
          </Link>

          {currentKbId ? (
            <Link
              href={buildKnowledgeBasePath(currentKbId)}
              className="relative overflow-hidden rounded-lg border border-[#7C3AED]/24 bg-[linear-gradient(135deg,#F4F0FF,#FFFFFF_60%)] px-4 py-3 text-sm font-medium text-ink-primary shadow-[0_12px_26px_rgba(124,58,237,0.10)] transition-colors hover:border-[#7C3AED]/45"
            >
              <p>返回最近知识库</p>
              <p className="mt-1 truncate font-mono text-xs leading-5 text-ink-secondary">{formatKbId(currentKbId)}</p>
            </Link>
          ) : (
            <div className="rounded-lg border border-dashed border-border-subtle bg-elevated px-4 py-3 text-sm text-ink-tertiary">
              <p>暂无最近知识库</p>
              <p className="mt-1 text-xs leading-5">进入任意单库工作台后，这里会提供快速返回入口。</p>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
