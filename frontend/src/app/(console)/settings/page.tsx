"use client";

import { useEffect, useMemo, useState } from "react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { LoadingOverlay, Skeleton } from "@/components/ui/skeleton";
import { getSettingsGroups, updateSettings } from "@/lib/api/client";
import { toast } from "@/components/ui/toast";
import type { SettingsGroup } from "@/lib/contracts/types";

type SettingEntry = SettingsGroup["values"][number];

const SOURCE_META: Record<NonNullable<SettingEntry["source"]>, { label: string; className: string }> = {
  db: {
    label: "控制台覆盖",
    className: "border-[#365DFF]/18 bg-[#EEF3FF]/72 text-[#2447DB]",
  },
  env: {
    label: "环境变量",
    className: "border-[#CBD5E1] bg-white text-ink-secondary",
  },
  config: {
    label: "配置文件",
    className: "border-[#CBD5E1] bg-white text-ink-secondary",
  },
  code: {
    label: "内置默认",
    className: "border-[#E5E7EB] bg-[#F8FAFC] text-ink-tertiary",
  },
  system: {
    label: "ϵͳ״̬",
    className: "border-[#E5E7EB] bg-[#F8FAFC] text-ink-tertiary",
  },
};

const SETTING_COPY: Record<string, { title: string; description?: string }> = {
  LLM_BASE_URL: { title: "通用模型服务地址", description: "未单独指定模型通道时使用的 OpenAI 兼容接口地址。" },
  LLM_API_KEY: { title: "通用模型密钥", description: "清洗、切片、增强等通用模型调用的默认密钥。" },
  LLM_EMBEDDING_MODEL: { title: "向量模型名称", description: "用于切片向量化和检索查询向量化。" },
  LLM_EMBEDDING_BATCH_SIZE: { title: "向量批量大小", description: "单次 embedding 请求包含的文本条数。" },
  LLM_EMBEDDING_MAX_CONCURRENCY: { title: "向量并发数", description: "embedding 阶段允许同时发起的请求数量。" },
  LLM_EMBEDDING_MAX_RETRIES: { title: "向量重试次数", description: "embedding 请求失败后的最大重试次数。" },
  LLM_EMBEDDING_API_KEY_POOL: { title: "向量密钥池", description: "多个 embedding 密钥的轮换池，用于分摊并发压力。" },
  LLM_EMBEDDING_KEY_RETRIES: { title: "向量密钥切换重试", description: "单个密钥触发限流后允许切换密钥重试的次数。" },
  LLM_EMBEDDING_KEY_COOLDOWN_SECONDS: { title: "向量密钥冷却时间", description: "密钥触发限流后暂停使用的秒数。" },
  RAG_RETRIEVAL_SNAPSHOT: { title: "召回快照优化", description: "在线问答是否启用候选快照查询以减少重复检索。" },
  LLM_CLEANER_MODEL: { title: "清洗模型名称", description: "用于解析后文本清洗和噪声过滤。" },
  LLM_CLEANER_BASE_URL: { title: "清洗模型服务地址" },
  LLM_CLEANER_API_KEY: { title: "清洗模型密钥" },
  LLM_CLEANER_SYSTEM_PROMPT: { title: "清洗系统提示词", description: "控制教材内容保留、噪声过滤和格式修复策略。" },
  LLM_CHUNKER_SYSTEM_PROMPT: { title: "切片系统提示词", description: "控制 LLM 切片边界和知识点完整性。" },
  LLM_QUALITY_GATE_SYSTEM_PROMPT: { title: "质量审核提示词", description: "控制切片质量门控与有效证据保留。" },
  LLM_ENHANCE_SYSTEM_PROMPT: { title: "增强系统提示词", description: "控制摘要、图表描述和结构化抽取。" },
  RAG_LLM_MODEL: { title: "问答模型名称", description: "在线 RAG 生成答案使用的模型。" },
  RAG_LLM_BASE_URL: { title: "问答模型服务地址" },
  RAG_LLM_API_KEY: { title: "问答模型密钥" },
  RAG_SYSTEM_PROMPT: { title: "问答系统提示词", description: "控制引用格式、拒答策略和基于上下文回答的约束。" },
  PDF_PARSER_PROVIDER: { title: "解析服务提供方", description: "选择 MinerU、官方 MinerU 或阿里 Document Mind 等解析通道。" },
  PDF_PARSER_FALLBACKS: { title: "解析失败回退通道" },
  "parser.cloud.parse_method": { title: "云解析方式" },
  "parser.cloud.version": { title: "云解析版本" },
  "parser.cloud.timeout": { title: "云解析超时时间" },
  "parser.cloud.poll_interval": { title: "云解析轮询间隔" },
  "parser.cloud.enable_formula": { title: "公式解析" },
  "parser.cloud.enable_table_html": { title: "表格 HTML 输出" },
  "parser.cloud.language": { title: "解析语言" },
  "parser.cloud.is_ocr": { title: "强制 OCR" },
  "parser.cloud.model_version": { title: "解析模型版本" },
  "302AI_API_BASE": { title: "302AI 服务地址" },
  "302AI_API_KEY": { title: "302AI API Key" },
  MINERU_OFFICIAL_API_BASE: { title: "MinerU 官方服务地址" },
  MINERU_OFFICIAL_API_TOKEN: { title: "MinerU 官方 Token" },
  MINERU_OFFICIAL_MODEL_VERSION: { title: "MinerU 官方模型版本" },
  ALIYUN_DOCUMENT_MIND_ENDPOINT: { title: "Document Mind 服务地址" },
  ALIYUN_DOCUMENT_MIND_OUTPUT_FORMAT: { title: "Document Mind 输出格式" },
  ALIYUN_DOCUMENT_MIND_ACCESS_KEY_ID: { title: "Document Mind AccessKey ID" },
  ALIYUN_DOCUMENT_MIND_ACCESS_KEY_SECRET: { title: "Document Mind AccessKey Secret" },
  ALIYUN_DOCUMENT_MIND_CREDENTIAL_POOL: { title: "Document Mind 凭证池" },
  DEFAULT_INGESTION_STRATEGY: { title: "默认入库策略" },
  INGESTION_READY_MODE: { title: "入库可用模式", description: "控制完整增强入库或基础可检索优先。" },
  HIERARCHICAL_ENHANCE_MODE: { title: "三层增强模式" },
  HIERARCHICAL_TEXT_ENHANCE_WORKERS: { title: "文本增强并发" },
  HIERARCHICAL_TABLE_ENHANCE_WORKERS: { title: "表格增强并发" },
  HIERARCHICAL_IMAGE_ENHANCE_WORKERS: { title: "图片增强并发" },
  HIERARCHICAL_ENHANCE_MAX_CONCURRENCY: { title: "增强全局并发上限" },
  HIERARCHICAL_REUSE_LLM_CLIENTS: { title: "复用模型客户端" },
  LLM_API_KEY_POOL: { title: "文本模型密钥池" },
  VL_API_KEY_POOL: { title: "视觉模型密钥池" },
  DATABASE_URL: { title: "数据库连接串" },
  PGVECTOR_HOST: { title: "数据库主机" },
  PGVECTOR_PORT: { title: "数据库端口" },
  PGVECTOR_DB: { title: "数据库名称" },
  PGVECTOR_USER: { title: "数据库用户" },
  PGVECTOR_PASSWORD: { title: "数据库密码" },
  PGVECTOR_ENABLED: { title: "启用向量数据库" },
  PGVECTOR_DEFAULT_KB_ID: { title: "默认知识库 ID" },
  DB_AVAILABLE: { title: "数据库可用状态" },
  OSS_ACCESS_KEY_ID: { title: "OSS AccessKey ID" },
  OSS_ACCESS_KEY_SECRET: { title: "OSS AccessKey Secret" },
  OSS_ENDPOINT: { title: "OSS Endpoint" },
  OSS_BUCKET: { title: "OSS Bucket" },
  OUTPUT_DIR: { title: "本地输出目录" },
  OUTPUT_ENCODING: { title: "输出文件编码" },
  PROJECT_NAME: { title: "项目名称" },
  SETTINGS_GROUP_COUNT: { title: "配置分组数量" },
  CONFIG_FILE: { title: "配置文件" },
  SETTINGS_PRIORITY: { title: "配置优先级" },
  PLANNING_WORKFLOW: { title: "规划工作流" },
  TECH_STACK: { title: "技术栈" },
};

