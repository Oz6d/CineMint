import { useEffect, useState } from "react";
import {
  Ban,
  CheckCircle2,
  ChevronRight,
  Clock,
  FolderSearch,
  ListChecks,
  TextCursorInput,
  RefreshCw,
  RotateCcw,
  Sparkles,
  Wand2,
} from "lucide-react";
import clsx from "clsx";
import type { Task } from "../types";
import { useAsync } from "../lib/hooks";
import { api } from "../lib/api";
import { useToast } from "../lib/toast";
import { Badge, Button, Card, ConfirmDialog, Drawer, EmptyState, Progress, Spinner, StatusBadge } from "../components/ui";
import { formatTime, taskTypeLabel } from "../lib/format";

type Filter = "all" | "active" | "done";

const FILTERS: { key: Filter; label: string }[] = [
  { key: "all", label: "全部" },
  { key: "active", label: "进行中" },
  { key: "done", label: "已结束" },
];

const LEVEL_TONES: Record<string, string> = {
  info: "text-slate-400",
  success: "text-mint-300",
  warn: "text-amber-300",
  warning: "text-amber-300",
  error: "text-rose-300",
};

function TaskIcon({ type, className }: { type: string; className?: string }) {
  if (type === "scan") return <FolderSearch size={16} className={className} />;
  if (type === "rename") return <TextCursorInput size={16} className={className} />;
  return <Wand2 size={16} className={className} />;
}

function statsSummary(task: Task): string {
  if (task.type === "scan") {
    const summary = `发现 ${task.stats.found ?? task.total} · 匹配 ${task.stats.matched ?? 0} · 待确认 ${task.stats.review ?? 0} · 未识别 ${task.stats.unmatched ?? 0}`;
    if (task.status === "running" && task.stats.visited != null) {
      return `${summary} · 已检查 ${task.stats.visited} 个条目`;
    }
    return summary;
  }
  if (task.type === "rename") {
    return `成功 ${task.stats.success ?? task.done} · 文件 ${task.stats.files ?? 0} · 文件夹 ${task.stats.folders ?? 0} · 失败 ${task.stats.failed ?? 0}`;
  }
  return `成功 ${task.stats.success ?? task.done} · 失败 ${task.stats.failed ?? 0} · 跳过 ${task.stats.skipped ?? 0}`;
}

