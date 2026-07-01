import { TablePagination, useClientPagination } from "@/components/ui/table-pagination";

type EvidenceRow = {
  id: string;
  source: string;
  page: number;
  layer: string;
  strategy: string;
  status: string;
  score: number | null;
  summary: string;
};

export function EvidenceTable({
  title,
  rows,
}: {
  title: string;
  rows: EvidenceRow[];
}) {
  const rowsPagination = useClientPagination(rows, 20);

  return (
    <div className="overflow-hidden rounded-lg border border-[#7C3AED]/24 bg-[radial-gradient(circle_at_88%_8%,rgba(124,58,237,0.14),transparent_34%),linear-gradient(135deg,rgba(124,58,237,0.08),rgba(255,255,255,0.92))] shadow-panel">
      <div className="flex items-center justify-between border-b border-[#7C3AED]/18 bg-[linear-gradient(90deg,rgba(124,58,237,0.12),rgba(236,72,153,0.08),rgba(255,255,255,0.78))] px-5 py-4">
        <div>
          <div className="text-[11px] font-semibold uppercase tracking-[0.18em] text-[#6D28D9]">证据表</div>
          <h3 className="mt-1 text-lg font-semibold">{title}</h3>
        </div>
        <div className="rounded-md border border-[#C4B5FD] bg-white/82 px-3 py-1 font-mono text-xs text-[#6D28D9] shadow-sm">
          {rows.length} 行
        </div>
      </div>
      <div className="overflow-x-auto">
        <table className="min-w-full text-left text-sm">
          <thead className="bg-[#F5F3FF] text-xs uppercase tracking-[0.16em] text-[#6D28D9]">
            <tr>
              <th className="px-5 py-3">ID</th>
              <th className="px-5 py-3">来源</th>
              <th className="px-5 py-3">页码</th>
              <th className="px-5 py-3">层级</th>
              <th className="px-5 py-3">策略</th>
              <th className="px-5 py-3">分数</th>
              <th className="px-5 py-3">ժҪ</th>
            </tr>
          </thead>
          <tbody>
            {rowsPagination.pageItems.map((row) => (
              <tr key={row.id} className="border-t border-[#DDD6FE]/80 bg-white/66 align-top transition-colors hover:bg-[#F5F3FF]/76">
                <td className="px-5 py-4 font-mono text-xs text-ink-primary">{row.id}</td>
                <td className="px-5 py-4 text-ink-secondary">{row.source}</td>
                <td className="px-5 py-4 font-mono text-xs text-ink-primary">{row.page}</td>
                <td className="px-5 py-4">
                  <span className="rounded-md bg-[#F4F0FF] px-2.5 py-1 text-xs font-medium text-[#6D28D9]">
                    {row.layer}
                  </span>
                </td>
                <td className="px-5 py-4 text-ink-secondary">{row.strategy}</td>
                <td className="px-5 py-4 font-mono text-xs text-ink-primary">
                  {row.score === null ? "无" : row.score.toFixed(2)}
                </td>
                <td className="px-5 py-4 text-sm leading-6 text-ink-secondary">{row.summary}</td>
              </tr>
            ))}
          </tbody>
        </table>
        <TablePagination
          page={rowsPagination.page}
          pageSize={rowsPagination.pageSize}
          total={rowsPagination.total}
          pageCount={rowsPagination.pageCount}
          startIndex={rowsPagination.startIndex}
          endIndex={rowsPagination.endIndex}
          onPageChange={rowsPagination.setPage}
          onPageSizeChange={rowsPagination.setPageSize}
          itemLabel="行"
        />
      </div>
    </div>
  );
}