function isSensitiveLabel(label: string): boolean {
  const normalized = label.toLowerCase();
  return (
    normalized.includes("api_key") ||
    normalized.includes("access_key") ||
    normalized.includes("secret") ||
    normalized.includes("password") ||
    normalized.includes("token")
  );
}

function maskValue(entry: SettingEntry): string {
  const value = entry.value;
  if (!value || entry.hasValue === false) return "未配置";
  const label = entry.label;
  if (!isSensitiveLabel(label)) return value;
  if (value.length <= 4) return "****";
  return `****${value.slice(-4)}`;
}

function prettifySettingKey(label: string): string {
  const last = label.split(".").pop() ?? label;
  return last
    .split("_")
    .filter(Boolean)
    .map((part) => {
      const lower = part.toLowerCase();
      const dictionary: Record<string, string> = {
        api: "API",
        key: "密钥",
        token: "Token",
        base: "服务地址",
        url: "URL",
        model: "模型",
        prompt: "提示词",
        timeout: "超时时间",
        retries: "重试次数",
        retry: "重试",
        concurrency: "并发数",
        interval: "间隔",
        enabled: "启用状态",
        password: "密码",
        user: "用户",
        host: "主机",
        port: "端口",
        db: "数据库",
        bucket: "Bucket",
        endpoint: "Endpoint",
        output: "输出",
        format: "格式",
        language: "语言",
        version: "版本",
        provider: "提供方",
        sharding: "分片",
        cache: "缓存",
        ocr: "OCR",
      };
      return dictionary[lower] ?? part;
    })
    .join(" ");
}

