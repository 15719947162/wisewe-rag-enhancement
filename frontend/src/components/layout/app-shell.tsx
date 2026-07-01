"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { getAuthSession, logoutAuthSession } from "@/lib/api/client";
import {
  clearSelectedIdentity,
  getSelectedIdentity,
  IDENTITY_CHANGED_EVENT,
  setSessionIdentity,
} from "@/lib/auth/identity";
import type { IntegrationIdentity } from "@/lib/contracts/types";

type AppShellProps = { children: React.ReactNode };

const primaryNav = [
  {
    title: "控制台",
    signal: "系统态势",
    domain: "command",
    dotClass: "bg-[#365DFF]",
    iconClass: "bg-gradient-to-br from-[#365DFF] to-[#06B6D4] shadow-[0_10px_24px_rgba(54,93,255,0.24)]",
    activeClass: "border-[#365DFF]/20 bg-[linear-gradient(90deg,rgba(54,93,255,0.16),rgba(6,182,212,0.08)),rgba(255,255,255,0.80)] text-[#2447DB] shadow-[inset_3px_0_0_#365DFF,0_10px_22px_rgba(54,93,255,0.10)]",
    hoverClass: "hover:border-[#365DFF]/30 hover:bg-[#EEF3FF] hover:text-[#2447DB]",
    items: [
      { href: "/overview", label: "系统总览", icon: LayoutDashboardIcon },
    ],
  },
  {
    title: "权限管理",
    signal: "同步边界",
    domain: "permission",
    dotClass: "bg-[#0EA5E9]",
    iconClass: "bg-gradient-to-br from-[#0EA5E9] to-[#4F46E5] shadow-[0_10px_24px_rgba(14,165,233,0.22)]",
    activeClass: "border-[#0EA5E9]/20 bg-[linear-gradient(90deg,rgba(14,165,233,0.16),rgba(79,70,229,0.08)),rgba(255,255,255,0.80)] text-[#0369A1] shadow-[inset_3px_0_0_#0EA5E9,0_10px_22px_rgba(14,165,233,0.10)]",
    hoverClass: "hover:border-[#0EA5E9]/30 hover:bg-[#ECF8FF] hover:text-[#0369A1]",
    items: [
      { href: "/identity-monitor", label: "身份与权限同步", icon: ShieldIcon },
    ],
  },
  {
    title: "知识库管理",
    signal: "内容入库",
    domain: "knowledge",
    dotClass: "bg-[#00A889]",
    iconClass: "bg-gradient-to-br from-[#00A889] to-[#10B981] shadow-[0_10px_24px_rgba(0,168,137,0.22)]",
    activeClass: "border-[#00A889]/20 bg-[linear-gradient(90deg,rgba(0,168,137,0.16),rgba(16,185,129,0.08)),rgba(255,255,255,0.80)] text-[#007F69] shadow-[inset_3px_0_0_#00A889,0_10px_22px_rgba(0,168,137,0.10)]",
    hoverClass: "hover:border-[#00A889]/30 hover:bg-[#E9FBF5] hover:text-[#007F69]",
    items: [
      { href: "/knowledge-bases", label: "知识库", icon: DatabaseIcon },
      { href: "/ingestion", label: "入库管理", icon: UploadIcon },
      { href: "/knowledge-graph", label: "知识图谱", icon: GraphIcon },
    ],
  },
  {
    title: "召回与评测管理",
    signal: "RAG 与评测",
    domain: "rag",
    dotClass: "bg-[#7C3AED]",
    iconClass: "bg-gradient-to-br from-[#7C3AED] to-[#EC4899] shadow-[0_10px_24px_rgba(124,58,237,0.22)]",
    activeClass: "border-[#7C3AED]/20 bg-[linear-gradient(90deg,rgba(124,58,237,0.16),rgba(236,72,153,0.08)),rgba(255,255,255,0.80)] text-[#6D28D9] shadow-[inset_3px_0_0_#7C3AED,0_10px_22px_rgba(124,58,237,0.10)]",
    hoverClass: "hover:border-[#7C3AED]/30 hover:bg-[#F4F0FF] hover:text-[#6D28D9]",
    items: [
      { href: "/query", label: "召回管理", icon: SearchIcon },
      { href: "/evaluation", label: "评测记录", icon: BarChartIcon },
    ],
  },
  {
    title: "配置管理",
    signal: "运行参数",
    domain: "config",
    dotClass: "bg-[#FF8A00]",
    iconClass: "bg-gradient-to-br from-[#FF8A00] to-[#FACC15] shadow-[0_10px_24px_rgba(255,138,0,0.20)]",
    activeClass: "border-[#FF8A00]/20 bg-[linear-gradient(90deg,rgba(255,138,0,0.17),rgba(250,204,21,0.10)),rgba(255,255,255,0.80)] text-[#B85F00] shadow-[inset_3px_0_0_#FF8A00,0_10px_22px_rgba(255,138,0,0.10)]",
    hoverClass: "hover:border-[#FF8A00]/30 hover:bg-[#FFF5DD] hover:text-[#B85F00]",
    items: [
      { href: "/settings", label: "配置中心", icon: SettingsIcon },
    ],
  },
  {
    title: "API管理",
    signal: "开放接口",
    domain: "api",
    dotClass: "bg-[#0284C7]",
    iconClass: "bg-gradient-to-br from-[#0284C7] to-[#4F46E5] shadow-[0_10px_24px_rgba(2,132,199,0.22)]",
    activeClass: "border-[#0284C7]/20 bg-[linear-gradient(90deg,rgba(2,132,199,0.16),rgba(79,70,229,0.08)),rgba(255,255,255,0.80)] text-[#075985] shadow-[inset_3px_0_0_#0284C7,0_10px_22px_rgba(2,132,199,0.10)]",
    hoverClass: "hover:border-[#0284C7]/30 hover:bg-[#ECF8FF] hover:text-[#075985]",
    items: [
      { href: "/api-keys", label: "API Key", icon: KeyIcon },
      { href: "/openapi", label: "OpenAPI", icon: CodeIcon },
    ],
  },
  {
    title: "日志与统计管理",
    signal: "审计成本",
    domain: "observe",
    dotClass: "bg-[#E11D48]",
    iconClass: "bg-gradient-to-br from-[#E11D48] to-[#F97316] shadow-[0_10px_24px_rgba(225,29,72,0.20)]",
    activeClass: "border-[#E11D48]/20 bg-[linear-gradient(90deg,rgba(225,29,72,0.16),rgba(249,115,22,0.08)),rgba(255,255,255,0.80)] text-[#BE123C] shadow-[inset_3px_0_0_#E11D48,0_10px_22px_rgba(225,29,72,0.10)]",
    hoverClass: "hover:border-[#E11D48]/30 hover:bg-[#FFF0F4] hover:text-[#BE123C]",
    items: [
      { href: "/logs", label: "日志管理", icon: ClipboardIcon },
      { href: "/usage", label: "Token统计", icon: ActivityIcon },
    ],
  },
];

