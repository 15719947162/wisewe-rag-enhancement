"use client";

import { useEffect, useMemo, useState } from "react";
import { ContextRail } from "@/components/layout/context-rail";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { EmptyState } from "@/components/ui/empty-state";
import { Input } from "@/components/ui/input";
import { Modal } from "@/components/ui/modal";
import { LoadingOverlay, LoadingRows } from "@/components/ui/skeleton";
import { TablePagination, useClientPagination } from "@/components/ui/table-pagination";
import {
  createApiKey,
  createOpenApiApp,
  deleteApiKey,
  deleteOpenApiApp,
  getApiKeys,
  getKnowledgeBases,
  getOpenApiApps,
  rotateApiKey,
  updateApiKey,
  updateOpenApiApp,
} from "@/lib/api/client";
import type { ApiKeyRecord, KnowledgeBase, OpenApiAppRecord } from "@/lib/contracts/types";

const defaultCapabilities = ["rag.query", "rag.graph_query"];
const capabilityOptions = [
  { value: "rag.query", label: "RAG 查询" },
  { value: "rag.graph_query", label: "Graph RAG 查询" },
  { value: "kb.read", label: "知识库读取" },
  { value: "kb.usage.read", label: "用量读取" },
];

type FormState = {
  appId: string;
  name: string;
  kbIdsText: string;
  capabilities: string[];
  requireSignature: boolean;
  allowedIpsText: string;
  rpmLimit: string;
  dailyRequestLimit: string;
  note: string;
  expiresAt: string;
};

type AppFormState = {
  name: string;
  note: string;
};

const initialForm: FormState = {
  appId: "",
  name: "",
  kbIdsText: "",
  capabilities: defaultCapabilities,
  requireSignature: true,
  allowedIpsText: "",
  rpmLimit: "",
  dailyRequestLimit: "",
  note: "",
  expiresAt: "",
};

const initialAppForm: AppFormState = {
  name: "",
  note: "",
};

