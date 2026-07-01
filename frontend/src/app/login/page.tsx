"use client";

import { useRouter } from "next/navigation";
import { useEffect, useMemo, useState } from "react";

import { exchangeAiBaseJwt, getAiBaseSsoConfig, getAiBaseSsoLaunchUrl, getAuthSession, getIdentitySnapshotUsers } from "@/lib/api/client";
import { setSelectedIdentity, setSessionIdentity } from "@/lib/auth/identity";
import type { IntegrationIdentity } from "@/lib/contracts/types";

export default function LoginPage() {
  const router = useRouter();
  const [nextPath, setNextPath] = useState("/knowledge-bases");
  const [loggedOut, setLoggedOut] = useState(false);
  const [users, setUsers] = useState<IntegrationIdentity[]>([]);
  const [selectedUserKey, setSelectedUserKey] = useState("");
  const [loading, setLoading] = useState(true);
  const [ssoConfigured, setSsoConfigured] = useState(false);
  const [jwtToken, setJwtToken] = useState("");
  const [jwtSubmitting, setJwtSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const next = params.get("next") || "/knowledge-bases";
    setLoggedOut(params.get("logged_out") === "1");
    setNextPath(isSafeLocalPath(next) ? next : "/knowledge-bases");
  }, []);

  useEffect(() => {
    getAuthSession().then((session) => {
      if (!session?.identity) return;
      setSessionIdentity(session.identity);
      router.replace(nextPath);
    });
  }, [nextPath, router]);

  useEffect(() => {
    getAiBaseSsoConfig()
      .then((payload) => setSsoConfigured(payload.configured))
      .catch(() => setSsoConfigured(false));
  }, []);

  useEffect(() => {
    getIdentitySnapshotUsers(10)
      .then((payload) => {
        setUsers(payload.users);
        setSelectedUserKey((current) => current || makeUserKey(payload.users[0]));
        setError(null);
      })
      .catch((err) => {
        setUsers([]);
        setError(err instanceof Error ? err.message : "读取身份快照失败");
      })
      .finally(() => setLoading(false));
  }, []);

  const selectedUser = useMemo(
    () => users.find((user) => makeUserKey(user) === selectedUserKey) ?? users[0] ?? null,
    [selectedUserKey, users],
  );

  function handleEnter() {
    if (!selectedUser) return;
    setSelectedIdentity(selectedUser);
    router.replace(nextPath);
  }

  async function handleJwtExchange() {
    const token = jwtToken.trim();
    if (!token) return;
    setJwtSubmitting(true);
    try {
      const payload = await exchangeAiBaseJwt(token);
      setSessionIdentity(payload.identity);
      setError(null);
      router.replace(nextPath);
    } catch (err) {
      setError(err instanceof Error ? err.message : "JWT 交换失败");
    } finally {
      setJwtSubmitting(false);
    }
  }

  return (
    <main className="min-h-screen bg-[radial-gradient(circle_at_top_left,#EEF3FF_0,#F8FAFC_34%,#ECFDF5_100%)] px-6 py-10 text-ink-primary">
      <section className="mx-auto flex min-h-[calc(100vh-80px)] w-full max-w-[1040px] items-center">
        <div className="grid w-full overflow-hidden rounded-lg border border-[#BAE6FD] bg-white/86 shadow-[0_24px_70px_rgba(37,54,92,0.16)] backdrop-blur-xl lg:grid-cols-[360px_minmax(0,1fr)]">
          <div className="flex flex-col justify-between border-border-subtle bg-[linear-gradient(160deg,#EEF3FF_0%,#FFFFFF_54%,#ECFDF5_100%)] p-7 pr-7 lg:border-r">
            <div>
              <div className="flex h-11 w-11 items-center justify-center rounded-lg bg-[linear-gradient(135deg,#365DFF,#06B6D4)] text-sm font-bold text-white shadow-[0_12px_28px_rgba(54,93,255,0.25)]">
                KB
              </div>
              <h1 className="mt-6 text-[32px] font-bold leading-tight tracking-normal text-ink-primary">
                WiseWe RAG
              </h1>
              <p className="mt-3 text-sm leading-6 text-ink-secondary">
                优先通过 AI 基座正式 SSO 建立知识库本地短会话；本地身份快照仅保留为联调兜底入口。
              </p>
            </div>
            <p className="mt-8 text-xs leading-5 text-ink-tertiary">
              知识库只保存 HttpOnly 本地 session，不保存 AI 基座 JWT 原文或长期登录凭证。
            </p>
          </div>

          <div className="p-6">
            <div className="flex items-start justify-between gap-4 border-b border-border-subtle pb-4">
              <div>
                <p className="text-[11px] font-semibold uppercase tracking-[0.12em] text-ink-tertiary">
                  AI 基座 SSO
                </p>
                <h2 className="mt-1 text-xl font-semibold text-ink-primary">
                  {loggedOut ? "已退出知识库" : "进入知识库控制台"}
                </h2>
              </div>
              <span className="rounded-sm border border-status-good bg-[#ECFDF5] px-2 py-1 text-[11px] font-medium text-[#047857]">
                {ssoConfigured ? "正式 SSO" : "待配置"}
              </span>
            </div>

            {loggedOut ? (
              <div className="mt-4 rounded-lg border border-[#00A889]/20 bg-[#ECFDF5] p-4">
                <p className="text-sm font-semibold text-ink-primary">知识库本地会话已清理</p>
                <p className="mt-1 text-xs leading-5 text-ink-secondary">
                  本次操作只退出 WiseWe RAG 知识库，不会退出 AI 基座租户端账号。如需继续使用，请从 AI 基座重新进入。
                </p>
              </div>
            ) : null}

            <div className="mt-4 rounded-lg border border-[#0EA5E9]/20 bg-[#ECF8FF] p-4">
              <p className="text-sm font-semibold text-ink-primary">从 AI 基座进入</p>
              <p className="mt-1 text-xs leading-5 text-ink-secondary">
                使用一次性 sso_code 完成服务端交换，并创建知识库本地 session。
              </p>
              <a
                href={ssoConfigured ? getAiBaseSsoLaunchUrl(nextPath) : undefined}
                aria-disabled={!ssoConfigured}
                className={[
                  "mt-3 inline-flex h-10 w-full items-center justify-center rounded-md px-4 text-sm font-semibold transition-colors",
                  ssoConfigured
                    ? "bg-[linear-gradient(90deg,#365DFF,#06B6D4)] text-white shadow-[0_12px_28px_rgba(54,93,255,0.22)] hover:brightness-95"
                    : "cursor-not-allowed border border-border-subtle bg-white text-ink-tertiary",
                ].join(" ")}
              >
                {loggedOut ? "使用 AI 基座重新登录" : "打开 AI 基座 SSO"}
              </a>
            </div>

            <div className="mt-3 rounded-lg border border-border-subtle bg-white p-4">
              <p className="text-sm font-semibold text-ink-primary">JWT 一次性交换</p>
              <p className="mt-1 text-xs leading-5 text-ink-secondary">
                仅用于 AI 基座暂未提供 sso_code broker 的联调形态，JWT 不会写入本地存储。
              </p>
              <textarea
                value={jwtToken}
                onChange={(event) => setJwtToken(event.target.value)}
                rows={3}
                placeholder="粘贴 AI 基座短期 JWT"
                className="mt-3 w-full resize-none rounded-md border border-border-subtle px-3 py-2 text-xs outline-none focus:border-[#365DFF]"
              />
              <button
                type="button"
                onClick={handleJwtExchange}
                disabled={!jwtToken.trim() || jwtSubmitting}
                className="mt-2 inline-flex h-9 w-full items-center justify-center rounded-md border border-[#365DFF]/20 bg-white px-3 text-xs font-semibold text-[#2447DB] transition-colors hover:bg-[#EEF3FF] disabled:cursor-not-allowed disabled:text-ink-tertiary"
              >
                {jwtSubmitting ? "正在交换..." : "交换为知识库 session"}
              </button>
            </div>

            <div className="mt-5 border-t border-border-subtle pt-4">
              <div className="flex items-center justify-between gap-4">
                <div>
                  <p className="text-[11px] font-semibold uppercase tracking-[0.12em] text-ink-tertiary">
                    联调兜底
                  </p>
                  <h3 className="mt-1 text-sm font-semibold text-ink-primary">本地身份快照</h3>
                </div>
                <span className="rounded-sm border border-status-warning bg-[#FFFBEB] px-2 py-1 text-[11px] font-medium text-[#B45309]">
                  临时
                </span>
              </div>

            {loading ? (
              <div className="py-12 text-sm text-ink-secondary">正在读取本地身份快照...</div>
            ) : error ? (
              <div className="mt-4 rounded-sm border border-status-danger bg-[#FEF2F2] px-3 py-2 text-sm text-status-danger">
                {error}
              </div>
            ) : users.length === 0 ? (
              <div className="mt-4 rounded-sm border border-status-warning bg-[#FFFBEB] px-3 py-2 text-sm text-[#B45309]">
                本地身份快照为空，请先同步 AI 基座 1-5 组权限数据。
              </div>
            ) : (
              <div className="mt-4 space-y-2">
                {users.map((user) => {
                  const userKey = makeUserKey(user);
                  const checked = userKey === selectedUserKey;
                  return (
                    <label
                      key={userKey}
                      className={[
                        "flex cursor-pointer items-center justify-between gap-4 rounded-lg border px-4 py-3 shadow-sm transition-colors",
                        checked
                          ? "border-[#365DFF] bg-[#EEF3FF]"
                          : "border-border-subtle bg-white hover:border-[#0EA5E9] hover:bg-[#ECF8FF]",
                      ].join(" ")}
                    >
                      <span className="min-w-0">
                        <span className="block truncate text-sm font-semibold text-ink-primary">
                          {user.displayName || user.username || user.userId}
                        </span>
                        <span className="mt-1 block truncate text-xs text-ink-secondary">
                          {user.username} · user_id={user.userId}
                        </span>
                        <span className="mt-1 block truncate text-xs text-ink-tertiary">
                          {user.tenantName || `租户 ${user.tenantId}`} ·{" "}
                          {user.isTenantAdmin ? "租户管理员" : "普通用户"}
                        </span>
                      </span>
                      <input
                        type="radio"
                        name="identity"
                        value={userKey}
                        checked={checked}
                        onChange={() => setSelectedUserKey(userKey)}
                        className="h-4 w-4 accent-brand-primary"
                      />
                    </label>
                  );
                })}
              </div>
            )}

            <button
              type="button"
              onClick={handleEnter}
              disabled={!selectedUser || loading}
              className="mt-5 inline-flex h-10 w-full items-center justify-center rounded-md bg-[linear-gradient(90deg,#365DFF,#06B6D4)] px-4 text-sm font-semibold text-white shadow-[0_12px_28px_rgba(54,93,255,0.22)] transition-colors hover:brightness-95 disabled:cursor-not-allowed disabled:bg-border-strong disabled:bg-none"
            >
              进入控制台
            </button>
            </div>
          </div>
        </div>
      </section>
    </main>
  );
}

function makeUserKey(user: IntegrationIdentity | undefined): string {
  if (!user) return "";
  return `${user.tenantId}:${user.userId}`;
}

function isSafeLocalPath(path: string): boolean {
  return path.startsWith("/") && !path.startsWith("//");
}