const mobileNav = [
  { href: "/overview", label: "总览", icon: LayoutDashboardIcon },
  { href: "/knowledge-bases", label: "知识库", icon: DatabaseIcon },
  { href: "/knowledge-graph", label: "图谱", icon: GraphIcon },
  { href: "/ingestion", label: "入库", icon: UploadIcon },
  { href: "/query", label: "召回", icon: SearchIcon },
];

function getCurrentLabel(pathname: string) {
  if (pathname.startsWith("/knowledge-bases/")) return "知识库工作台";
  for (const group of primaryNav) {
    const current = group.items.find((item) => pathname.startsWith(item.href));
    if (current) return current.label;
  }
  return "控制台";
}

function getCurrentDomain(pathname: string) {
  for (const group of primaryNav) {
    if (group.items.some((item) => pathname === item.href || pathname.startsWith(`${item.href}/`))) {
      return group;
    }
  }
  return primaryNav[0];
}

function getContextSummary(pathname: string) {
  if (pathname.startsWith("/knowledge-bases/")) {
    return "围绕单个知识库完成文档管理、入库调试、问答验证和评测分析。";
  }
  if (pathname.startsWith("/knowledge-bases")) {
    return "先选择知识库，再进入单库工作台处理文档、入库和问答。";
  }
  if (pathname.startsWith("/knowledge-graph")) {
    return "选择知识库后进入对应图谱画布，查看切片关系、实体关系和三元组预览。";
  }
  if (pathname.startsWith("/overview")) {
    return "查看全局队列、告警和资源健康状态。";
  }
  if (pathname.startsWith("/identity-monitor")) {
    return "查看 AI 基座身份快照、用户及权限同步日志和访问判断治理状态。";
  }
  if (pathname.startsWith("/api-keys") || pathname.startsWith("/openapi")) {
    return "管理外部调用凭证、OpenAPI 协议边界和接口能力范围。";
  }
  if (pathname.startsWith("/logs") || pathname.startsWith("/usage")) {
    return "追踪请求、审计、调用量和 token 消耗的治理闭环。";
  }
  if (pathname.startsWith("/settings")) {
    return "管理模型、解析、切片、召回、安全和日志相关运行配置。";
  }
  if (pathname.startsWith("/query")) {
    return "统一管理普通 RAG、Graph RAG、候选证据和召回链路验证。";
  }
  if (pathname.startsWith("/evaluation")) {
    return "查看跨知识库评测记录、评分结果和证据质量反馈。";
  }
  if (pathname.startsWith("/ingestion")) {
    return "管理文档上传、解析、切片、向量化和入库任务。";
  }
  return "保留全局能力页，用于跨知识库巡检和统一调试。";
}

