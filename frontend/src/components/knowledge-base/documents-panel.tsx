"use client";

import Image from "next/image";
import { useEffect, useState } from "react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { EmptyState } from "@/components/ui/empty-state";
import { Modal } from "@/components/ui/modal";
import { LoadingOverlay, LoadingRows, Skeleton } from "@/components/ui/skeleton";
import { TablePagination, useClientPagination } from "@/components/ui/table-pagination";
import { StatusBadge } from "@/components/ui/status-badge";
import { toast } from "@/components/ui/toast";
import { deleteDocument, downloadDocumentCsv, downloadDocumentSource, getDocumentDetail, getDocuments } from "@/lib/api/client";
import type { DocumentDetail, DocumentRecord } from "@/lib/contracts/types";
import { formatTimestamp } from "@/lib/formatters";
import { getChunkLayerLabel, getTaskStateLabel } from "@/lib/i18n/zh-cn";

type DocumentsPanelProps = {
  kbId: string;
  title?: string;
};

export function DocumentsPanel({ kbId, title = "文档列表" }: DocumentsPanelProps) {
  const [docs, setDocs] = useState<DocumentRecord[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [exportingId, setExportingId] = useState<string | null>(null);
  const [detail, setDetail] = useState<DocumentDetail | null>(null);
  const [detailOpen, setDetailOpen] = useState(false);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailError, setDetailError] = useState<string | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<DocumentRecord | null>(null);
  const [deleting, setDeleting] = useState(false);

  async function loadDocuments() {
    setLoading(true);
    try {
      const data = await getDocuments(kbId);
      setDocs(data);
      setError(null);
    } catch (err) {
      setDocs([]);
      setError(err instanceof Error ? err.message : "加载文档失败");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void loadDocuments();
  }, [kbId]);

  async function handleExport(doc: DocumentRecord) {
    setExportingId(doc.id);
    try {
      await downloadDocumentCsv(doc.id, doc.filename);
      toast("success", `${doc.filename} 的 CSV 已开始下载`);
    } catch (err) {
      toast("danger", err instanceof Error ? err.message : "导出失败，请检查后端服务");
    } finally {
      setExportingId(null);
    }
  }

  async function handleOpenDetail(doc: DocumentRecord) {
    setDetailOpen(true);
    setDetailLoading(true);
    setDetailError(null);
    try {
      const payload = await getDocumentDetail(doc.id);
      setDetail(payload);
    } catch (err) {
      setDetail(null);
      setDetailError(err instanceof Error ? err.message : "加载文档详情失败");
    } finally {
      setDetailLoading(false);
    }
  }

  async function handleConfirmDelete() {
    if (!deleteTarget) return;
    setDeleting(true);
    try {
      await deleteDocument(deleteTarget.id);
      toast("success", `已删除 ${deleteTarget.filename}`);
      if (detail?.document.id === deleteTarget.id) {
        setDetail(null);
        setDetailOpen(false);
      }
      setDeleteTarget(null);
      await loadDocuments();
    } catch (err) {
      toast("danger", err instanceof Error ? err.message : "删除文档失败");
    } finally {
      setDeleting(false);
    }
  }

  const hasLoaded = !loading || docs.length > 0 || Boolean(error);
  const docsPagination = useClientPagination(docs, 20);

  return (
    <>
      <section className="relative overflow-hidden rounded-lg border border-[#00A889]/24 bg-[radial-gradient(circle_at_88%_8%,rgba(0,168,137,0.14),transparent_34%),linear-gradient(135deg,rgba(0,168,137,0.08),rgba(255,255,255,0.92))] shadow-panel">
        <LoadingOverlay active={loading && hasLoaded} tone="teal" label="正在刷新文档" />
        <div className="flex items-center justify-between border-b border-[#A7F3D0]/80 bg-[linear-gradient(90deg,rgba(0,168,137,0.12),rgba(16,185,129,0.08),rgba(255,255,255,0.76))] px-5 py-4">
          <div>
            <p className="text-[11px] font-semibold uppercase tracking-[0.1em] text-[#047857]">知识资产</p>
            <h2 className="mt-1 text-base font-semibold text-ink-primary">{title}</h2>
          </div>
        </div>

        {error ? <div className="border-b border-border-subtle bg-[#FEF2F2] px-5 py-3 text-sm text-status-danger">{error}</div> : null}

        {loading && !hasLoaded ? (
          <LoadingRows rows={5} />
        ) : docs.length === 0 ? (
          <EmptyState icon={<FileIcon />} title="还没有文档" description="上传 PDF 文档后，这里会显示已入库文档及切片结果。" />
        ) : (
          <div className="animate-data-enter overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-[#A7F3D0]/80 bg-[#ECFDF5] text-[12px] font-medium uppercase tracking-[0.06em] text-[#047857]">
                <th className="px-4 py-2.5 text-left">文件名</th>
                <th className="px-4 py-2.5 text-left">切片策略</th>
                <th className="px-4 py-2.5 text-right">切片数</th>
                <th className="px-4 py-2.5 text-left">״̬</th>
                <th className="px-4 py-2.5 text-right">更新时间</th>
                <th className="px-4 py-2.5 text-right">操作</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-[#A7F3D0]/70 bg-white/70">
              {docsPagination.pageItems.map((doc) => (
                <tr key={doc.id} className="transition-colors hover:bg-[#ECFDF5]/60">
                  <td className="px-4 py-3">
                    <button
                      type="button"
                      className="text-left font-medium text-ink-primary underline-offset-4 hover:text-[#00A889] hover:underline"
                      onClick={() => handleOpenDetail(doc)}
                    >
                      {doc.filename}
                    </button>
                    <div className="mt-1">
                      <Badge variant={doc.sourceStorage === "oss" ? "info" : "neutral"}>{getSourceStorageLabel(doc.sourceStorage)}</Badge>
                    </div>
                  </td>
                  <td className="px-4 py-3 text-ink-secondary">
                    {doc.isHierarchical ? (
                      <div className="flex flex-wrap items-center gap-2">
                        <span>三层切片</span>
                        <Badge variant="info">已标注</Badge>
                      </div>
                    ) : (
                      doc.strategy ?? "--"
                    )}
                  </td>
                  <td className="px-4 py-3 text-right font-mono text-ink-primary">{doc.chunkCount}</td>
                  <td className="px-4 py-3">
                    <StatusBadge status={doc.status}>{getTaskStateLabel(doc.status)}</StatusBadge>
                  </td>
                  <td className="px-4 py-3 text-right font-mono text-xs text-ink-tertiary">{formatTimestamp(doc.updatedAt)}</td>
                  <td className="px-4 py-3 text-right">
                    <div className="flex justify-end gap-2">
                      <Button variant="ghost" size="sm" onClick={() => handleOpenDetail(doc)}>
                        详情
                      </Button>
                      <Button variant="ghost" size="sm" loading={exportingId === doc.id} disabled={doc.chunkCount <= 0} onClick={() => handleExport(doc)}>
                        导出 CSV
                      </Button>
                      <Button variant="ghost" size="sm" disabled={!doc.sourceAvailable} onClick={() => downloadDocumentSource(doc.id)}>
                        源文件
                      </Button>
                      <Button variant="danger-ghost" size="sm" onClick={() => setDeleteTarget(doc)}>
                        删除
                      </Button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          <TablePagination
            page={docsPagination.page}
            pageSize={docsPagination.pageSize}
            total={docsPagination.total}
            pageCount={docsPagination.pageCount}
            startIndex={docsPagination.startIndex}
            endIndex={docsPagination.endIndex}
            onPageChange={docsPagination.setPage}
            onPageSizeChange={docsPagination.setPageSize}
          />
          </div>
        )}
      </section>

      <Modal
        open={detailOpen}
        onClose={() => setDetailOpen(false)}
        title={detail?.document.filename ?? "文档详情"}
        size="lg"
        footer={
          <>
            {detail?.document ? (
              <Button variant="danger-ghost" onClick={() => setDeleteTarget(detail.document)}>
                删除文档
              </Button>
            ) : null}
            <Button variant="secondary" onClick={() => setDetailOpen(false)}>
              关闭
            </Button>
          </>
        }
      >
        {detailLoading ? (
          <div className="space-y-4 py-2">
            <div className="grid gap-3 md:grid-cols-3">
              {[1, 2, 3].map((item) => (
                <Skeleton key={item} className="h-20 rounded-lg" />
              ))}
            </div>
            <LoadingRows rows={4} />
          </div>
        ) : detailError ? (
          <div className="rounded-sm border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">{detailError}</div>
        ) : detail ? (
          <div className="space-y-4">
            <div className="grid gap-3 md:grid-cols-3">
              <MetaCard label="切片策略" value={detail.document.isHierarchical ? "三层切片" : detail.document.strategy ?? "--"} />
              <MetaCard label="切片数量" value={String(detail.document.chunkCount)} mono />
              <MetaCard label="更新时间" value={formatTimestamp(detail.document.updatedAt)} mono />
            </div>
            {detail.document.isHierarchical ? (
              <div className="rounded-lg border border-[#A7F3D0] bg-[#ECFDF5] px-4 py-3 text-sm text-ink-secondary">
                当前文档使用三层切片，层级标注：
                <span className="ml-2 font-medium text-ink-primary">
                  {(detail.document.hierarchicalLayers ?? [])
                    .map((layer) => getChunkLayerLabel(layer as "parent" | "child" | "enhanced"))
                    .join(" / ") || "父块 / 子块 / 增强块"}
                </span>
              </div>
            ) : null}
            <div className="max-h-[60vh] space-y-3 overflow-y-auto pr-1">
              {detail.chunks.map((chunk) => (
                <article key={chunk.id} className="rounded-lg border border-[#D1FAE5] bg-[radial-gradient(circle_at_92%_16%,rgba(0,168,137,0.12),transparent_34%),linear-gradient(135deg,#ECFDF5,#FFFFFF_62%)] p-4 shadow-sm transition-colors hover:border-[#6EE7B7]">
                  <div className="flex flex-wrap items-center gap-2">
                    <Badge variant="neutral">#{chunk.chunkIndex + 1}</Badge>
                    <Badge variant="info">{getChunkLayerLabel(chunk.layer)}</Badge>
                    {chunk.isImageChunk ? <Badge variant="info">图片切片</Badge> : null}
                    {chunk.isTableChunk ? <Badge variant="info">表格切片</Badge> : null}
                    <span className="font-mono text-xs text-ink-tertiary">P.{chunk.page}</span>
                  </div>
                  {chunk.title ? <p className="mt-2 text-sm font-medium text-ink-primary">{chunk.title}</p> : null}
                  <p className="mt-2 whitespace-pre-wrap text-sm text-ink-secondary">{chunk.content}</p>
                  {chunk.isImageChunk && chunk.imagePath ? (
                    <div className="mt-3 overflow-hidden rounded-lg border border-[#A7F3D0] bg-[#ECFDF5]">
                      <Image
                        src={toAssetUrl(chunk.imagePath)}
                        alt={chunk.title || `图片切片 ${chunk.chunkIndex + 1}`}
                        width={1200}
                        height={800}
                        className="h-auto w-full object-contain"
                        unoptimized
                      />
                    </div>
                  ) : null}
                </article>
              ))}
            </div>
          </div>
        ) : (
          <div className="py-6 text-sm text-ink-tertiary">暂无详情数据。</div>
        )}
      </Modal>

      <Modal
        open={Boolean(deleteTarget)}
        onClose={() => setDeleteTarget(null)}
        title="删除文档"
        size="sm"
        footer={
          <>
            <Button variant="secondary" onClick={() => setDeleteTarget(null)}>
              取消
            </Button>
            <Button variant="danger" loading={deleting} onClick={handleConfirmDelete}>
              确认删除
            </Button>
          </>
        }
      >
        <p className="text-sm text-ink-secondary">
          确定要删除文档 <span className="font-semibold text-ink-primary">{deleteTarget?.filename}</span> 吗？
        </p>
        <p className="mt-2 text-sm text-ink-tertiary">删除后会同时移除该文档的切片、关系和图谱来源数据，无法恢复。</p>
      </Modal>
    </>
  );
}

function MetaCard({ label, value, mono = false }: { label: string; value: string; mono?: boolean }) {
  return (
    <div className="rounded-lg border border-[#D1FAE5] bg-[#ECFDF5]/65 px-4 py-3">
      <p className="text-[11px] font-semibold uppercase tracking-[0.08em] text-[#047857]">{label}</p>
      <p className={`mt-2 text-sm text-ink-primary ${mono ? "font-mono" : ""}`}>{value}</p>
    </div>
  );
}

function toAssetUrl(imagePath: string): string {
  const normalized = imagePath.replace(/\\/g, "/");
  if (normalized.startsWith("http://") || normalized.startsWith("https://") || normalized.startsWith("data:image/")) {
    return normalized;
  }
  const marker = "/data/output/";
  const markerIndex = normalized.indexOf(marker);
  const relative = markerIndex >= 0 ? normalized.slice(markerIndex + marker.length) : normalized.split("/output/").pop() ?? normalized;
  return `${process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://127.0.0.1:8000"}/api/assets/output/${relative}`;
}

function getSourceStorageLabel(sourceStorage?: string): string {
  if (sourceStorage === "oss") {
    return "OSS 源文件";
  }
  if (sourceStorage === "local") {
    return "本地源文件";
  }
  return "源文件未记录";
}

function FileIcon() {
  return (
    <svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
      <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
      <polyline points="14 2 14 8 20 8" />
    </svg>
  );
}