function getSettingCopy(label: string): { title: string; description?: string } {
  return SETTING_COPY[label] ?? { title: prettifySettingKey(label) };
}

function getSourceMeta(source?: SettingEntry["source"]) {
  return SOURCE_META[source ?? "env"] ?? SOURCE_META.env;
}

function getCategoryLabel(category: SettingEntry["category"]): string {
  return category === "common" ? "常用" : "高级";
}

function isTruthyValue(value: string): boolean {
  return ["true", "1", "yes", "on"].includes(value.toLowerCase());
}

function getValueTone(entry: SettingEntry): string {
  if (entry.sensitive && entry.hasValue) {
    return "border-[#CBD5E1] bg-[#F8FAFC] text-ink-secondary";
  }
  if (!entry.value || entry.hasValue === false) {
    return "border-[#E5E7EB] bg-[#F9FAFB] text-ink-tertiary";
  }
  if (["true", "false", "1", "0", "yes", "no", "on", "off"].includes(entry.value.toLowerCase())) {
    return isTruthyValue(entry.value)
      ? "border-[#A7F3D0] bg-[#ECFDF5]/72 text-[#047857]"
      : "border-[#E5E7EB] bg-[#F8FAFC] text-ink-tertiary";
  }
  return "border-[#E5E7EB] bg-white text-ink-secondary";
}