export function AppShell({ children }: AppShellProps) {
  const pathname = usePathname();
  const router = useRouter();
  const currentLabel = getCurrentLabel(pathname);
  const currentDomain = getCurrentDomain(pathname);
  const [identity, setIdentity] = useState<IntegrationIdentity | null>(null);
  const [identityReady, setIdentityReady] = useState(false);
  const isSuperManager = Boolean(identity?.roleCodes?.includes("superManager"));
  const visiblePrimaryNav = isSuperManager
    ? primaryNav
    : primaryNav.filter((group) => !["permission", "config", "api", "observe"].includes(group.domain));

  useEffect(() => {
    let cancelled = false;

    const refreshIdentity = () => {
      setIdentity(getSelectedIdentity());
      setIdentityReady(true);
    };

    getAuthSession().then((session) => {
      if (cancelled) return;
      if (session?.identity) {
        setSessionIdentity(session.identity);
      } else {
        refreshIdentity();
      }
    });

    window.addEventListener(IDENTITY_CHANGED_EVENT, refreshIdentity);
    window.addEventListener("storage", refreshIdentity);
    return () => {
      cancelled = true;
      window.removeEventListener(IDENTITY_CHANGED_EVENT, refreshIdentity);
      window.removeEventListener("storage", refreshIdentity);
    };
  }, []);

  useEffect(() => {
    if (!identityReady || identity) return;
    router.replace(`/login?next=${encodeURIComponent(pathname || "/knowledge-bases")}`);
  }, [identity, identityReady, pathname, router]);

  useEffect(() => {
    if (!identityReady || !identity || isSuperManager) return;
    if (
      pathname.startsWith("/settings")
      || pathname.startsWith("/identity-monitor")
      || pathname.startsWith("/api-keys")
      || pathname.startsWith("/openapi")
      || pathname.startsWith("/logs")
      || pathname.startsWith("/usage")
    ) {
      router.replace("/overview");
    }
  }, [identity, identityReady, isSuperManager, pathname, router]);

  async function handleLogout() {
    try {
      await logoutAuthSession();
    } catch {
      // Session may already be expired; always clear local identity state.
    }
    clearSelectedIdentity();
    router.replace("/login?logged_out=1");
  }

  if (!identityReady || !identity) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-canvas text-sm text-ink-secondary">
        正在进入身份验证...
      </div>
    );
  }

  return (
    <div className="flex min-h-screen bg-transparent">
      <aside className="sticky top-0 hidden h-screen w-[286px] shrink-0 overflow-hidden border-r border-border-subtle/90 bg-[linear-gradient(180deg,rgba(255,255,255,0.92),rgba(249,251,255,0.84)),radial-gradient(circle_at_12%_0%,rgba(54,93,255,0.14),transparent_28%),radial-gradient(circle_at_92%_18%,rgba(236,72,153,0.10),transparent_24%)] backdrop-blur-xl before:absolute before:inset-y-0 before:left-0 before:w-px before:bg-[linear-gradient(180deg,rgba(54,93,255,0.18),rgba(0,168,137,0.12),transparent)] lg:flex lg:flex-col">
        <div className="flex items-center gap-3 px-6 pb-5 pt-7">
          <div className="flex h-[42px] w-[42px] items-center justify-center rounded-lg bg-[conic-gradient(from_130deg,#365DFF,#06B6D4,#EC4899,#FF8A00,#365DFF)] text-base font-extrabold text-white shadow-[0_14px_32px_rgba(54,93,255,0.28)]">
            W
          </div>
          <div>
            <p className="text-[15px] font-semibold text-ink-primary">WiseWe RAG</p>
            <p className="text-[11px] uppercase tracking-[0.12em] text-ink-tertiary">知识库管理系统</p>
          </div>
        </div>

        <nav className="flex-1 overflow-y-auto px-4 pb-6">
          {visiblePrimaryNav.map((group) => (
            <div key={group.title} className="mb-4 border-0 bg-transparent">
              <div className="mb-2 flex items-center gap-2 px-1">
                <span className={["h-1.5 w-1.5 rounded-full shadow-[0_0_0_4px_rgba(54,93,255,0.08)]", group.dotClass].join(" ")} />
                <p className="text-[11px] font-bold uppercase tracking-[0.12em] text-ink-tertiary">
                  {group.title}
                </p>
              </div>
              <div className="space-y-1">
                {group.items.map((item) => {
                  const active = pathname === item.href || pathname.startsWith(`${item.href}/`);
                  const Icon = item.icon;
                  return (
                    <Link
                      key={item.href}
                      href={item.href}
                      className={[
                        "group relative flex cursor-pointer items-center gap-3 rounded-lg border px-3 py-2.5 text-[13px] font-semibold transition-[background,border-color,color,box-shadow] duration-200",
                        active
                          ? group.activeClass
                          : `border-transparent bg-transparent text-ink-secondary ${group.hoverClass}`,
                      ].join(" ")}
                    >
                      <span className={["flex h-6 w-6 shrink-0 items-center justify-center rounded-lg text-white", group.iconClass].join(" ")}>
                        <Icon size={14} />
                      </span>
                      <span className="min-w-0 flex-1 truncate">{item.label}</span>
                      {active ? <span className={["h-5 w-1 rounded-full", group.dotClass].join(" ")} /> : null}
                    </Link>
                  );
                })}
              </div>
            </div>
          ))}
        </nav>
      </aside>

      <div className="flex min-w-0 flex-1 flex-col">
        <header className="sticky top-0 z-20 border-b border-border-subtle/90 bg-[#FAFBFF]/80 backdrop-blur-xl">
          <div className="mx-auto flex min-h-[76px] w-full max-w-[1480px] items-center gap-4 px-4 py-3 lg:px-[30px]">
            <div className="min-w-0">
              <div className="flex items-center gap-1.5 text-[13px] text-ink-tertiary">
                <span>WiseWe</span>
                <ChevronRightIcon size={14} />
                <span className="font-medium text-ink-primary">{currentLabel}</span>
                <span className={["ml-2 hidden rounded-full px-2 py-0.5 text-[10px] font-semibold text-white sm:inline-flex", currentDomain.iconClass].join(" ")}>
                  {currentDomain.title}
                </span>
              </div>
              <p className="mt-1 line-clamp-1 text-xs text-ink-secondary">{getContextSummary(pathname)}</p>
            </div>
            <div className="ml-auto hidden h-9 w-[min(430px,34vw)] items-center rounded-lg border border-border-subtle bg-white px-3 text-[13px] text-ink-tertiary xl:flex">
              搜索知识库、requestId、API Key、任务...
            </div>
            <div className="hidden items-center gap-2 md:flex">
              <Link
                href="/knowledge-bases?create=1"
                className="inline-flex h-9 cursor-pointer items-center rounded-lg bg-[linear-gradient(135deg,#365DFF,#7C3AED)] px-3.5 text-[13px] font-semibold text-white shadow-[0_12px_24px_rgba(54,93,255,0.20)] transition-[filter,box-shadow] hover:brightness-95 hover:shadow-[0_14px_30px_rgba(54,93,255,0.26)]"
              >
                新建知识库
              </Link>
              <Link
                href={`/login?next=${encodeURIComponent(pathname || "/knowledge-bases")}`}
                className="inline-flex h-9 cursor-pointer items-center rounded-lg border border-[#0EA5E9]/20 bg-white px-3 text-[13px] font-medium text-ink-secondary transition-colors hover:border-[#0EA5E9]/40 hover:bg-[#ECF8FF] hover:text-[#0369A1]"
              >
                切换身份
              </Link>
              <button
                type="button"
                onClick={handleLogout}
                className="inline-flex h-9 cursor-pointer items-center rounded-lg border border-[#E11D48]/22 bg-white px-3 text-[13px] font-medium text-[#BE123C] transition-colors hover:border-[#E11D48]/45 hover:bg-[#FFF0F4]"
              >
                退出
              </button>
            </div>
          </div>
        </header>

        <main className="mx-auto w-full max-w-[1480px] flex-1 px-4 py-7 pb-24 lg:px-[30px]">{children}</main>
      </div>

      <nav className="fixed bottom-0 left-0 right-0 z-30 flex border-t border-border-subtle bg-white/92 shadow-[0_-10px_30px_rgba(37,54,92,0.08)] backdrop-blur lg:hidden">
        {mobileNav.map((item) => {
          const active = pathname === item.href || pathname.startsWith(`${item.href}/`);
          const Icon = item.icon;
          return (
            <Link
              key={item.href}
              href={item.href}
              className={[
                "flex flex-1 flex-col items-center gap-1 py-2 text-[10px] font-medium transition-colors",
                active ? "text-brand-primary" : "text-ink-tertiary",
              ].join(" ")}
            >
              <Icon size={20} />
              {item.label}
            </Link>
          );
        })}
      </nav>
    </div>
  );
}