export default function TasksPage() {
  const { data: tasks, loading, reload } = useAsync<Task[]>("/api/tasks?limit=100", [], { pollMs: 3000 });
  const [filter, setFilter] = useState<Filter>("all");
  const [openTaskId, setOpenTaskId] = useState<number | null>(null);
  const [detail, setDetail] = useState<Task | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [cancelTarget, setCancelTarget] = useState<Task | null>(null);
  const [acting, setActing] = useState(false);
  const toast = useToast();

  const list = (tasks || []).filter((task) => {
    if (filter === "active") return task.status === "running" || task.status === "queued";
    if (filter === "done") return task.status !== "running" && task.status !== "queued";
    return true;
  });

  useEffect(() => {
    if (!openTaskId) {
      setDetail(null);
      return;
    }
    let mounted = true;
    const load = async () => {
      try {
        const result = await api.get<Task>(`/api/tasks/${openTaskId}`);
        if (mounted) setDetail(result);
      } catch (err) {
        if (mounted) toast.error(err instanceof Error ? err.message : "读取任务失败");
      } finally {
        if (mounted) setDetailLoading(false);
      }
    };
    setDetailLoading(true);
    void load();
    const timer = window.setInterval(() => {
      if (detail?.status === "running" || detail?.status === "queued" || !detail) void load();
    }, 2000);
    return () => {
      mounted = false;
      window.clearInterval(timer);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [openTaskId]);

  const cancel = async () => {
    if (!cancelTarget) return;
    setActing(true);
    try {
      await api.post(`/api/tasks/${cancelTarget.id}/cancel`);
      toast.success("已请求取消任务，当前文件处理完成后停止");
      setCancelTarget(null);
      reload();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "取消失败");
    } finally {
      setActing(false);
    }
  };

  const retry = async (task: Task) => {
    try {
      const result = await api.post<{ id: number }>(`/api/tasks/${task.id}/retry`);
      toast.success("任务已重新提交");
      reload();
      setOpenTaskId(result.id);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "重试失败");
    }
  };

  return (
    <div className="mx-auto max-w-7xl space-y-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-lg font-semibold text-white">任务中心</h1>
          <p className="mt-1 text-xs text-slate-500">扫描、整理与仅重命名任务会在这里排队执行，可查看进度与完整日志。</p>
        </div>
        <Button onClick={() => reload()}>
          <RefreshCw size={15} /> 刷新
        </Button>
      </div>

      <div className="flex gap-1 rounded-xl border border-white/[0.07] bg-white/[0.02] p-1 w-fit">
        {FILTERS.map((entry) => (
          <button
            key={entry.key}
            onClick={() => setFilter(entry.key)}
            className={clsx(
              "rounded-lg px-3.5 py-1.5 text-xs transition",
              filter === entry.key
                ? "bg-gradient-to-r from-mint-500/20 to-sky-500/15 text-mint-300"
                : "text-slate-400 hover:text-slate-200",
            )}
          >
            {entry.label}
          </button>
        ))}
      </div>

      {loading && !tasks ? (
        <div className="flex h-48 items-center justify-center gap-2 text-sm text-slate-400">
          <Spinner /> 加载任务…
        </div>
      ) : list.length === 0 ? (
        <EmptyState
          icon={<ListChecks size={26} />}
          title={filter === "active" ? "当前没有进行中的任务" : "还没有任务记录"}
          description="在影视库详情页执行扫描、整理或仅重命名后，任务会出现在这里。"
        />
      ) : (
        <div className="space-y-2.5">
          {list.map((task) => {
            const active = task.status === "running" || task.status === "queued";
            return (
              <Card key={task.id} className="p-4">
                <div className="flex flex-wrap items-center gap-3">
                  <div
                    className={clsx(
                      "flex h-10 w-10 shrink-0 items-center justify-center rounded-xl border",
                      active
                        ? "border-sky-500/30 bg-sky-500/10"
                        : task.status === "success"
                          ? "border-mint-500/25 bg-mint-500/[0.08]"
                          : task.status === "partial"
                            ? "border-amber-500/25 bg-amber-500/[0.08]"
                            : "border-white/10 bg-white/[0.04]",
                    )}
                  >
                    <TaskIcon
                      type={task.type}
                      className={clsx(
                        active ? "text-sky-300" : task.status === "success" ? "text-mint-400" : "text-slate-400",
                      )}
                    />
                  </div>
                  <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="text-[13px] font-medium text-slate-100">
                        {taskTypeLabel(task.type)} · {task.library_name}
                      </span>
                      <StatusBadge status={task.status} />
                      <span className="text-[11px] text-slate-600">#{task.id}</span>
                    </div>
                    <div className="mt-1 text-[11px] text-slate-500">{statsSummary(task)}</div>
                    {active ? <Progress value={task.progress} className="mt-2 max-w-md" /> : null}
                  </div>
                  <div className="flex items-center gap-2 text-[11px] text-slate-500">
                    <Clock size={12} />
                    {formatTime(task.finished_at || task.started_at || task.created_at)}
                  </div>
                  <div className="flex items-center gap-1.5">
                    {active ? (
                      <>
                        <span className="mr-2 hidden text-[11px] text-sky-200/80 sm:inline">
                          {task.done}/{task.total || "?"} · {Math.round(task.progress)}%
                        </span>
                        <Button className="px-2.5 py-1.5 text-xs" onClick={() => setCancelTarget(task)}>
                          <Ban size={13} /> 取消
                        </Button>
                      </>
                    ) : (
                      <Button className="px-2.5 py-1.5 text-xs" onClick={() => retry(task)}>
                        <RotateCcw size={13} /> 重试
                      </Button>
                    )}
                    <Button className="px-2.5 py-1.5 text-xs" onClick={() => setOpenTaskId(task.id)}>
                      详情 <ChevronRight size={13} />
                    </Button>
                  </div>
                </div>
              </Card>
            );
          })}
        </div>
      )}

      <Drawer open={openTaskId !== null} onClose={() => setOpenTaskId(null)} title="任务详情" width="max-w-2xl">
        {detailLoading && !detail ? (
          <div className="flex h-40 items-center justify-center gap-2 text-sm text-slate-400">
            <Spinner /> 读取任务…
          </div>
        ) : detail ? (
          <div className="space-y-5">
            <div className="grid grid-cols-2 gap-3">
              {[
                { label: "类型", value: taskTypeLabel(detail.type) },
                { label: "影视库", value: detail.library_name },
                { label: "状态", value: <StatusBadge status={detail.status} /> },
                {
                  label: "进度",
                  value: (
                    <span className="text-sm text-slate-200">
                      {detail.done}/{detail.total || "?"} · {Math.round(detail.progress)}%
                    </span>
                  ),
                },
                { label: "创建时间", value: <span className="text-xs text-slate-300">{formatTime(detail.created_at)}</span> },
                {
                  label: "结束时间",
                  value: <span className="text-xs text-slate-300">{formatTime(detail.finished_at)}</span>,
                },
              ].map((entry) => (
                <div key={entry.label} className="rounded-xl border border-white/[0.06] bg-white/[0.02] px-3.5 py-2.5">
                  <div className="text-[11px] text-slate-500">{entry.label}</div>
                  <div className="mt-1 text-sm text-slate-200">{entry.value}</div>
                </div>
              ))}
            </div>

            {detail.status === "running" || detail.status === "queued" ? (
              <Progress value={detail.progress} />
            ) : null}

            <div className="rounded-xl border border-white/[0.06] bg-white/[0.02] px-3.5 py-3 text-[11px] text-slate-400">
              {statsSummary(detail)}
            </div>

            {detail.error ? (
              <div className="rounded-xl border border-rose-500/25 bg-rose-500/[0.08] px-3.5 py-3 text-xs text-rose-200">
                {detail.error}
              </div>
            ) : null}

            <div>
              <div className="mb-2 flex items-center justify-between">
                <span className="text-xs font-medium text-slate-300">执行日志</span>
                <span className="text-[11px] text-slate-600">{(detail.logs || []).length} 条</span>
              </div>
              <div className="max-h-[380px] space-y-1 overflow-y-auto rounded-xl border border-white/[0.06] bg-ink-950/60 p-3 font-mono text-[11px] leading-relaxed">
                {(detail.logs || []).length === 0 ? (
                  <div className="text-slate-600">暂无日志</div>
                ) : (
                  (detail.logs || []).map((log) => (
                    <div key={log.id} className="flex gap-2">
                      <span className="shrink-0 text-slate-600">{log.ts?.slice(11, 19) || "—"}</span>
                      <span className={clsx("shrink-0 uppercase", LEVEL_TONES[log.level] || "text-slate-500")}>
                        {log.level}
                      </span>
                      <span className="min-w-0 break-all text-slate-300">{log.message}</span>
                    </div>
                  ))
                )}
              </div>
            </div>

            {detail.status === "running" || detail.status === "queued" ? (
              <div className="flex justify-end">
                <Button variant="danger" onClick={() => setCancelTarget(detail)}>
                  <Ban size={15} /> 取消任务
                </Button>
              </div>
            ) : detail.status !== "success" ? (
              <div className="flex justify-end">
                <Button onClick={() => retry(detail)}>
                  <RotateCcw size={15} /> 重新执行
                </Button>
              </div>
            ) : (
              <div className="flex items-center justify-end gap-2 text-xs text-mint-300">
                <CheckCircle2 size={15} /> 任务已完成
              </div>
            )}
          </div>
        ) : (
          <EmptyState icon={<Sparkles size={24} />} title="任务不存在" />
        )}
      </Drawer>

      <ConfirmDialog
        open={Boolean(cancelTarget)}
        title="取消任务"
        message="取消后当前文件会处理完再停止，已完成的文件不会被回滚。确定取消吗？"
        confirmText={acting ? "取消中…" : "确定取消"}
        danger
        onConfirm={cancel}
        onClose={() => setCancelTarget(null)}
      />
    </div>
  );
}