export default function SettingsPage() {
  const [groups, setGroups] = useState<SettingsGroup[]>([]);
  const [edits, setEdits] = useState<Record<string, string>>({});
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState<string | null>(null);
  const [activeNav, setActiveNav] = useState<string>("models_common");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    getSettingsGroups()
      .then((data) => {
        setGroups(data);
        setError(null);
        if (data.length > 0 && !data.some((group) => group.id === activeNav)) {
          setActiveNav(data[0].id);
        }
      })
      .catch((err) => {
        setGroups([]);
        setError(err instanceof Error ? err.message : "加载配置失败");
      })
      .finally(() => {
        setLoading(false);
      });
  }, []);

  const activeGroup = useMemo(() => {
    return groups.find((group) => group.id === activeNav) ?? groups[0] ?? null;
  }, [activeNav, groups]);
  const hasLoaded = !loading || groups.length > 0 || Boolean(error);

  function handleEdit(groupId: string, label: string, value: string) {
    setEdits((prev) => ({ ...prev, [`${groupId}:${label}`]: value }));
  }

  function getEditValue(groupId: string, label: string, original: string) {
    return edits[`${groupId}:${label}`] ?? original;
  }

  async function handleSave(group: SettingsGroup) {
    setSaving(group.id);
    try {
      const payload: Record<string, string> = {};
      for (const entry of group.values) {
        const key = `${group.id}:${entry.label}`;
        if (entry.editable && edits[key] !== undefined) {
          payload[entry.label] = edits[key];
        }
      }

      if (Object.keys(payload).length === 0) {
        toast("info", "当前分组没有可保存的修改");
        return;
      }

      const result = await updateSettings(payload);

      setGroups((prev) =>
        prev.map((currentGroup) =>
          currentGroup.id !== group.id
            ? currentGroup
            : {
                ...currentGroup,
                values: currentGroup.values.map((entry) =>
                  payload[entry.label] === undefined
                    ? entry
                    : {
                        ...entry,
                        value: payload[entry.label],
                        source: result.updated.includes(entry.label) ? "db" : entry.source,
                      }
                ),
              }
        )
      );

      toast("success", `${group.title} 已保存`);
      setEdits((prev) => {
        const next = { ...prev };
        for (const entry of group.values) {
          delete next[`${group.id}:${entry.label}`];
        }
        return next;
      });
    } catch {
      toast("danger", "保存配置失败，请稍后重试");
    } finally {
      setSaving(null);
    }
  }

  function handleCancel(group: SettingsGroup) {
    setEdits((prev) => {
      const next = { ...prev };
      for (const entry of group.values) {
        delete next[`${group.id}:${entry.label}`];
      }
      return next;
    });
  }

  return (
    <div className="space-y-6">
      <section className="rounded-lg border border-border-subtle bg-white/90 px-6 py-5 shadow-sm">
        <div className="flex flex-col gap-3 lg:flex-row lg:items-end lg:justify-between">
          <div>
            <p className="text-[11px] font-semibold uppercase tracking-[0.14em] text-ink-tertiary">Configuration</p>
            <h1 className="mt-2 text-[28px] font-semibold leading-tight text-ink-primary">配置中心</h1>
            <p className="mt-2 max-w-3xl text-sm leading-6 text-ink-secondary">
              管理模型、解析、切片、数据库与存储参数。控制台修改会写入数据库覆盖层，优先级高于环境变量与配置文件。
            </p>
          </div>
          <div className="rounded-md border border-[#CBD5E1] bg-[#F8FAFC] px-3 py-2 text-xs leading-5 text-ink-secondary">
            <span className="font-medium text-ink-primary">优先级：</span>控制台覆盖 / 环境变量 / 配置文件 / 内置默认
          </div>
        </div>
      </section>

      {error && (
        <div className="rounded-sm border border-status-danger bg-[#FEF2F2] px-4 py-3 text-sm text-status-danger">
          {error}
        </div>
      )}

      <section className="flex flex-col gap-4 xl:flex-row xl:gap-6">
        <nav className="shrink-0 xl:w-[220px]">
          <div className="flex gap-1 overflow-x-auto rounded-lg border border-border-subtle bg-white/82 p-1.5 shadow-sm xl:block xl:overflow-hidden xl:p-1.5">
            {groups.map((item) => (
              <button
                key={item.id}
                onClick={() => setActiveNav(item.id)}
                className={[
                  "flex shrink-0 items-center justify-between gap-3 rounded-md px-3 py-2.5 text-left text-[13px] font-medium transition-colors xl:w-full",
                  item.id === activeNav
                    ? "bg-[#EEF3FF] text-[#2447DB] shadow-sm"
                    : "text-ink-secondary hover:bg-[#F8FAFC] hover:text-ink-primary",
                ].join(" ")}
              >
                <span>{item.title}</span>
                <span className="rounded-full bg-white/80 px-1.5 py-0.5 font-mono text-[10px] text-ink-tertiary">
                  {item.values.length}
                </span>
              </button>
            ))}
          </div>
        </nav>

        <div className="min-w-0 flex-1">
          <div className="relative">
          <LoadingOverlay active={loading && hasLoaded} tone="blue" label="正在加载配置" />
          {loading && !hasLoaded ? (
            <Skeleton className="h-[420px] rounded-md" />
          ) : !activeGroup ? (
            <div className="rounded-lg border border-border-subtle bg-white px-5 py-10 text-sm text-ink-secondary">
              未找到可展示的配置分组。
            </div>
          ) : (
            <SettingsGroupCard
              group={activeGroup}
              edits={edits}
              saving={saving === activeGroup.id}
              onEdit={handleEdit}
              onSave={handleSave}
              onCancel={handleCancel}
              getEditValue={getEditValue}
            />
          )}
          </div>
        </div>
      </section>
    </div>
  );
}