function LayoutDashboardIcon({ size = 16 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <rect width="7" height="9" x="3" y="3" rx="1" />
      <rect width="7" height="5" x="14" y="3" rx="1" />
      <rect width="7" height="9" x="14" y="12" rx="1" />
      <rect width="7" height="5" x="3" y="16" rx="1" />
    </svg>
  );
}

function DatabaseIcon({ size = 16 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <ellipse cx="12" cy="5" rx="9" ry="3" />
      <path d="M3 5v14c0 1.66 4.03 3 9 3s9-1.34 9-3V5" />
      <path d="M3 12c0 1.66 4.03 3 9 3s9-1.34 9-3" />
    </svg>
  );
}

function UploadIcon({ size = 16 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
      <polyline points="17 8 12 3 7 8" />
      <line x1="12" x2="12" y1="3" y2="15" />
    </svg>
  );
}

function GraphIcon({ size = 16 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <circle cx="5" cy="12" r="3" />
      <circle cx="19" cy="5" r="3" />
      <circle cx="19" cy="19" r="3" />
      <path d="M7.7 10.7 16.3 6.3" />
      <path d="M7.7 13.3 16.3 17.7" />
      <path d="M19 8v8" />
    </svg>
  );
}

function SearchIcon({ size = 16 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <circle cx="11" cy="11" r="8" />
      <path d="m21 21-4.3-4.3" />
    </svg>
  );
}

