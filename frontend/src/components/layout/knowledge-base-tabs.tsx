"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { buildKnowledgeBasePath } from "@/lib/kb-id";

type KnowledgeBaseTabsProps = {
  kbId: string;
};

const tabs = [
  { href: "", label: "知识库概览" },
  { href: "/documents", label: "文档管理" },
  { href: "/ingestion", label: "入库任务" },
  { href: "/query", label: "召回管理" },
  { href: "/graph", label: "知识图谱" },
  { href: "/evaluation", label: "评测记录" },
];

export function KnowledgeBaseTabs({ kbId }: KnowledgeBaseTabsProps) {
  const pathname = usePathname();
  const base = buildKnowledgeBasePath(kbId);

  return (
    <div className="overflow-x-auto rounded-md border border-[#00A889]/24 bg-[linear-gradient(90deg,rgba(0,168,137,0.11),rgba(255,255,255,0.82),rgba(124,58,237,0.06))] shadow-sm">
      <div className="flex min-w-max items-center gap-1 px-2 py-2">
        {tabs.map((tab) => {
          const href = `${base}${tab.href}`;
          const active = tab.href === "" ? pathname === href : pathname.startsWith(href);
          return (
            <Link
              key={tab.label}
              href={href}
              className={[
                "rounded-md px-3 py-2 text-sm font-medium transition-colors",
                active
                  ? "bg-white/86 text-[#007F69] shadow-[inset_0_-3px_0_#00A889,0_8px_18px_rgba(0,168,137,0.10)]"
                  : "text-ink-secondary hover:bg-white/72 hover:text-[#007F69]",
              ].join(" ")}
            >
              {tab.label}
            </Link>
          );
        })}
      </div>
    </div>
  );
}
