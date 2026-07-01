"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import { ContextRail } from "@/components/layout/context-rail";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { EmptyState } from "@/components/ui/empty-state";
import { LoadingOverlay, LoadingRows } from "@/components/ui/skeleton";
import { TablePagination, useClientPagination } from "@/components/ui/table-pagination";
import { getIdentitySnapshotUsers, getIdentitySyncLogs, removeIdentitySnapshotData, syncIdentityDelta } from "@/lib/api/client";
import { getSelectedIdentity, IDENTITY_CHANGED_EVENT } from "@/lib/auth/identity";
import type { IdentitySyncLogRecord, IntegrationIdentity } from "@/lib/contracts/types";
import { formatNumber, formatTimestamp } from "@/lib/formatters";

export default function IdentityMonitorPage() {
  const [users, setUsers] = useState<IntegrationIdentity[]>([]);
  const [syncLogs, setSyncLogs] = useState<IdentitySyncLogRecord[]>([]);
  const [currentIdentity, setCurrentIdentity] = useState(() => getSelectedIdentity());
  const [loading, setLoading] = useState(true);
  const [syncing, setSyncing] = useState(false);
  const [removing, setRemoving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [tenantFilter, setTenantFilter] = useState("");
  const [userFilter, setUserFilter] = useState("");
  const [roleFilter, setRoleFilter] = useState("");
  const [syncDateFilter, setSyncDateFilter] = useState("");

  const latestSync = syncLogs[0];
  const filteredUsers = useMemo(() => {
    const tenantKeyword = tenantFilter.trim().toLowerCase();
    const userKeyword = userFilter.trim().toLowerCase();
    const roleKeyword = roleFilter.trim().toLowerCase();
    return users.filter((item) => {
      const tenantText = `${item.tenantName || ""} ${item.tenantId || ""}`.toLowerCase();
      const userText = `${item.displayName || ""} ${item.username || ""} ${item.userId || ""}`.toLowerCase();
      const roleText = `${formatRoles(item.roleNames, item.roleCodes)} ${(item.roleCodes || []).join(" ")}`.toLowerCase();
      const syncedAt = item.syncedAt || latestSync?.finishedAt || "";
      return (
        (!tenantKeyword || tenantText.includes(tenantKeyword)) &&
        (!userKeyword || userText.includes(userKeyword)) &&
        (!roleKeyword || roleText.includes(roleKeyword)) &&
        (!syncDateFilter || syncedAt.includes(syncDateFilter))
      );
    });
  }, [latestSync?.finishedAt, roleFilter, syncDateFilter, tenantFilter, userFilter, users]);
  const pagination = useClientPagination(filteredUsers, 20);
  const canRunSync =
    Boolean(currentIdentity?.source?.startsWith("ai_base_sso_")) &&
    Boolean(currentIdentity?.roleCodes?.includes("superManager"));

  const stats = useMemo(() => {
    const tenantCount = new Set(users.map((item) => item.tenantId).filter(Boolean)).size;
    const adminCount = users.filter((item) => item.isTenantAdmin).length;
    return { tenantCount, adminCount };
  }, [users]);

  async function loadData() {
    setLoading(true);
    try {
      const logsPayload = await getIdentitySyncLogs(20);
      const latestSuccess = logsPayload.find((item) => item.status === "success");
      const snapshotLimit = Math.max(latestSuccess?.usersCount || 0, 1000);
      const snapshotPayload = await getIdentitySnapshotUsers(snapshotLimit);
      setUsers(snapshotPayload.users);
      setSyncLogs(logsPayload);
      setError(null);
    } catch (err) {
      setUsers([]);
      setSyncLogs([]);
      setError(err instanceof Error ? err.message : "加载身份与权限数据失败");
    } finally {
      setLoading(false);
    }
  }

  async function removeAllData() {
    const confirmed = window.confirm("将移除本地身份快照和同步运行记录。该操作不可撤销，是否继续？");
    if (!confirmed) return;
    const phrase = window.prompt("请输入“移除全部数据”确认操作");
    if (phrase !== "移除全部数据") {
      setError("已取消移除：确认文本不匹配");
      return;
    }
    setRemoving(true);
    try {
      await removeIdentitySnapshotData();
      await loadData();
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "移除身份与权限数据失败");
    } finally {
      setRemoving(false);
    }
  }

  async function runSync() {
    setSyncing(true);
    try {
      await syncIdentityDelta();
      await loadData();
    } catch (err) {
      setError(err instanceof Error ? err.message : "触发用户及权限同步失败");
    } finally {
      setSyncing(false);
    }
  }

  useEffect(() => {
    void loadData();
  }, []);

  useEffect(() => {
    const refreshIdentity = () => setCurrentIdentity(getSelectedIdentity());
    refreshIdentity();
    window.addEventListener(IDENTITY_CHANGED_EVENT, refreshIdentity);
    window.addEventListener("storage", refreshIdentity);
    return () => {
      window.removeEventListener(IDENTITY_CHANGED_EVENT, refreshIdentity);
      window.removeEventListener("storage", refreshIdentity);
    };
  }, []);

  return (
    <div className="space-y-6">
      <ContextRail
        title="身份与权限同步"
        description="查看 AI 基座租户、用户、角色映射与用户及权限同步状态。"
        showGlobalHint={false}
      />

      <section className="relative overflow-hidden rounded-lg border border-[#BAE6FD] bg-[linear-gradient(135deg,#ECF8FF_0%,#FFFFFF_55%,#EEF3FF_100%)] p-6 shadow-[0_18px_44px_rgba(14,165,233,0.12)]">
        <div className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
          <div>
            <div className="flex flex-wrap items-center gap-2">
              <h1 className="text-[30px] font-bold leading-tight text-ink-primary">身份与权限同步</h1>
              <Badge variant="neutral">P1</Badge>
            </div>
            <p className="mt-2 text-sm text-ink-secondary">权限管理域 / 身份与访问治理</p>
          </div>
        </div>
      </section>

      <section className="grid gap-4 md:grid-cols-4">
        <MetricCard label="租户" value={formatNumber(stats.tenantCount)} helper="当前快照可见租户" />
        <MetricCard label="用户" value={formatNumber(users.length)} helper="当前快照用户明细" />
        <MetricCard label="RAG管理员" value={formatNumber(stats.adminCount)} helper="命中 superManager 的身份" />
        <MetricCard
          label="最近同步"
          value={latestSync ? formatNumber(latestSync.usersCount) : "--"}
          helper={latestSync ? `用户记录 / ${latestSync.status}` : "暂无同步记录"}
        />
      </section>

      {error && <div className="rounded-sm border border-status-danger bg-[#FEF2F2] px-4 py-3 text-sm text-status-danger">{error}</div>}

      <section className="overflow-hidden rounded-lg border border-[#0EA5E9]/24 bg-white/86 shadow-panel">
        <div className="border-b border-[#BAE6FD]/80 bg-[linear-gradient(90deg,rgba(14,165,233,0.12),rgba(79,70,229,0.08),rgba(255,255,255,0.76))] px-5 py-4">
          <div className="flex flex-col gap-3 xl:flex-row xl:items-start xl:justify-between">
            <div>
              <p className="text-[11px] font-semibold uppercase tracking-[0.1em] text-[#075985]">Identity Snapshot</p>
              <h2 className="mt-1 text-base font-semibold text-ink-primary">用户及权限明细</h2>
              <p className="mt-1 text-sm text-ink-secondary">
                展示本地只读快照中的租户、用户、AI 基座原始角色和知识库侧 RAG 角色映射。
              </p>
            </div>
            <div className="flex flex-wrap gap-2">
              <Button size="sm" variant="ghost" onClick={loadData} disabled={loading || syncing || removing}>
                刷新
              </Button>
              <Button
                size="sm"
                variant="primary"
                onClick={runSync}
                loading={syncing}
                disabled={loading || syncing || removing || !canRunSync}
                title={canRunSync ? "触发 AI 基座身份增量同步" : "仅 AI 基座 SSO 登录的 superManager 可同步"}
              >
                立即同步
              </Button>
              <Button
                size="sm"
                variant="danger-ghost"
                onClick={removeAllData}
                loading={removing}
                disabled={loading || syncing || removing || !canRunSync}
                title={canRunSync ? "移除本地身份快照和同步运行记录" : "仅 superManager 可移除同步数据"}
              >
                移除全部数据
              </Button>
              <Link
                href="/logs"
                className="inline-flex h-8 items-center rounded-lg border border-[#BAE6FD] bg-white/85 px-3 text-xs font-semibold text-[#075985] shadow-sm transition-colors hover:border-[#0EA5E9] hover:bg-[#ECF8FF]"
              >
                查看日志
              </Link>
            </div>
          </div>
          <div className="mt-4 grid gap-3 md:grid-cols-2 xl:grid-cols-4">
            <FilterInput label="租户名称" value={tenantFilter} onChange={setTenantFilter} placeholder="名称 / ID" />
            <FilterInput label="用户名称" value={userFilter} onChange={setUserFilter} placeholder="姓名 / 账号 / ID" />
            <FilterInput label="原始角色" value={roleFilter} onChange={setRoleFilter} placeholder="角色名 / 角色码" />
            <FilterInput label="同步时间" value={syncDateFilter} onChange={setSyncDateFilter} type="date" />
          </div>
        </div>
        <LoadingOverlay active={(loading || syncing || removing) && users.length > 0} tone="blue" label="正在刷新身份快照" />
        {loading && users.length === 0 ? (
          <LoadingRows rows={6} />
        ) : users.length === 0 ? (
          <EmptyState title="暂无身份快照" description="使用 AI 基座 SSO 的 superManager 触发同步后，这里会展示租户、用户和角色明细。" />
        ) : filteredUsers.length === 0 ? (
          <EmptyState title="没有匹配结果" description="调整租户、用户、角色或同步时间筛选后再查看。" />
        ) : (
          <div className="overflow-x-auto animate-data-enter">
            <table className="w-full min-w-[1040px] text-sm">
              <thead>
                <tr className="border-b border-[#BAE6FD]/80 bg-[#ECF8FF] text-[12px] font-medium uppercase tracking-[0.06em] text-[#075985]">
                  <th className="px-4 py-2.5 text-left">租户</th>
                  <th className="px-4 py-2.5 text-left">用户</th>
                  <th className="px-4 py-2.5 text-left">原始角色</th>
                  <th className="px-4 py-2.5 text-left">RAG角色</th>
                  <th className="px-4 py-2.5 text-left">同步方式</th>
                  <th className="px-4 py-2.5 text-left">同步时间</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-[#BAE6FD]/70">
                {pagination.pageItems.map((item) => (
                  <tr key={`${item.tenantId}:${item.userId}`} className="transition-colors hover:bg-[#ECF8FF]/70">
                    <td className="px-4 py-3 align-top">
                      <p className="font-medium text-ink-primary">{item.tenantName || item.tenantId}</p>
                      <p className="mt-1 font-mono text-xs text-ink-tertiary">{item.tenantId}</p>
                    </td>
                    <td className="px-4 py-3 align-top">
                      <p className="font-medium text-ink-primary">{item.displayName || item.username || item.userId}</p>
                      <p className="mt-1 text-xs text-ink-tertiary">{item.username || "-"} / {item.userId}</p>
                    </td>
                    <td className="w-[190px] px-4 py-3 align-top">
                      <p className="max-w-[180px] truncate text-ink-primary" title={formatRoles(item.roleNames, item.roleCodes)}>
                        {formatRoles(item.roleNames, item.roleCodes)}
                      </p>
                      <p className="mt-1 max-w-[180px] truncate font-mono text-xs text-ink-tertiary" title={item.roleCodes.length > 0 ? item.roleCodes.join(" / ") : "-"}>
                        {item.roleCodes.length > 0 ? item.roleCodes.join(" / ") : "-"}
                      </p>
                    </td>
                    <td className="px-4 py-3 align-top">
                      <Badge variant={item.isTenantAdmin ? "success" : "neutral"} dot>{formatRagRole(item)}</Badge>
                    </td>
                    <td className="px-4 py-3 align-top">
                      <p className="font-mono text-xs text-ink-primary">{latestSync?.syncMode || item.source || "-"}</p>
                      {latestSync?.sourceHost && <p className="mt-1 max-w-[220px] truncate text-xs text-ink-tertiary">{latestSync.sourceHost}</p>}
                    </td>
                    <td className="px-4 py-3 align-top">
                      <p className="text-ink-primary">{formatTimestamp(item.syncedAt || latestSync?.finishedAt || "")}</p>
                      {latestSync?.maxUpdatedAt && <p className="mt-1 text-xs text-ink-tertiary">水位 {latestSync.maxUpdatedAt}</p>}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            <TablePagination
              page={pagination.page}
              pageSize={pagination.pageSize}
              total={pagination.total}
              pageCount={pagination.pageCount}
              startIndex={pagination.startIndex}
              endIndex={pagination.endIndex}
              onPageChange={pagination.setPage}
              onPageSizeChange={pagination.setPageSize}
            />
          </div>
        )}
      </section>
    </div>
  );
}

function FilterInput({
  label,
  value,
  onChange,
  placeholder = "",
  type = "text",
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
  type?: "text" | "date";
}) {
  return (
    <label className="block">
      <span className="text-xs font-medium text-ink-secondary">{label}</span>
      <input
        type={type}
        value={value}
        onChange={(event) => onChange(event.target.value)}
        placeholder={placeholder}
        className="mt-1 h-9 w-full rounded-md border border-[#BAE6FD] bg-white px-3 text-sm text-ink-primary outline-none transition-colors placeholder:text-ink-tertiary focus:border-[#0EA5E9] focus:ring-2 focus:ring-[#BAE6FD]/70"
      />
    </label>
  );
}

function formatRoles(roleNames: string[] | undefined, roleCodes: string[]): string {
  const names = (roleNames || []).filter(Boolean);
  if (names.length > 0) return names.join(" / ");
  if (roleCodes.length > 0) return roleCodes.join(" / ");
  return "未关联角色";
}

function formatRagRole(item: IntegrationIdentity): string {
  if (item.ragRole === "租户管理员") return "RAG管理员";
  return item.ragRole || (item.isTenantAdmin ? "RAG管理员" : "普通用户");
}

function MetricCard({ label, value, helper }: { label: string; value: string; helper: string }) {
  return (
    <div className="relative min-h-[128px] overflow-hidden rounded-md border border-[#0EA5E9]/24 bg-[radial-gradient(circle_at_88%_18%,rgba(14,165,233,0.16),transparent_36%),linear-gradient(135deg,#ECF8FF,#FFFFFF_58%)] p-5 shadow-[0_14px_34px_rgba(14,165,233,0.10)] after:pointer-events-none after:absolute after:-bottom-10 after:-right-8 after:h-24 after:w-24 after:rounded-full after:bg-[#0EA5E9]/14 after:content-['']">
      <p className="text-[12px] font-medium uppercase tracking-[0.08em] text-[#075985]">{label}</p>
      <p className="mt-2 font-mono text-[34px] font-bold leading-none text-ink-primary">{value}</p>
      <p className="mt-2 text-xs text-ink-secondary">{helper}</p>
    </div>
  );
}