function BarChartIcon({ size = 16 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <line x1="18" x2="18" y1="20" y2="10" />
      <line x1="12" x2="12" y1="20" y2="4" />
      <line x1="6" x2="6" y1="20" y2="14" />
    </svg>
  );
}

function SettingsIcon({ size = 16 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M12.22 2h-.44a2 2 0 0 0-2 2v.18a2 2 0 0 1-1 1.73l-.43.25a2 2 0 0 1-2 0l-.15-.08a2 2 0 0 0-2.73.73l-.22.38a2 2 0 0 0 .73 2.73l.15.1a2 2 0 0 1 1 1.72v.51a2 2 0 0 1-1 1.74l-.15.09a2 2 0 0 0-.73 2.73l.22.38a2 2 0 0 0 2.73.73l.15-.08a2 2 0 0 1 2 0l.43.25a2 2 0 0 1 1 1.73V20a2 2 0 0 0 2 2h.44a2 2 0 0 0 2-2v-.18a2 2 0 0 1 1-1.73l.43-.25a2 2 0 0 1 2 0l.15.08a2 2 0 0 0 2.73-.73l.22-.39a2 2 0 0 0-.73-2.73l-.15-.08a2 2 0 0 1-1-1.74v-.5a2 2 0 0 1 1-1.74l.15-.09a2 2 0 0 0 .73-2.73l-.22-.38a2 2 0 0 0-2.73-.73l-.15.08a2 2 0 0 1-2 0l-.43-.25a2 2 0 0 1-1-1.73V4a2 2 0 0 0-2-2z" />
      <circle cx="12" cy="12" r="3" />
    </svg>
  );
}

function ShieldIcon({ size = 16 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z" />
      <path d="m9 12 2 2 4-5" />
    </svg>
  );
}

function KeyIcon({ size = 16 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <circle cx="7.5" cy="15.5" r="5.5" />
      <path d="m21 2-9.6 9.6" />
      <path d="m15 8 3 3" />
      <path d="m17 6 3 3" />
    </svg>
  );
}

function CodeIcon({ size = 16 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="m16 18 6-6-6-6" />
      <path d="m8 6-6 6 6 6" />
    </svg>
  );
}

function ClipboardIcon({ size = 16 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <rect width="16" height="18" x="4" y="3" rx="2" />
      <path d="M9 3h6v4H9z" />
      <path d="M8 12h8" />
      <path d="M8 16h5" />
    </svg>
  );
}

function ActivityIcon({ size = 16 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M22 12h-4l-3 8L9 4l-3 8H2" />
    </svg>
  );
}

function ChevronRightIcon({ size = 16 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="m9 18 6-6-6-6" />
    </svg>
  );
}

function DatabaseGlyph() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="white" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <ellipse cx="12" cy="5" rx="8" ry="3" />
      <path d="M4 5v6c0 1.66 3.58 3 8 3s8-1.34 8-3V5" />
      <path d="M4 11v8c0 1.66 3.58 3 8 3s8-1.34 8-3v-8" />
    </svg>
  );
}
