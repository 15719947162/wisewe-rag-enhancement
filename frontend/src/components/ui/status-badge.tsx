import type { TaskState } from "@/lib/contracts/types";

const tones: Record<TaskState, string> = {
  pending: "border-[#94A3B8]/30 bg-[#F1F5F9] text-[#566274]",
  running: "border-[#0EA5E9]/25 bg-[#ECF8FF] text-[#0369A1]",
  awaiting_confirmation: "border-[#7C3AED]/25 bg-[#F4F0FF] text-[#6D28D9]",
  success: "border-[#10B981]/25 bg-[#E9FBF5] text-[#007F69]",
  degraded: "border-[#FF8A00]/25 bg-[#FFF5DD] text-[#B85F00]",
  failed: "border-[#E11D48]/25 bg-[#FFF0F4] text-[#BE123C]",
  empty: "border-[#DDE3F0] bg-[#FAFBFF] text-[#8A95A8]",
};

const labels: Record<TaskState, string> = {
  pending: "待处理",
  running: "进行中",
  awaiting_confirmation: "待确认",
  success: "成功",
  degraded: "降级",
  failed: "失败",
  empty: "空",
};

export function StatusBadge({
  status,
  children,
}: {
  status: TaskState;
  children?: React.ReactNode;
}) {
  return (
    <span
      className={[
        "inline-flex items-center gap-1.5 rounded-full border px-3 py-1 text-[11px] font-semibold",
        tones[status],
      ].join(" ")}
    >
      <span
        className={[
          "status-dot",
          status === "pending" && "bg-status-pending",
          status === "running" && "bg-[#0EA5E9] animate-pulse-line",
          status === "awaiting_confirmation" && "bg-[#7C3AED] animate-pulse-line",
          status === "success" && "bg-status-success",
          status === "degraded" && "bg-[#FF8A00]",
          status === "failed" && "bg-status-danger",
          status === "empty" && "bg-[#94A3B8]",
        ].join(" ")}
      />
      {children ?? labels[status]}
    </span>
  );
}