function SettingsGroupCard({
  group,
  edits,
  saving,
  onEdit,
  onSave,
  onCancel,
  getEditValue,
}: {
  group: SettingsGroup;
  edits: Record<string, string>;
  saving: boolean;
  onEdit: (groupId: string, label: string, value: string) => void;
  onSave: (group: SettingsGroup) => Promise<void>;
  onCancel: (group: SettingsGroup) => void;
  getEditValue: (groupId: string, label: string, original: string) => string;
}) {
  const hasEditableEntries = group.values.some((entry) => entry.editable);
  const isDirty = group.values.some((entry) => entry.editable && edits[`${group.id}:${entry.label}`] !== undefined);

  return (
    <div className="overflow-hidden rounded-lg border border-border-subtle bg-white shadow-sm">
      <div className="border-b border-border-subtle bg-[#FAFBFF] px-5 py-4">
        <div className="flex flex-col gap-3 lg:flex-row lg:items-start lg:justify-between">
          <div>
            <h2 className="text-lg font-semibold text-ink-primary">{group.title}</h2>
            <p className="mt-1 max-w-3xl text-sm leading-6 text-ink-secondary">{group.description}</p>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <Badge variant={hasEditableEntries ? "info" : "neutral"}>
              {hasEditableEntries ? "可编辑" : "只读"}
            </Badge>
            {isDirty && <Badge variant="warning">有未保存修改</Badge>}
            <span className="rounded-full border border-[#E5E7EB] bg-white px-2 py-0.5 text-xs text-ink-tertiary">
              {group.values.length} 项
            </span>
          </div>
        </div>
      </div>

      <div className="divide-y divide-border-subtle bg-white">
        {group.values.length === 0 ? (
          <div className="px-5 py-10 text-sm text-ink-secondary">当前分组暂无配置项。</div>
        ) : (
          group.values.map((entry) => {
            const editKey = `${group.id}:${entry.label}`;
            const isEditing = entry.editable && edits[editKey] !== undefined;
            const currentVal = getEditValue(group.id, entry.label, entry.value);
            const useTextarea = entry.label.endsWith("_SYSTEM_PROMPT") || currentVal.length > 120;
            const copy = getSettingCopy(entry.label);
            const sourceMeta = getSourceMeta(entry.source);
            const isModified = entry.editable && edits[editKey] !== undefined;

            return (
              <div
                key={editKey}
                className={[
                  "grid gap-4 px-5 py-4 transition-colors lg:grid-cols-[minmax(240px,0.42fr)_minmax(0,1fr)]",
                  isEditing ? "bg-[#EEF3FF]/34" : "hover:bg-[#FAFBFF]",
                ].join(" ")}
              >
                <div className="min-w-0">
                  <div className="flex flex-wrap items-center gap-2">
                    <p className="text-[14px] font-semibold text-ink-primary">{copy.title}</p>
                    {isModified && <span className="h-1.5 w-1.5 rounded-full bg-[#FF8A00]" />}
                  </div>
                  {copy.description && (
                    <p className="mt-1 text-xs leading-5 text-ink-tertiary">{copy.description}</p>
                  )}
                  <div className="mt-2 flex flex-wrap items-center gap-2">
                    <span className="rounded-md border border-[#E5E7EB] bg-[#F8FAFC] px-2 py-0.5 font-mono text-[11px] text-ink-tertiary">
                      {entry.label}
                    </span>
                    <span
                      className={[
                        "rounded-full border px-2 py-0.5 text-[11px] font-medium",
                        sourceMeta.className,
                      ].join(" ")}
                    >
                      {sourceMeta.label}
                    </span>
                    <span className="rounded-full border border-[#E5E7EB] bg-white px-2 py-0.5 text-[11px] text-ink-tertiary">
                      {getCategoryLabel(entry.category)}
                    </span>
                    {!entry.editable && (
                      <span className="rounded-full border border-[#E5E7EB] bg-[#F8FAFC] px-2 py-0.5 text-[11px] text-ink-tertiary">
                        只读
                      </span>
                    )}
                  </div>
                </div>

                <div className="min-w-0 flex-1">
                  {isEditing ? (
                    useTextarea ? (
                      <textarea
                        value={currentVal}
                        onChange={(e) => onEdit(group.id, entry.label, e.target.value)}
                        rows={entry.label.endsWith("_SYSTEM_PROMPT") ? 8 : Math.min(8, Math.max(3, Math.ceil(currentVal.length / 48)))}
                        className="w-full rounded-md border border-[#365DFF]/30 bg-white px-3 py-2 text-[13px] leading-6 text-ink-primary shadow-sm transition-colors duration-150 focus:border-[#365DFF] focus:outline-none focus:ring-2 focus:ring-[#365DFF]/14"
                      />
                    ) : (
                      <Input
                        inputSize="sm"
                        value={currentVal}
                        onChange={(e) => onEdit(group.id, entry.label, e.target.value)}
                        className="w-full xl:max-w-[480px]"
                      />
                    )
                  ) : (
                    <button
                      type="button"
                      disabled={!entry.editable}
                      onClick={() => entry.editable && onEdit(group.id, entry.label, entry.value)}
                      className={[
                        "block w-full rounded-md border px-3 py-2 text-left text-sm transition-colors",
                        entry.editable
                          ? "cursor-pointer hover:border-[#365DFF]/30 hover:bg-[#EEF3FF]/32 hover:text-ink-primary"
                          : "cursor-default",
                        getValueTone(entry),
                      ].join(" ")}
                      title={entry.editable ? "点击编辑" : "当前字段只读"}
                    >
                      <span className="block whitespace-pre-wrap break-all font-mono text-[13px] leading-5">
                        {maskValue(entry)}
                      </span>
                    </button>
                  )}
                </div>
              </div>
            );
          })
        )}
      </div>

      <div className="flex justify-end gap-2 border-t border-border-subtle bg-[#FAFBFF] px-5 py-3">
        <Button variant="secondary" size="sm" disabled={!isDirty} onClick={() => onCancel(group)}>
          取消
        </Button>
        <Button variant="primary" size="sm" loading={saving} disabled={!isDirty} onClick={() => onSave(group)}>
          保存配置
        </Button>
      </div>
    </div>
  );
}