export default function ApiKeysPage() {
  const [items, setItems] = useState<ApiKeyRecord[]>([]);
  const [knowledgeBases, setKnowledgeBases] = useState<KnowledgeBase[]>([]);
  const [openApiApps, setOpenApiApps] = useState<OpenApiAppRecord[]>([]);
  const [form, setForm] = useState<FormState>(initialForm);
  const [appForm, setAppForm] = useState<AppFormState>(initialAppForm);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [oneTimeKey, setOneTimeKey] = useState<string | null>(null);
  const [createKeyOpen, setCreateKeyOpen] = useState(false);

  async function load() {
    setLoading(true);
    try {
      const [keys, kbs, apps] = await Promise.all([getApiKeys(), getKnowledgeBases(), getOpenApiApps()]);
      setItems(keys);
      setKnowledgeBases(kbs);
      setOpenApiApps(apps);
      setError(null);
    } catch (err) {
      setItems([]);
      setError(err instanceof Error ? err.message : "加载 API Key 失败");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    load();
  }, []);

  const stats = useMemo(() => {
    const active = items.filter((item) => item.status === "active").length;
    const disabled = items.filter((item) => item.status === "disabled").length;
    const expired = items.filter((item) => isExpired(item.expiresAt)).length;
    return { active, disabled, expired };
  }, [items]);
  const hasLoaded = !loading || items.length > 0 || Boolean(error);
  const itemsPagination = useClientPagination(items, 20);

  function toggleCapability(value: string) {
    setForm((prev) => {
      const exists = prev.capabilities.includes(value);
      return {
        ...prev,
        capabilities: exists
          ? prev.capabilities.filter((item) => item !== value)
          : [...prev.capabilities, value],
      };
    });
  }

  async function handleCreate() {
    const kbIds = parseList(form.kbIdsText);
    if (!form.name.trim()) {
      setError("请填写 API Key 名称");
      return;
    }
    if (kbIds.length === 0) {
      setError("请至少绑定一个知识库 ID");
      return;
    }
    if (form.capabilities.length === 0) {
      setError("请至少选择一个能力范围");
      return;
    }

    setSaving(true);
    try {
      const created = await createApiKey({
        appId: form.appId || null,
        name: form.name.trim(),
        kbIds,
        capabilities: form.capabilities,
        requireSignature: form.requireSignature,
        allowedIps: parseList(form.allowedIpsText),
        rpmLimit: parseLimit(form.rpmLimit),
        dailyRequestLimit: parseLimit(form.dailyRequestLimit),
        note: form.note.trim(),
        expiresAt: form.expiresAt ? new Date(form.expiresAt).toISOString() : null,
      });
      setOneTimeKey(created.plainKey ?? null);
      setForm(initialForm);
      setCreateKeyOpen(false);
      await load();
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "创建 API Key 失败");
    } finally {
      setSaving(false);
    }
  }

  async function handleCreateApp() {
    if (!appForm.name.trim()) {
      setError("请填写 app 名称");
      return;
    }
    setSaving(true);
    try {
      const created = await createOpenApiApp({
        name: appForm.name.trim(),
        note: appForm.note.trim(),
      });
      setOpenApiApps((prev) => [created, ...prev]);
      setForm((prev) => ({ ...prev, appId: created.id }));
      setAppForm(initialAppForm);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "创建 OpenAPI app 失败");
    } finally {
      setSaving(false);
    }
  }

  async function handleToggleApp(app: OpenApiAppRecord) {
    const nextStatus = app.status === "active" ? "disabled" : "active";
    try {
      const updated = await updateOpenApiApp(app.id, { status: nextStatus });
      setOpenApiApps((prev) => prev.map((entry) => (entry.id === app.id ? updated : entry)));
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "更新 OpenAPI app 状态失败");
    }
  }

  async function handleDeleteApp(app: OpenApiAppRecord) {
    try {
      await deleteOpenApiApp(app.id);
      setOpenApiApps((prev) => prev.filter((entry) => entry.id !== app.id));
      setForm((prev) => ({ ...prev, appId: prev.appId === app.id ? "" : prev.appId }));
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "删除 OpenAPI app 失败");
    }
  }

  async function handleToggle(item: ApiKeyRecord) {
    const nextStatus = item.status === "active" ? "disabled" : "active";
    try {
      const updated = await updateApiKey(item.id, { status: nextStatus });
      setItems((prev) => prev.map((entry) => (entry.id === item.id ? updated : entry)));
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "更新 API Key 状态失败");
    }
  }

  async function handleRotate(item: ApiKeyRecord) {
    try {
      const rotated = await rotateApiKey(item.id);
      setOneTimeKey(rotated.plainKey ?? null);
      setItems((prev) => prev.map((entry) => (entry.id === item.id ? rotated : entry)));
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "轮换 API Key 失败");
    }
  }

  async function handleDelete(item: ApiKeyRecord) {
    try {
      await deleteApiKey(item.id);
      setItems((prev) => prev.filter((entry) => entry.id !== item.id));
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "删除 API Key 失败");
    }
  }

  return (
    <div className="space-y-6">
      <ContextRail
        title="API Key 管理"
        description="管理知识库 OpenAPI 的本地技术凭证、绑定知识库和能力范围。当前切片支持基础 Bearer 鉴权；强签名、nonce 和 IP 白名单仍在后续安全加固范围。"
        showGlobalHint={false}
      />

      <section className="grid gap-4 md:grid-cols-4">
        <MetricCard label="总数" value={String(items.length)} helper="未包含软删除记录" />
        <MetricCard label="启用中" value={String(stats.active)} helper="可用于 OpenAPI 查询" />
        <MetricCard label="已禁用" value={String(stats.disabled)} helper="调用会被拒绝" />
        <MetricCard label="已过期" value={String(stats.expired)} helper="按 expiresAt 判断" />
      </section>

      {oneTimeKey && (
        <section className="relative overflow-hidden rounded-md border border-[#FF8A00]/28 bg-[radial-gradient(circle_at_88%_16%,rgba(255,138,0,0.18),transparent_36%),linear-gradient(135deg,#FFF5DD,#FFFFFF_58%)] p-5 shadow-panel">
          <div className="flex flex-col gap-3 lg:flex-row lg:items-start lg:justify-between">
            <div>
              <h2 className="text-base font-semibold text-ink-primary">一次性明文 Key</h2>
              <p className="mt-1 text-sm text-ink-secondary">
                这段明文只在创建或轮换后返回一次，刷新页面后不会再次显示。
              </p>
              <code className="mt-3 block break-all rounded-md border border-[#FF8A00]/20 bg-white px-3 py-2 font-mono text-xs text-ink-primary">
                {oneTimeKey}
              </code>
            </div>
            <Button size="sm" variant="ghost" onClick={() => setOneTimeKey(null)}>
              我已保存
            </Button>
          </div>
        </section>
      )}

      {error && (
        <div className="rounded-sm border border-status-danger bg-[#FEF2F2] px-4 py-3 text-sm text-status-danger">
          {error}
        </div>
      )}

      <section className="relative overflow-hidden rounded-lg border border-[#0EA5E9]/24 bg-white/86 shadow-panel">
        <div className="border-b border-[#BAE6FD]/80 bg-[linear-gradient(90deg,rgba(14,165,233,0.12),rgba(79,70,229,0.08),rgba(255,255,255,0.76))] px-5 py-4">
          <p className="text-[11px] font-semibold uppercase tracking-[0.1em] text-[#0369A1]">OpenAPI App</p>
          <h2 className="mt-1 text-base font-semibold text-ink-primary">调用方 app</h2>
        </div>
        <div className="grid gap-4 px-5 py-4 lg:grid-cols-[1fr_1fr_auto] lg:items-end">
          <Field label="app 名称">
            <Input
              inputSize="sm"
              placeholder="例如：AI 基座用户端"
              value={appForm.name}
              onChange={(event) => setAppForm((prev) => ({ ...prev, name: event.target.value }))}
            />
          </Field>
          <Field label="备注">
            <Input
              inputSize="sm"
              placeholder="责任系统、联系人或用途"
              value={appForm.note}
              onChange={(event) => setAppForm((prev) => ({ ...prev, note: event.target.value }))}
            />
          </Field>
          <Button size="sm" variant="secondary" onClick={handleCreateApp} loading={saving}>
            创建 app
          </Button>
        </div>
        {openApiApps.length > 0 && (
          <div className="flex flex-wrap gap-2 border-t border-[#BAE6FD]/80 px-5 py-4">
            {openApiApps.map((app) => (
              <div key={app.id} className="flex items-center gap-2 rounded-md border border-[#BAE6FD] bg-white px-2 py-1">
                <Badge variant={app.status === "active" ? "info" : "neutral"}>
                  {app.name} / {app.id}
                </Badge>
                <Button size="sm" variant="ghost" onClick={() => handleToggleApp(app)}>
                  {app.status === "active" ? "禁用" : "启用"}
                </Button>
                <Button size="sm" variant="danger-ghost" onClick={() => handleDeleteApp(app)}>
                  删除
                </Button>
              </div>
            ))}
          </div>
        )}
      </section>

      <section className="relative overflow-hidden rounded-lg border border-[#0EA5E9]/24 bg-white/86 shadow-panel">
        <LoadingOverlay active={loading && hasLoaded} tone="blue" label="正在刷新凭证" />
        <div className="border-b border-[#BAE6FD]/80 bg-[linear-gradient(90deg,rgba(14,165,233,0.12),rgba(79,70,229,0.08),rgba(255,255,255,0.76))] px-5 py-4">
          <div className="flex flex-col gap-3 lg:flex-row lg:items-start lg:justify-between">
            <div>
              <p className="text-[11px] font-semibold uppercase tracking-[0.1em] text-[#0369A1]">Governance</p>
              <h2 className="mt-1 text-base font-semibold text-ink-primary">API Key 列表</h2>
            </div>
            <Button size="sm" variant="primary" onClick={() => setCreateKeyOpen(true)}>
              创建 Key
            </Button>
          </div>
        </div>
        {loading && !hasLoaded ? (
          <LoadingRows rows={4} />
        ) : items.length === 0 ? (
          <EmptyState title="暂无 API Key" description="创建后即可用于 OpenAPI v1 的查询类接口基础鉴权。" />
        ) : (
          <div className="overflow-x-auto animate-data-enter">
            <table className="w-full min-w-[1120px] text-sm">
              <thead>
                <tr className="border-b border-[#BAE6FD]/80 bg-[linear-gradient(90deg,#ECF8FF,#EEF3FF)] text-[12px] font-medium uppercase tracking-[0.06em] text-[#0369A1]">
                  <th className="px-4 py-2.5 text-left">名称 / ID</th>
                  <th className="px-4 py-2.5 text-left">Key 指纹</th>
                  <th className="px-4 py-2.5 text-left">绑定知识库</th>
                  <th className="px-4 py-2.5 text-left">能力</th>
                  <th className="px-4 py-2.5 text-left">时间</th>
                  <th className="px-4 py-2.5 text-left">状态</th>
                  <th className="px-4 py-2.5 text-right">操作</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-[#BAE6FD]/70">
                {itemsPagination.pageItems.map((item) => (
                  <tr key={item.id} className="transition-colors hover:bg-[#ECF8FF]/70">
                    <td className="px-4 py-3 align-top">
                      <p className="font-medium text-ink-primary">{item.name}</p>
                      <p className="mt-1 font-mono text-xs text-ink-tertiary">{item.id}</p>
                      {item.note && <p className="mt-1 max-w-[220px] truncate text-xs text-ink-secondary">{item.note}</p>}
                    </td>
                    <td className="px-4 py-3 align-top">
                      <p className="font-mono text-xs text-ink-primary">{item.keyPrefix}...</p>
                      <p className="mt-1 font-mono text-xs text-ink-tertiary">后缀 {item.keySuffix}</p>
                    </td>
                    <td className="px-4 py-3 align-top">
                      <div className="flex max-w-[240px] flex-wrap gap-1.5">
                        {item.kbIds.map((kbId) => (
                          <Badge key={kbId} variant="neutral">{kbId}</Badge>
                        ))}
                      </div>
                    </td>
                    <td className="px-4 py-3 align-top">
                      <div className="flex max-w-[260px] flex-wrap gap-1.5">
                        {item.capabilities.map((capability) => (
                          <Badge key={capability} variant="info">{capability}</Badge>
                        ))}
                      </div>
                    </td>
                    <td className="px-4 py-3 align-top text-xs text-ink-secondary">
                      <p>创建：{formatDate(item.createdAt)}</p>
                      <p className="mt-1">过期：{formatDate(item.expiresAt) || "未设置"}</p>
                      <p className="mt-1">最近使用：{formatDate(item.lastUsedAt) || "暂无"}</p>
                    </td>
                    <td className="px-4 py-3 align-top">
                      <Badge variant={statusVariant(item)} dot>
                        {statusLabel(item)}
                      </Badge>
                    </td>
                    <td className="px-4 py-3 align-top">
                      <div className="flex justify-end gap-2">
                        <Button size="sm" variant="secondary" onClick={() => handleToggle(item)}>
                          {item.status === "active" ? "禁用" : "启用"}
                        </Button>
                        <Button size="sm" variant="secondary" onClick={() => handleRotate(item)}>
                          轮换
                        </Button>
                        <Button size="sm" variant="danger-ghost" onClick={() => handleDelete(item)}>
                          删除
                        </Button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            <TablePagination
              page={itemsPagination.page}
              pageSize={itemsPagination.pageSize}
              total={itemsPagination.total}
              pageCount={itemsPagination.pageCount}
              startIndex={itemsPagination.startIndex}
              endIndex={itemsPagination.endIndex}
              onPageChange={itemsPagination.setPage}
              onPageSizeChange={itemsPagination.setPageSize}
            />
          </div>
        )}
      </section>

      <Modal
        open={createKeyOpen}
        onClose={() => setCreateKeyOpen(false)}
        title="创建 API Key"
        size="xl"
        footer={
          <>
            <Button variant="ghost" onClick={() => setCreateKeyOpen(false)}>
              取消
            </Button>
            <Button variant="primary" onClick={handleCreate} loading={saving}>
              创建 Key
            </Button>
          </>
        }
      >
        <div className="space-y-5">
          <div className="grid gap-4 lg:grid-cols-2">
            <Field label="调用方 app">
              <select
                className="h-9 w-full rounded-md border border-line-subtle bg-white px-3 text-sm text-ink-primary outline-none transition-colors focus:border-[#0EA5E9] focus:ring-2 focus:ring-[#BAE6FD]"
                value={form.appId}
                onChange={(event) => setForm((prev) => ({ ...prev, appId: event.target.value }))}
              >
                <option value="">未绑定 app</option>
                {openApiApps.map((app) => (
                  <option key={app.id} value={app.id}>
                    {app.name} / {app.id}
                  </option>
                ))}
              </select>
            </Field>
            <Field label="名称">
              <Input
                inputSize="sm"
                placeholder="例如：教务系统问答"
                value={form.name}
                onChange={(event) => setForm((prev) => ({ ...prev, name: event.target.value }))}
              />
            </Field>
            <Field label="绑定知识库 ID">
              <Input
                inputSize="sm"
                placeholder="多个 ID 用逗号分隔"
                value={form.kbIdsText}
                onChange={(event) => setForm((prev) => ({ ...prev, kbIdsText: event.target.value }))}
              />
            </Field>
            <Field label="过期时间">
              <Input
                inputSize="sm"
                type="datetime-local"
                value={form.expiresAt}
                onChange={(event) => setForm((prev) => ({ ...prev, expiresAt: event.target.value }))}
              />
            </Field>
            <Field label="IP 白名单">
              <Input
                inputSize="sm"
                placeholder="用途、调用方或责任人"
                value={form.allowedIpsText}
                onChange={(event) => setForm((prev) => ({ ...prev, allowedIpsText: event.target.value }))}
              />
            </Field>
            <Field label="每分钟请求上限">
              <Input
                inputSize="sm"
                type="number"
                min="0"
                placeholder="0 表示不限制"
                value={form.rpmLimit}
                onChange={(event) => setForm((prev) => ({ ...prev, rpmLimit: event.target.value }))}
              />
            </Field>
            <Field label="每日请求配额">
              <Input
                inputSize="sm"
                type="number"
                min="0"
                placeholder="0 表示不限制"
                value={form.dailyRequestLimit}
                onChange={(event) => setForm((prev) => ({ ...prev, dailyRequestLimit: event.target.value }))}
              />
            </Field>
            <Field label="备注">
              <Input
                inputSize="sm"
                placeholder="用途、调用方或责任人"
                value={form.note}
                onChange={(event) => setForm((prev) => ({ ...prev, note: event.target.value }))}
              />
            </Field>
          </div>
          <div>
            <p className="mb-2 text-[12px] font-medium uppercase tracking-[0.08em] text-ink-tertiary">能力范围</p>
            <div className="flex flex-wrap gap-2">
              {capabilityOptions.map((item) => {
                const active = form.capabilities.includes(item.value);
                return (
                  <button
                    key={item.value}
                    type="button"
                    onClick={() => toggleCapability(item.value)}
                    className={[
                      "cursor-pointer rounded-md border px-3 py-1.5 text-xs font-medium transition-colors",
                      active
                        ? "border-[#0EA5E9]/35 bg-[#ECF8FF] text-[#0369A1]"
                        : "border-[#BAE6FD] bg-white text-ink-secondary hover:border-[#0EA5E9]/35 hover:bg-[#ECF8FF]",
                    ].join(" ")}
                  >
                    {item.label}
                  </button>
                );
              })}
            </div>
          </div>
          <p className="text-xs text-ink-tertiary">
            可绑定知识库：{knowledgeBases.length ? knowledgeBases.map((item) => item.id).join("、") : "暂无可选知识库"}
          </p>
        </div>
      </Modal>
    </div>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="block">
      <span className="mb-1.5 block text-[12px] font-medium uppercase tracking-[0.08em] text-ink-tertiary">
        {label}
      </span>
      {children}
    </label>
  );
}

function MetricCard({ label, value, helper }: { label: string; value: string; helper: string }) {
  return (
    <div className="relative overflow-hidden rounded-md border border-[#0EA5E9]/20 bg-gradient-to-br from-white to-[#ECF8FF] p-5 shadow-sm">
      <p className="text-[12px] font-medium uppercase tracking-[0.08em] text-[#0369A1]">{label}</p>
      <p className="mt-2 font-mono text-[34px] font-bold leading-none text-ink-primary">{value}</p>
      <p className="mt-2 text-xs text-ink-secondary">{helper}</p>
    </div>
  );
}

function parseList(value: string): string[] {
  return value
    .split(/[,，\s]+/)
    .map((item) => item.trim())
    .filter(Boolean);
}

function parseLimit(value: string): number {
  const parsed = Number.parseInt(value, 10);
  return Number.isFinite(parsed) && parsed > 0 ? parsed : 0;
}

function isExpired(value?: string | null): boolean {
  if (!value) return false;
  return new Date(value).getTime() <= Date.now();
}

function statusVariant(item: ApiKeyRecord) {
  if (item.status === "disabled") return "warning";
  if (isExpired(item.expiresAt)) return "danger";
  if (item.status === "active") return "success";
  return "neutral";
}

function statusLabel(item: ApiKeyRecord): string {
  if (isExpired(item.expiresAt)) return "已过期";
  if (item.status === "active") return "启用";
  if (item.status === "disabled") return "禁用";
  return item.status;
}

function formatDate(value?: string | null): string {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  return date.toLocaleString("zh-CN", { hour12: false });
}
