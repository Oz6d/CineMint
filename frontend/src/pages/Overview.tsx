import { Link, useNavigate } from "react-router-dom";
import {
  ArrowRight,
  CheckCircle2,
  Clapperboard,
  FolderSearch,
  HardDrive,
  Library,
  ListChecks,
  Play,
  Sparkles,
  TriangleAlert,
} from "lucide-react";
import type { Overview } from "../types";
import { useAsync } from "../lib/hooks";
import { Badge, Button, Card, EmptyState, Poster, Progress, Spinner, StatusBadge } from "../components/ui";
import { formatTime, taskTypeLabel } from "../lib/format";
import { useToast } from "../lib/toast";
import { api } from "../lib/api";

export default function OverviewPage() {
  const { data, loading, reload } = useAsync<Overview>("/api/overview", [], { pollMs: 5000 });
  const toast = useToast();
  const navigate = useNavigate();

  const seedDemo = async () => {
    try {
      await api.post("/api/demo/seed");
      toast.success("演示数据已就绪");
      reload();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "生成失败");
    }
  };

  if (loading && !data) {
    return (
      <div className="flex h-64 items-center justify-center gap-2 text-sm text-slate-400">
        <Spinner /> 加载总览…
      </div>
    );
  }
  if (!data) return null;

  const { totals, libraries, active_tasks, recent_tasks, pending_items } = data;
  const hasLibraries = libraries.length > 0;

  const stats = [
    { label: "影视作品", value: totals.works, icon: Clapperboard, tone: "text-mint-400" },
    { label: "待确认", value: totals.review, icon: TriangleAlert, tone: "text-amber-300" },
    { label: "已整理文件", value: totals.organized, icon: CheckCircle2, tone: "text-sky-300" },
    { label: "未识别", value: totals.unmatched, icon: FolderSearch, tone: "text-slate-300" },
  ];

  return (
    <div className="mx-auto max-w-7xl space-y-6">
      {/* Hero */}
      <div className="glass relative overflow-hidden p-6 lg:p-8">
        <div className="pointer-events-none absolute -right-20 -top-24 h-64 w-64 rounded-full bg-mint-500/15 blur-3xl" />
        <div className="pointer-events-none absolute -bottom-28 right-32 h-52 w-52 rounded-full bg-sky-500/10 blur-3xl" />
        <div className="relative flex flex-wrap items-end justify-between gap-6">
          <div className="max-w-xl">
            <Badge tone="mint">
              <Sparkles size={12} /> 自动识别 · 重命名 · 刮削
            </Badge>
            <h1 className="mt-3 text-2xl font-semibold leading-snug text-white lg:text-3xl">
              把杂乱的片库，整理成一面海报墙
            </h1>
            <p className="mt-2 text-sm leading-relaxed text-slate-400">
              接入本地 / WebDAV / SMB 目录，自动解析季集与年份，匹配 TMDb 元数据，
              按模板重命名归档，并生成 Emby / Jellyfin / Infuse 可用的 NFO 与海报。
            </p>
            <div className="mt-5 flex flex-wrap gap-2.5">
              {hasLibraries ? (
                <>
                  <Button variant="primary" onClick={() => navigate("/libraries")}>
                    <Play size={15} /> 进入影视库
                  </Button>
                  {totals.pending > 0 ? (
                    <Button onClick={() => navigate(`/libraries/${libraries[0].id}?tab=review`)}>
                      处理 {totals.pending} 个待整理文件
                      <ArrowRight size={15} />
                    </Button>
                  ) : null}
                </>
              ) : (
                <>
                  <Button variant="primary" onClick={() => navigate("/libraries")}>
                    <Library size={15} /> 创建影视库
                  </Button>
                  <Button onClick={seedDemo}>
                    <Sparkles size={15} /> 载入演示数据
                  </Button>
                </>
              )}
            </div>
          </div>
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
            {stats.map((stat) => (
              <div key={stat.label} className="rounded-2xl border border-white/[0.07] bg-white/[0.03] px-4 py-3">
                <stat.icon size={16} className={stat.tone} />
                <div className="mt-2 text-2xl font-semibold tracking-tight text-white">{stat.value}</div>
                <div className="text-[11px] text-slate-500">{stat.label}</div>
              </div>
            ))}
          </div>
        </div>
      </div>

      {/* 活动任务 */}
      {active_tasks.length > 0 ? (
        <Card className="border-sky-500/20 bg-sky-500/[0.04]">
          <div className="mb-3 flex items-center gap-2 text-sm text-sky-200">
            <Spinner size={14} /> 正在执行的任务
          </div>
          <div className="space-y-3">
            {active_tasks.map((task) => (
              <div key={task.id}>
                <div className="flex items-center justify-between text-xs text-slate-300">
                  <span>
                    {taskTypeLabel(task.type)} · {task.library_name}
                  </span>
                  <span>
                    {task.done}/{task.total} · {Math.round(task.progress)}%
                  </span>
                </div>
                <Progress value={task.progress} className="mt-1.5" />
              </div>
            ))}
          </div>
        </Card>
      ) : null}

      {/* 影视库卡片 */}
      <div>
        <div className="mb-3 flex items-center justify-between">
          <h2 className="text-sm font-semibold text-slate-200">我的影视库</h2>
          <Link to="/libraries" className="text-xs text-mint-400 transition hover:text-mint-300">
            管理影视库 →
          </Link>
        </div>
        {hasLibraries ? (
          <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
            {libraries.map((library) => (
              <Link
                key={library.id}
                to={`/libraries/${library.id}`}
                className="glass group relative overflow-hidden p-0 transition hover:border-mint-500/30 hover:shadow-glow"
              >
                <div className="flex gap-1.5 p-1.5">
                  {library.posters.slice(0, 4).map((url, index) => (
                    <Poster
                      key={`${url}-${index}`}
                      url={url}
                      title={library.name}
                      rounded="rounded-lg"
                      className="h-24 w-16 shrink-0"
                    />
                  ))}
                  {library.posters.length === 0
                    ? Array.from({ length: 4 }).map((_, index) => (
                        <div key={index} className="poster-fallback h-24 w-16 shrink-0 rounded-lg opacity-60" />
                      ))
                    : null}
                </div>
                <div className="px-4 py-3.5">
                  <div className="flex items-center justify-between gap-3">
                    <div className="min-w-0">
                      <div className="truncate text-sm font-medium text-white">{library.name}</div>
                      <div className="mt-0.5 flex items-center gap-1.5 text-[11px] text-slate-500">
                        <HardDrive size={11} />
                        {library.source_type.toUpperCase()} · {library.source_name}
                        {library.root_path ? ` · /${library.root_path}` : ""}
                      </div>
                    </div>
                    {library.is_demo ? <Badge tone="violet">演示</Badge> : null}
                  </div>
                  <div className="mt-3 flex flex-wrap gap-1.5">
                    <Badge tone="mint">{library.counts.organized} 已整理</Badge>
                    {library.counts.matched > 0 ? <Badge tone="sky">{library.counts.matched} 已匹配</Badge> : null}
                    {library.counts.review > 0 ? <Badge tone="amber">{library.counts.review} 待确认</Badge> : null}
                    {library.counts.unmatched > 0 ? <Badge>{library.counts.unmatched} 未识别</Badge> : null}
                    {library.counts.pending === 0 && library.counts.total > 0 ? (
                      <Badge tone="sky">全部就绪</Badge>
                    ) : null}
                  </div>
                </div>
              </Link>
            ))}
          </div>
        ) : (
          <EmptyState
            icon={<Library size={26} />}
            title="还没有影视库"
            description="创建影视库需要一个文件源：本地挂载目录、WebDAV 或 SMB 共享。也可以先载入演示数据体验完整流程。"
            action={
              <div className="mt-2 flex gap-2">
                <Button variant="primary" onClick={() => navigate("/sources")}>
                  添加文件源
                </Button>
                <Button onClick={seedDemo}>
                  <Sparkles size={15} /> 载入演示数据
                </Button>
              </div>
            }
          />
        )}
      </div>

      <div className="grid gap-6 lg:grid-cols-5">
        {/* 待处理 */}
        <Card className="lg:col-span-3">
          <div className="mb-4 flex items-center justify-between">
            <h2 className="text-sm font-semibold text-slate-200">需要你确认的识别结果</h2>
            <Badge tone={pending_items.length ? "amber" : "mint"}>{pending_items.length} 条</Badge>
          </div>
          {pending_items.length === 0 ? (
            <div className="py-8 text-center text-xs text-slate-500">暂时没有待确认的条目，去扫描一下影视库吧。</div>
          ) : (
            <div className="space-y-2.5">
              {pending_items.slice(0, 5).map((item) => (
                <Link
                  key={item.id}
                  to={`/libraries/${item.library_id}?tab=review&item=${item.id}`}
                  className="flex items-center gap-3 rounded-xl border border-white/[0.05] bg-white/[0.02] px-3 py-2.5 transition hover:border-mint-500/25 hover:bg-white/[0.04]"
                >
                  <Poster
                    url={item.match?.poster_url}
                    title={item.parsed?.title || item.file_name}
                    rounded="rounded-lg"
                    className="h-14 w-10 shrink-0"
                  />
                  <div className="min-w-0 flex-1">
                    <div className="truncate text-[13px] text-slate-200">
                      {item.match?.title || item.parsed?.title || "未知标题"}
                      {item.match?.year ? <span className="ml-1.5 text-slate-500">({item.match.year})</span> : null}
                    </div>
                    <div className="mt-0.5 truncate text-[11px] text-slate-500">{item.file_name}</div>
                    <div className="mt-1 flex items-center gap-2">
                      <StatusBadge status={item.status} />
                      <span className="text-[11px] text-slate-500">{item.library_name}</span>
                    </div>
                  </div>
                  <ArrowRight size={15} className="shrink-0 text-slate-600" />
                </Link>
              ))}
            </div>
          )}
        </Card>

        {/* 最近任务 */}
        <Card className="lg:col-span-2">
          <div className="mb-4 flex items-center justify-between">
            <h2 className="text-sm font-semibold text-slate-200">最近任务</h2>
            <Link to="/tasks" className="text-xs text-mint-400 transition hover:text-mint-300">
              全部任务 →
            </Link>
          </div>
          {recent_tasks.length === 0 ? (
            <div className="py-8 text-center text-xs text-slate-500">还没有任务记录</div>
          ) : (
            <div className="space-y-3">
              {recent_tasks.map((task) => (
                <Link key={task.id} to="/tasks" className="block rounded-xl border border-white/[0.05] bg-white/[0.02] px-3 py-2.5 transition hover:border-white/15">
                  <div className="flex items-center justify-between text-[12px] text-slate-300">
                    <span className="flex items-center gap-1.5">
                      <ListChecks size={13} className="text-slate-500" />
                      {taskTypeLabel(task.type)} · {task.library_name}
                    </span>
                    <StatusBadge status={task.status} />
                  </div>
                  <div className="mt-1.5 flex items-center justify-between text-[11px] text-slate-500">
                    <span>{formatTime(task.finished_at || task.created_at)}</span>
                    {task.type === "scan" && task.stats?.found != null ? <span>发现 {task.stats.found} 个文件</span> : null}
                    {task.type === "organize" && task.stats?.success != null ? (
                      <span>
                        成功 {task.stats.success} · 失败 {task.stats.failed ?? 0}
                      </span>
                    ) : null}
                  </div>
                </Link>
              ))}
            </div>
          )}
        </Card>
      </div>
    </div>
  );
}
