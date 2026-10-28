import { useEffect, useRef, useState } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import {
  ArrowLeft,
  Check,
  CheckCheck,
  ExternalLink,
  Eye,
  Film,
  Files,
  Layers3,
  Loader2,
  Paperclip,
  PencilLine,
  TextCursorInput,
  RefreshCw,
  Search,
  Settings2,
  SkipForward,
  Sparkles,
  Tv,
  Undo2,
  Wand2,
  Wrench,
} from "lucide-react";
import clsx from "clsx";
import type { AttachmentPayload, Library as LibraryType, MediaItem, Task, Work, WorkSeason } from "../types";
import { useAsync } from "../lib/hooks";
import { api } from "../lib/api";
import { useToast } from "../lib/toast";
import { AttachmentPanel } from "../components/AttachmentPanel";
import { FileBrowser } from "../components/FileBrowser";
import { LibraryFiles } from "../components/LibraryFiles";
import { RenameModal } from "../components/RenameModal";
import { TargetNameModal, type TargetNameItem } from "../components/TargetNameModal";
import {
  Badge,
  Button,
  Card,
  ConfirmDialog,
  Drawer,
  EmptyState,
  Field,
  Input,
  Modal,
  Poster,
  Select,
  Spinner,
  StatusBadge,
  Switch,
  TitleLogo,
} from "../components/ui";
import { formatSize, formatTime, seasonEpisode } from "../lib/format";

type Tab = "works" | "review" | "files" | "organized";

type ItemListPayload = {
  total: number;
  items: MediaItem[];
  checked?: number;
  hidden_missing?: number;
  unverified?: number;
};

const ATTACHMENT_KIND_LABELS: Record<string, string> = {
  subtitle: "字幕",
  audio: "音轨",
  image: "图片",
  meta: "元信息",
};

type WorkItem = Work["items"][number];
type WorkSeasonGroup = {
  number: number;
  label: string;
  kicker: string;
  items: WorkItem[];
  season?: WorkSeason;
};

function seasonLabel(number: number, name?: string): string {
  const fallback = number === 0 ? "特别篇" : number > 0 ? `Season ${number}` : "未分季";
  const custom = (name || "").trim();
  if (!custom) return fallback;
  const normalized = custom.toLowerCase().replace(/\s+/g, " ");
  const generic = new Set([
    `season ${number}`,
    `season ${String(number).padStart(2, "0")}`,
    `第 ${number} 季`,
    `第${number}季`,
  ]);
  if (generic.has(normalized)) return fallback;
  return custom;
}

function seasonKicker(number: number): string {
  if (number === 0) return "SPECIALS";
  if (number > 0) return `SEASON ${number}`;
  return "UNSORTED";
}

const TABS: { key: Tab; label: string }[] = [
  { key: "works", label: "海报墙" },
  { key: "review", label: "待确认" },
  { key: "organized", label: "已整理" },
  { key: "files", label: "全部文件" },
];

export default function LibraryDetailPage() {
  const { id } = useParams();
  const libraryId = Number(id);
  const [params, setParams] = useSearchParams();
  const tab = (params.get("tab") as Tab) || "works";
  const toast = useToast();

  const [selected, setSelected] = useState<number[]>([]);
  const [filesView, setFilesView] = useState<"videos" | "all">("videos");
  const [openItem, setOpenItem] = useState<MediaItem | null>(null);
  const [openWork, setOpenWork] = useState<Work | null>(null);
  const [targetEditItem, setTargetEditItem] = useState<TargetNameItem | null>(null);
  const [workSeason, setWorkSeason] = useState<number | "all">("all");
  const [planOpen, setPlanOpen] = useState(false);
  const [plan, setPlan] = useState<any[] | null>(null);
  const [planLoading, setPlanLoading] = useState(false);
  const [namingOpen, setNamingOpen] = useState(false);
  const [organizing, setOrganizing] = useState(false);
  const [renameOpen, setRenameOpen] = useState(false);
  const [renameIds, setRenameIds] = useState<number[]>([]);
  const [correctOpen, setCorrectOpen] = useState(false);
  const [correctItems, setCorrectItems] = useState<BatchItem[]>([]);
  const [correctMode, setCorrectMode] = useState<BatchMode>("offset");
  const taskTimer = useRef<number | null>(null);
  const workLogoCache = useRef<Map<string, string>>(new Map());
  const itemFetchRef = useRef<number | null>(null);

  const { data: library, reload: reloadLibrary } = useAsync<LibraryType>(
    Number.isFinite(libraryId) ? `/api/libraries/${libraryId}` : null,
    [],
    { pollMs: 5000 },
  );
  const { data: activeTasks } = useAsync<Task[]>("/api/tasks/active", [], { pollMs: 2500 });
  const libraryTask = (activeTasks || []).find((task) => task.library_id === libraryId) || null;

  const statusFilter =
    tab === "review" ? "review,unmatched,failed" : tab === "organized" ? "organized" : "";
  const itemQuery =
    tab === "files" && filesView === "videos"
      ? `/api/libraries/${libraryId}/source-items?limit=2000`
      : tab === "review" || tab === "organized"
        ? `/api/libraries/${libraryId}/items?limit=500&status=${statusFilter}`
        : null;
  const { data: itemData, reload: reloadItems } = useAsync<ItemListPayload>(itemQuery, [tab, filesView]);
  const { data: workData, loading: worksLoading, reload: reloadWorks } = useAsync<{
    works: Work[];
    unmatched: number;
    hidden_missing?: number;
    unverified?: number;
    error?: string;
  }>(tab === "works" ? `/api/libraries/${libraryId}/works` : null, [tab]);

  useEffect(() => {
    return () => {
      if (taskTimer.current) window.clearInterval(taskTimer.current);
    };
  }, []);

  const watchTask = (taskId: number) => {
    if (taskTimer.current) window.clearInterval(taskTimer.current);
    const tick = async () => {
      try {
        const task = await api.get<Task>(`/api/tasks/${taskId}`);
        if (task.status !== "running" && task.status !== "queued") {
          if (taskTimer.current) window.clearInterval(taskTimer.current);
          taskTimer.current = null;
          reloadItems();
          reloadWorks();
          reloadLibrary();
        }
      } catch {
        if (taskTimer.current) window.clearInterval(taskTimer.current);
        taskTimer.current = null;
      }
    };
    taskTimer.current = window.setInterval(tick, 2000);
  };

  useEffect(() => {
    const raw = params.get("item");
    if (!raw || !itemData) return;
    if (tab === "files" && filesView === "all") return;
    const itemId = Number(raw);
    const found = itemData.items.find((item) => item.id === itemId);
    if (found) {
      itemFetchRef.current = null;
      setOpenItem(found);
      return;
    }
    // 已整理 / 源缺失的条目不再出现在「全部文件」树中，
    // 但从海报墙点「查看」时仍按 id 直接读取详情，避免入口失效。
    if (itemFetchRef.current === itemId) return;
    itemFetchRef.current = itemId;
    api
      .get<MediaItem>(`/api/items/${itemId}`)
      .then((item) => setOpenItem(item))
      .catch(() => undefined)
      .finally(() => {
        if (itemFetchRef.current === itemId) itemFetchRef.current = null;
      });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [params.get("item"), itemData, tab, filesView]);

  const items = tab === "files" && filesView === "all" ? [] : itemData?.items || [];
  const allSelected = items.length > 0 && selected.length === items.length;
  const reviewCount = library ? library.counts.review + library.counts.unmatched + library.counts.failed : 0;

  const toggleSelect = (itemId: number) => {
    setSelected((current) =>
      current.includes(itemId) ? current.filter((entry) => entry !== itemId) : [...current, itemId],
    );
  };

  const scan = async () => {
    try {
      const result = await api.post<{ task_id: number }>(`/api/libraries/${libraryId}/scan`);
      toast.success("扫描任务已开始");
      watchTask(result.task_id);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "扫描失败");
    }
  };

  const previewPlan = async (ids: number[]) => {
    setPlanOpen(true);
    setPlanLoading(true);
    setPlan(null);
    try {
      const result = await api.post<{ items: any[] }>(`/api/libraries/${libraryId}/plan`, { item_ids: ids });
      setPlan(result.items);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "生成预览失败");
      setPlanOpen(false);
    } finally {
      setPlanLoading(false);
    }
  };

  const handleTargetSaved = (updated: MediaItem) => {
    const planned = updated.target_name_override || updated.planned_name || updated.file_name;
    setOpenItem((current) => (current && current.id === updated.id ? updated : current));
    setOpenWork((current) => {
      if (!current) return current;
      return {
        ...current,
        items: current.items.map((item) =>
          item.id === updated.id
            ? {
                ...item,
                planned_name: planned,
                target_name_override: updated.target_name_override || "",
                auto_name: updated.planned_name || item.auto_name,
              }
            : item,
        ),
      };
    });
    reloadItems();
    reloadWorks();
    if (planOpen) void previewPlan((plan || []).filter((entry) => !entry.error).map((entry) => entry.id));
  };

  const organize = async (ids: number[]) => {
    if (!ids.length) {
      toast.error("请先选择要整理的文件");
      return;
    }
    setOrganizing(true);
    try {
      const result = await api.post<{ task_id: number }>(`/api/libraries/${libraryId}/organize`, { item_ids: ids });
      toast.success("整理任务已提交，进度可在任务中心查看");
      setPlanOpen(false);
      setSelected([]);
      watchTask(result.task_id);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "整理失败");
    } finally {
      setOrganizing(false);
    }
  };

  const openRename = (ids: number[]) => {
    if (!ids.length) {
      toast.error("请先选择要重命名的文件");
      return;
    }
    const usable = ids.filter((id) => items.find((item) => item.id === id)?.status !== "organized");
    if (!usable.length) {
      toast.error("已整理的文件不支持仅重命名，请先恢复为待确认");
      return;
    }
    if (usable.length < ids.length) {
      toast.push(`已跳过 ${ids.length - usable.length} 个已整理的文件`);
    }
    setRenameIds(usable);
    setRenameOpen(true);
  };

  const updateItem = (updated: MediaItem) => {
    setOpenItem((current) => (current && current.id === updated.id ? updated : current));
    reloadItems();
    reloadWorks();
  };

  const openCorrection = (entries: BatchItem[], mode: BatchMode = "offset") => {
    if (!entries.length) return;
    const ordered = [...entries].sort(
      (a, b) =>
        (a.season ?? 0) - (b.season ?? 0) ||
        (a.episode ?? 0) - (b.episode ?? 0) ||
        a.file_name.localeCompare(b.file_name),
    );
    setCorrectItems(ordered);
    setCorrectMode(mode);
    setCorrectOpen(true);
  };

  const correctionFromSelection = () => {
    const picked = items.filter((item) => selected.includes(item.id));
    const usable = picked.filter((item) => item.status !== "organized");
    if (!usable.length) {
      toast.error("已整理的文件不支持批量纠错，请先恢复为待确认");
      return;
    }
    if (usable.length < picked.length) {
      toast.push(`已跳过 ${picked.length - usable.length} 个已整理的文件`);
    }
    openCorrection(
      usable.map((item) => ({
        id: item.id,
        file_name: item.file_name,
        season: item.parsed?.season,
        episode: item.parsed?.episode,
        media_type: item.match?.media_type || item.parsed?.media_type,
      })),
    );
  };

  const openWorkWithLogo = (work: Work) => {
    const cacheKey = `${work.media_type}:${work.tmdb_id}`;
    const cached = workLogoCache.current.get(cacheKey);
    const needsSeasonDetails = work.media_type === "tv";
    setWorkSeason("all");
    if (cached !== undefined && !needsSeasonDetails) {
      setOpenWork({ ...work, logo_url: cached });
      return;
    }
    // 旧数据里可能存的是中文 logo，打开时统一按原文语言重新取一次
    setOpenWork({ ...work, logo_url: cached !== undefined ? cached : work.logo_url });
    if (!work.tmdb_id || !work.media_type) return;
    api
      .get<{ logo_url?: string; seasons?: WorkSeason[] }>(`/api/items/tmdb/${work.media_type}/${work.tmdb_id}`)
      .then((details) => {
        const logo = details.logo_url || "";
        workLogoCache.current.set(cacheKey, logo);
        setOpenWork((current) =>
          current && current.key === work.key
            ? { ...current, logo_url: logo, season_details: details.seasons || [] }
            : current,
        );
      })
      .catch(() => undefined);
  };

  const isTvWork = openWork?.media_type === "tv";
  const seasonInfo = new Map<number, WorkSeason>();
  for (const season of openWork?.season_details || []) {
    seasonInfo.set(season.season_number, season);
  }

  const seasonGroups: WorkSeasonGroup[] = [];
  if (isTvWork && openWork) {
    const bySeason = new Map<number, WorkItem[]>();
    for (const item of openWork.items) {
      const number = item.season == null ? -1 : Number(item.season);
      const group = bySeason.get(number);
      if (group) group.push(item);
      else bySeason.set(number, [item]);
    }
    for (const [number, groupItems] of [...bySeason.entries()].sort(([a], [b]) => a - b)) {
      const meta = seasonInfo.get(number);
      seasonGroups.push({
        number,
        label: seasonLabel(number, meta?.name),
        kicker: seasonKicker(number),
        items: groupItems,
        season: meta,
      });
    }
  }

  const activeSeason =
    isTvWork && seasonGroups.some((group) => group.number === workSeason) ? workSeason : "all";
  const visibleSeasonGroups =
    isTvWork && activeSeason !== "all"
      ? seasonGroups.filter((group) => group.number === activeSeason)
      : seasonGroups;
  const visibleWorkItems = isTvWork
    ? visibleSeasonGroups.flatMap((group) => group.items)
    : openWork?.items || [];

  const renderWorkItem = (item: WorkItem) => {
    const afterName = item.target_path
      ? item.target_path.split("/").pop() || item.planned_name || item.file_name
      : item.planned_name || item.file_name;
    return (
      <div
        key={item.id}
        className="group flex items-center gap-3 rounded-2xl border border-white/[0.05] bg-white/[0.02] px-3.5 py-3 transition hover:border-sky-400/20 hover:bg-white/[0.04]"
      >
        <div className="min-w-0 flex-1">
          <div className="truncate text-[12px] font-medium text-sky-300" title={afterName}>
            {afterName}
          </div>
          <div className="mt-1 truncate text-[11px] text-slate-200/90" title={item.file_name}>
            {item.file_name}
          </div>
        </div>
        <div className="flex shrink-0 items-center gap-1.5">
          <StatusBadge status={item.status} />
          <Button
            className="px-2 py-1 text-[11px]"
            title="修改整理后名称"
            onClick={() =>
              setTargetEditItem({
                id: item.id,
                file_name: item.file_name,
                planned_name: afterName,
                auto_name: item.auto_name,
                target_name_override: item.target_name_override,
              })
            }
          >
            <PencilLine size={12} />
          </Button>
          <Button
            className="px-2 py-1 text-[11px]"
            onClick={() => {
              const found = items.find((entry) => entry.id === item.id);
              if (found) {
                setOpenWork(null);
                setOpenItem(found);
              } else {
                setParams({ tab: "files", item: String(item.id) });
                setOpenWork(null);
              }
            }}
          >
            查看
          </Button>
        </div>
      </div>
    );
  };

  if (!library) {
    return (
      <div className="flex h-64 items-center justify-center gap-2 text-sm text-slate-400">
        <Spinner /> 加载影视库…
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-7xl space-y-5">
      {/* 头部 */}
      <div className="glass relative overflow-hidden p-5 lg:p-6">
        <div className="pointer-events-none absolute -right-16 -top-20 h-56 w-56 rounded-full bg-sky-500/10 blur-3xl" />
        <div className="relative flex flex-wrap items-start justify-between gap-4">
          <div className="min-w-0">
            <Link to="/libraries" className="mb-2 inline-flex items-center gap-1 text-[11px] text-slate-500 transition hover:text-slate-300">
              <ArrowLeft size={12} /> 返回影视库列表
            </Link>
            <div className="flex flex-wrap items-center gap-2">
              <h1 className="text-lg font-semibold text-white">{library.name}</h1>
              {library.is_demo ? <Badge tone="violet">演示数据</Badge> : null}
              {libraryTask ? (
                <Badge tone="sky">
                  <Loader2 size={11} className="animate-spin" />
                  {libraryTask.type === "scan" ? "扫描中" : "整理中"} {Math.round(libraryTask.progress)}%
                </Badge>
              ) : null}
            </div>
            <div className="mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px] text-slate-500">
              <span>
                文件源：{library.source_type.toUpperCase()} · {library.source_name}
              </span>
              <span className="font-mono">/{library.root_path || ""}</span>
              <span className="font-mono">→ /{library.target_root || ""}</span>
              {library.last_scan_at ? <span>上次扫描 {formatTime(library.last_scan_at)}</span> : null}
            </div>
          </div>
          <div className="flex flex-wrap gap-2">
            <Button onClick={() => setNamingOpen(true)}>
              <Settings2 size={15} /> 命名与刮削
            </Button>
            <Button onClick={scan} disabled={Boolean(libraryTask)}>
              <RefreshCw size={15} /> 扫描识别
            </Button>
            <Button
              onClick={() => openRename(selected)}
              disabled={!selected.length || Boolean(libraryTask)}
            >
              <TextCursorInput size={15} /> 仅重命名
            </Button>
            <Button
              variant="primary"
              onClick={() => previewPlan(selected)}
              disabled={!selected.length || Boolean(libraryTask)}
            >
              <Wand2 size={15} /> 整理所选（{selected.length}）
            </Button>
          </div>
        </div>

        <div className="relative mt-5 grid grid-cols-2 gap-3 sm:grid-cols-5">
          {[
            { label: "作品", value: library.counts.works },
            {
              label: "待整理文件",
              value: library.counts.total - library.counts.organized - library.counts.missing,
            },
            { label: "已整理", value: library.counts.organized, tone: "text-mint-400" },
            { label: "待确认", value: library.counts.review, tone: "text-amber-300" },
            { label: "未识别", value: library.counts.unmatched, tone: "text-slate-300" },
          ].map((stat) => (
            <div key={stat.label} className="rounded-xl border border-white/[0.06] bg-white/[0.02] px-3.5 py-2.5">
              <div className={clsx("text-xl font-semibold text-white", stat.tone)}>{stat.value}</div>
              <div className="text-[11px] text-slate-500">{stat.label}</div>
            </div>
          ))}
        </div>
      </div>

      {/* 标签页 */}
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex gap-1 rounded-xl border border-white/[0.07] bg-white/[0.02] p-1">
          {TABS.map((entry) => (
            <button
              key={entry.key}
              onClick={() => {
                setParams(entry.key === "works" ? {} : { tab: entry.key });
                setSelected([]);
              }}
              className={clsx(
                "rounded-lg px-3.5 py-1.5 text-xs transition",
                tab === entry.key ? "bg-gradient-to-r from-mint-500/20 to-sky-500/15 text-mint-300" : "text-slate-400 hover:text-slate-200",
              )}
            >
              {entry.label}
              {entry.key === "review" && reviewCount > 0 ? (
                <span className="ml-1.5 rounded-full bg-amber-500/20 px-1.5 py-0.5 text-[10px] text-amber-300">
                  {reviewCount}
                </span>
              ) : null}
            </button>
          ))}
        </div>
        <div className="flex flex-wrap items-center gap-2">
          {tab === "files" ? (
            <div className="flex gap-1 rounded-xl border border-white/[0.07] bg-white/[0.02] p-1">
              <button
                onClick={() => {
                  setFilesView("all");
                  setSelected([]);
                }}
                className={clsx(
                  "inline-flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs transition",
                  filesView === "all"
                    ? "bg-sky-500/15 text-sky-300"
                    : "text-slate-400 hover:text-slate-200",
                )}
              >
                <Files size={13} /> 所有文件
              </button>
              <button
                onClick={() => {
                  setFilesView("videos");
                  setSelected([]);
                }}
                className={clsx(
                  "inline-flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs transition",
                  filesView === "videos"
                    ? "bg-mint-500/15 text-mint-300"
                    : "text-slate-400 hover:text-slate-200",
                )}
              >
                <Film size={13} /> 仅视频
              </button>
            </div>
          ) : null}
          {((tab === "files" && filesView === "videos") || tab === "review" || tab === "organized") &&
          items.length > 0 ? (
            <div className="flex items-center gap-2 text-xs text-slate-400">
              <button
                className="rounded-lg border border-white/10 px-2.5 py-1.5 transition hover:bg-white/[0.05]"
                onClick={() => setSelected(allSelected ? [] : items.map((item) => item.id))}
              >
                {allSelected ? "取消全选" : tab === "files" ? "全选" : "全选本页"}
              </button>
              {selected.length > 0 ? (
                <>
                  <Button className="px-2.5 py-1.5 text-xs" onClick={() => setSelected([])}>
                    清空
                  </Button>
                  <Button className="px-2.5 py-1.5 text-xs" onClick={correctionFromSelection}>
                    <Wrench size={13} /> 批量纠错
                  </Button>
                  <Button className="px-2.5 py-1.5 text-xs" onClick={() => openRename(selected)}>
                    <TextCursorInput size={13} /> 仅重命名
                  </Button>
                  <Button variant="primary" className="px-2.5 py-1.5 text-xs" onClick={() => previewPlan(selected)}>
                    <Wand2 size={13} /> 整理所选
                  </Button>
                </>
              ) : null}
            </div>
          ) : null}
        </div>
      </div>

      {tab === "files" ? (
        <div className="rounded-xl border border-white/[0.06] bg-white/[0.02] px-3.5 py-2.5 text-[11px] leading-relaxed text-slate-500">
          {filesView === "all" ? (
            <>
              所有文件视图直接读取待整理目录，可查看并删除 / 移动视频、字幕、NFO、海报和其他文件。删除 / 移动会同步影序条目记录。
            </>
          ) : (
            <>
              仅显示已扫描、仍存在于待整理目录且尚未整理的文件；已整理记录请在「已整理」查看。
            </>
          )}
        </div>
      ) : null}

      {/* 内容 */}
      {tab === "works" ? (
        <>
          <div className="rounded-xl border border-white/[0.06] bg-white/[0.02] px-3.5 py-2.5 text-[11px] leading-relaxed text-slate-500">
            海报墙只显示待整理目录中仍存在的文件；整理完成或已删除的作品会自动移出。
            {workData?.error ? <span className="text-rose-300"> {workData.error}</span> : null}
          </div>
          {worksLoading && !workData ? (
            <div className="flex min-h-[220px] items-center justify-center gap-2 rounded-xl border border-white/[0.06] bg-white/[0.02] text-xs text-slate-500">
              <Spinner /> 正在核对待整理目录…
            </div>
          ) : (
            <WorksGrid
              works={workData?.works || []}
              onOpen={openWorkWithLogo}
              onReview={(work) => setParams({ tab: "review" })}
            />
          )}
        </>
      ) : tab === "files" ? (
        library ? (
          filesView === "all" ? (
            <LibraryFiles
              key={library.id}
              library={library}
              onChanged={() => {
                reloadItems();
                reloadWorks();
                reloadLibrary();
              }}
            />
          ) : (
            <FileBrowser
              key={library.id}
              library={library}
              items={items}
              selected={selected}
              onSelect={setSelected}
              onOpenItem={setOpenItem}
              onPreview={(ids) => previewPlan(ids)}
              onRename={(ids) => openRename(ids)}
              onEditTarget={setTargetEditItem}
              onChanged={() => {
                reloadItems();
                reloadWorks();
                reloadLibrary();
              }}
            />
          )
        ) : null
      ) : (
        <ItemsTable
          items={items}
          selected={selected}
          onToggle={toggleSelect}
          onOpen={setOpenItem}
          onPreview={(itemId) => previewPlan([itemId])}
          onEditTarget={setTargetEditItem}
          emptyText={
            tab === "review"
              ? "没有待确认的条目，识别结果都很确定。"
              : tab === "organized"
                ? "还没有已整理的文件。"
                : "这里还没有文件，先扫描一次吧。"
          }
          showSelection
        />
      )}

      {/* 作品抽屉 */}
      <Drawer
        open={Boolean(openWork)}
        onClose={() => {
          setOpenWork(null);
          setWorkSeason("all");
        }}
        title={openWork ? <TitleLogo url={openWork.logo_url} title={openWork.title} /> : ""}
      >
        {openWork ? (
          <div className="space-y-5">
            <div className="relative overflow-hidden rounded-2xl">
              <Poster url={openWork.backdrop_url || openWork.poster_url} title={openWork.title} rounded="rounded-2xl" className="h-52 w-full" />
              <div className="absolute inset-0 bg-gradient-to-t from-ink-950 via-ink-950/30 to-transparent" />
              <div className="absolute bottom-4 left-4 right-4 flex items-end gap-4">
                <Poster url={openWork.poster_url} title={openWork.title} className="h-36 w-24 shrink-0" />
                <div className="min-w-0 pb-1">
                  <div className="flex items-center gap-2">
                    <Badge tone={openWork.media_type === "tv" ? "sky" : "mint"}>
                      {openWork.media_type === "tv" ? <Tv size={11} /> : <Film size={11} />}
                      {openWork.media_type === "tv" ? "剧集" : "电影"}
                    </Badge>
                    {openWork.vote_average ? <Badge tone="amber">★ {openWork.vote_average.toFixed(1)}</Badge> : null}
                  </div>
                  <h2 className="mt-1.5 text-lg font-semibold text-white">
                    {openWork.title} {openWork.year ? <span className="text-slate-400">({openWork.year})</span> : null}
                  </h2>
                  {openWork.original_title && openWork.original_title !== openWork.title ? (
                    <div className="text-xs text-slate-400">{openWork.original_title}</div>
                  ) : null}
                </div>
              </div>
            </div>

            {openWork.overview ? <p className="text-xs leading-relaxed text-slate-400">{openWork.overview}</p> : null}

            <div className="flex flex-wrap gap-1.5">
              {(openWork.genres || []).map((genre) => (
                <Badge key={genre}>{genre}</Badge>
              ))}
            </div>

            <div className="flex gap-2">
              <Button
                className="flex-1"
                disabled={!openWork.items.some((item) => item.status !== "organized")}
                onClick={() => {
                  const pendingItems = openWork.items.filter((item) => item.status !== "organized");
                  setOpenWork(null);
                  openCorrection(
                    pendingItems.map((item) => ({
                      id: item.id,
                      file_name: item.file_name,
                      season: item.season,
                      episode: item.episode,
                      media_type: openWork.media_type,
                    })),
                    "keep",
                  );
                }}
              >
                <Wrench size={15} /> {openWork.media_type === "tv" ? "整部换剧集" : "换作品"}
              </Button>
              <Button
                variant="primary"
                className="flex-1"
                onClick={() => previewPlan(openWork.items.filter((item) => item.status !== "organized").map((item) => item.id))}
                disabled={!openWork.items.some((item) => item.status !== "organized")}
              >
                <Wand2 size={15} /> 整理这部作品
              </Button>
            </div>
            <Button
              className="w-full"
              onClick={() => {
                const pendingIds = openWork.items.filter((item) => item.status !== "organized").map((item) => item.id);
                setOpenWork(null);
                openRename(pendingIds);
              }}
              disabled={!openWork.items.some((item) => item.status !== "organized")}
            >
              <TextCursorInput size={15} /> 仅重命名这部作品
            </Button>

            <div>
              <div className="mb-3 flex flex-wrap items-center justify-between gap-1.5">
                <div>
                  <div className="text-xs font-medium text-slate-200">
                    文件与剧集 <span className="text-slate-500">（{openWork.items.length}）</span>
                  </div>
                  {isTvWork ? (
                    <div className="mt-0.5 text-[10px] text-slate-500">
                      按季浏览 · {seasonGroups.length} 季
                    </div>
                  ) : null}
                </div>
                <div className="text-[10px] text-slate-500">整理时连同字幕、音轨、图片和 NFO 一起移动</div>
              </div>

              {isTvWork ? (
                <div className="scroll-x-thin -mx-1 mb-4 flex gap-1.5 overflow-x-auto px-1 pb-2">
                  <button
                    type="button"
                    aria-pressed={activeSeason === "all"}
                    onClick={() => setWorkSeason("all")}
                    className={clsx(
                      "relative flex w-[140px] shrink-0 items-center gap-2.5 rounded-2xl border px-2 py-2 text-left transition-all duration-200",
                      activeSeason === "all"
                        ? "border-sky-400/45 bg-gradient-to-br from-sky-400/[0.18] via-sky-400/[0.07] to-transparent shadow-[0_14px_34px_-24px_rgba(56,189,248,0.9)]"
                        : "border-white/[0.06] bg-white/[0.025] hover:-translate-y-0.5 hover:border-white/15 hover:bg-white/[0.05]",
                    )}
                  >
                    <div
                      className={clsx(
                        "flex h-12 w-9 shrink-0 items-center justify-center rounded-lg border",
                        activeSeason === "all"
                          ? "border-sky-300/30 bg-sky-300/10 text-sky-200"
                          : "border-white/10 bg-white/[0.03] text-slate-400",
                      )}
                    >
                      <Layers3 size={17} />
                    </div>
                    <div className="min-w-0">
                      <div className="text-[9px] uppercase tracking-[0.18em] text-slate-500">所有季</div>
                      <div className="truncate text-[12px] font-semibold text-slate-100">全部剧集</div>
                      <div className="text-[10px] text-slate-500">{openWork.items.length} 个文件</div>
                    </div>
                    {activeSeason === "all" ? (
                      <span className="absolute right-2 top-2 h-1.5 w-1.5 rounded-full bg-sky-300 shadow-[0_0_10px_rgba(125,211,252,0.9)]" />
                    ) : null}
                  </button>

                  {seasonGroups.map((group) => {
                    const active = activeSeason === group.number;
                    return (
                      <button
                        key={group.number}
                        type="button"
                        aria-pressed={active}
                        onClick={() => setWorkSeason(group.number)}
                        title={group.label}
                        className={clsx(
                          "relative flex w-[140px] shrink-0 items-center gap-2.5 rounded-2xl border px-2 py-2 text-left transition-all duration-200",
                          active
                            ? "border-sky-400/45 bg-gradient-to-br from-sky-400/[0.18] via-sky-400/[0.07] to-transparent shadow-[0_14px_34px_-24px_rgba(56,189,248,0.9)]"
                            : "border-white/[0.06] bg-white/[0.025] hover:-translate-y-0.5 hover:border-white/15 hover:bg-white/[0.05]",
                        )}
                      >
                        <div className="relative h-12 w-9 shrink-0 overflow-hidden rounded-lg border border-white/10 bg-gradient-to-br from-sky-500/15 to-ink-950">
                          <span className="absolute inset-0 flex items-center justify-center text-[10px] font-semibold tracking-[0.08em] text-sky-200/70">
                            {group.number >= 0 ? `S${String(group.number).padStart(2, "0")}` : "—"}
                          </span>
                          {group.season?.poster_url ? (
                            <img
                              src={group.season.poster_url}
                              alt=""
                              loading="lazy"
                              className="absolute inset-0 h-full w-full object-cover"
                              onError={(event) => {
                                event.currentTarget.style.display = "none";
                              }}
                            />
                          ) : null}
                        </div>
                        <div className="min-w-0">
                          <div className="truncate text-[9px] uppercase tracking-[0.18em] text-slate-500">{group.kicker}</div>
                          <div className="truncate text-[12px] font-semibold text-slate-100" title={group.label}>
                            {group.label}
                          </div>
                          <div className="text-[10px] text-slate-500">{group.items.length} 个文件</div>
                        </div>
                        {active ? (
                          <span className="absolute right-2 top-2 h-1.5 w-1.5 rounded-full bg-sky-300 shadow-[0_0_10px_rgba(125,211,252,0.9)]" />
                        ) : null}
                      </button>
                    );
                  })}
                </div>
              ) : null}

              <div className="space-y-4">
                {isTvWork ? (
                  visibleSeasonGroups.map((group) => (
                    <div key={group.number} className="space-y-2">
                      {activeSeason === "all" ? (
                        <div className="flex items-center gap-2 px-0.5">
                          <span className="h-1.5 w-1.5 rounded-full bg-sky-400/80" />
                          <span className="text-[11px] font-semibold text-slate-300">{group.label}</span>
                          <span className="text-[10px] text-slate-600">{group.items.length} 个文件</span>
                          <span className="ml-auto text-[9px] uppercase tracking-[0.16em] text-slate-600">{group.kicker}</span>
                        </div>
                      ) : null}
                      {group.items.map(renderWorkItem)}
                    </div>
                  ))
                ) : (
                  visibleWorkItems.map(renderWorkItem)
                )}
                {!visibleWorkItems.length ? (
                  <div className="rounded-2xl border border-dashed border-white/[0.07] px-4 py-6 text-center text-xs text-slate-500">
                    当前分类下没有待整理文件
                  </div>
                ) : null}
              </div>
            </div>
          </div>
        ) : null}
      </Drawer>

      <TargetNameModal
        item={targetEditItem}
        onClose={() => setTargetEditItem(null)}
        onSaved={handleTargetSaved}
      />

      {/* 文件抽屉 */}
      <ItemDrawer
        item={openItem}
        onClose={() => {
          setOpenItem(null);
          setParams(tab === "works" ? {} : { tab });
        }}
        onUpdated={updateItem}
        onPreview={(itemId) => previewPlan([itemId])}
        onRename={(itemId) => {
          setOpenItem(null);
          openRename([itemId]);
        }}
      />

      {/* 批量纠错 / 整部换剧集 */}
      <BatchMatchModal
        open={correctOpen}
        items={correctItems}
        defaultMode={correctMode}
        onClose={() => setCorrectOpen(false)}
        onApplied={() => {
          setSelected([]);
          reloadItems();
          reloadWorks();
          reloadLibrary();
        }}
      />

      {/* 整理预览 */}
      <Modal
        open={planOpen}
        onClose={() => setPlanOpen(false)}
        title="整理预览"
        width="max-w-3xl"
        footer={
          <>
            <Button onClick={() => setPlanOpen(false)}>取消</Button>
            <Button
              variant="primary"
              loading={organizing}
              disabled={!plan?.some((entry) => !entry.error)}
              onClick={() => organize((plan || []).filter((entry) => !entry.error).map((entry) => entry.id))}
            >
              <CheckCheck size={15} /> 确认并开始整理
            </Button>
          </>
        }
      >
        {planLoading ? (
          <div className="flex h-40 items-center justify-center gap-2 text-sm text-slate-400">
            <Spinner /> 正在计算目标路径…
          </div>
        ) : (
          <div className="space-y-2">
            {(plan || []).map((entry) => (
              <div key={entry.id} className="rounded-xl border border-white/[0.06] bg-white/[0.02] px-3.5 py-3">
                <div className="break-all text-[12px] text-slate-200">{entry.file_name}</div>
                {entry.error ? (
                  <div className="mt-1 text-[11px] text-rose-300">无法整理：{entry.error}</div>
                ) : (
                  <div className="mt-2 space-y-1.5 font-mono text-[11px] leading-relaxed">
                    <div className="flex gap-2">
                      <span className="h-fit shrink-0 select-none rounded bg-white/[0.06] px-1.5 py-px text-[10px] text-slate-500">源</span>
                      <span className="min-w-0 break-all text-slate-500">{entry.source_path}</span>
                    </div>
                    <div className="flex gap-2">
                      <span className="h-fit shrink-0 select-none rounded bg-mint-400/10 px-1.5 py-px text-[10px] text-mint-300">目标</span>
                      <span className="min-w-0 break-all text-mint-200">{entry.target_path}</span>
                      <button
                        type="button"
                        className="h-fit shrink-0 rounded p-0.5 text-slate-600 transition hover:bg-white/[0.08] hover:text-mint-300"
                        title="修改整理后名称"
                        onClick={() =>
                          setTargetEditItem({
                            id: entry.id,
                            file_name: entry.file_name,
                            planned_name: entry.target_path?.split("/").pop() || entry.file_name,
                            auto_name: entry.auto_name,
                            target_name_override: entry.target_name_override,
                          })
                        }
                      >
                        <PencilLine size={11} />
                      </button>
                    </div>
                    {entry.attachment_total || entry.attachment_unmatched ? (
                      <div className="mt-2 rounded-lg border border-white/[0.05] bg-white/[0.02] px-2.5 py-2 font-sans">
                        <div className="flex flex-wrap items-center gap-1.5 text-[10px] text-slate-400">
                          <Paperclip size={11} className="text-slate-500" />
                          <span>附带 {entry.attachment_total || 0} 个文件</span>
                          {Object.entries(entry.attachment_counts || {})
                            .filter(([, count]) => Number(count) > 0)
                            .map(([kind, count]) => (
                              <span key={kind} className="rounded bg-white/[0.05] px-1.5 py-px text-slate-400">
                                {ATTACHMENT_KIND_LABELS[kind] || kind} {Number(count)}
                              </span>
                            ))}
                        </div>
                        {(entry.attachments || []).length ? (
                          <div className="mt-1 max-h-24 space-y-0.5 overflow-y-auto font-mono text-[10px] leading-relaxed text-slate-500">
                            {(entry.attachments || []).map((attachment: any) => (
                              <div key={attachment.path} className="break-all">
                                {attachment.name}
                                {attachment.scope === "folder" ? " · 文件夹级" : ""}
                                {attachment.manual ? " · 手动关联" : ""}
                              </div>
                            ))}
                          </div>
                        ) : null}
                        {entry.attachment_unmatched ? (
                          <div className="mt-1 text-[10px] text-amber-200/80">
                            另有 {entry.attachment_unmatched} 个文件未能确定归属，整理时不会移动。
                          </div>
                        ) : null}
                      </div>
                    ) : null}
                  </div>
                )}
              </div>
            ))}
            <p className="pt-1 text-[11px] leading-relaxed text-slate-500">
              整理操作会移动文件，并同步同目录的字幕、外挂音轨、图片和 NFO；文件夹级海报 / NFO 保留原文件名。目标已有同名文件时会保留，不会覆盖。
            </p>
          </div>
        )}
      </Modal>

      {/* 仅重命名 */}
      <RenameModal
        open={renameOpen}
        libraryId={libraryId}
        itemIds={renameIds}
        onClose={() => setRenameOpen(false)}
        onStarted={(taskId) => {
          setSelected([]);
          watchTask(taskId);
        }}
      />

      {/* 命名设置 */}
      <NamingModal
        open={namingOpen}
        library={library}
        onClose={() => setNamingOpen(false)}
        onSaved={(updated) => {
          setNamingOpen(false);
          reloadLibrary();
          toast.success("命名与刮削设置已保存");
        }}
      />
    </div>
  );
}

function WorksGrid({ works, onOpen, onReview }: { works: Work[]; onOpen: (work: Work) => void; onReview: (work: Work) => void }) {
  if (!works.length) {
    return (
      <EmptyState
        icon={<Sparkles size={26} />}
        title="海报墙还是空的"
        description="扫描识别后，匹配到 TMDb 的作品会在这里以海报卡片形式展示。"
      />
    );
  }
  return (
    <div className="grid grid-cols-2 gap-4 sm:grid-cols-3 md:grid-cols-4 xl:grid-cols-6">
      {works.map((work) => {
        const pending = (work.counts.review || 0) + (work.counts.unmatched || 0) + (work.counts.failed || 0);
        return (
          <div key={work.key} className="group">
            <div className="relative cursor-pointer overflow-hidden rounded-xl" onClick={() => onOpen(work)}>
              <Poster
                url={work.poster_url}
                title={work.title}
                subtitle={work.year ? String(work.year) : undefined}
                className="aspect-[2/3] w-full transition duration-300 group-hover:scale-[1.03]"
              />
              <div className="pointer-events-none absolute inset-0 rounded-xl bg-gradient-to-t from-ink-950/90 via-transparent to-transparent opacity-80" />
              <div className="absolute left-2 top-2 flex flex-wrap gap-1">
                {pending > 0 ? <Badge tone="amber">{pending} 待确认</Badge> : <Badge tone="mint">已就绪</Badge>}
              </div>
              <div className="absolute bottom-2 left-2 right-2">
                <div className="truncate text-[13px] font-medium text-white">{work.title}</div>
                <div className="flex items-center justify-between text-[11px] text-slate-400">
                  <span>{work.year || "—"}</span>
                  <span>{work.counts.total} 个文件</span>
                </div>
              </div>
            </div>
            {pending > 0 ? (
              <button
                className="mt-1.5 w-full rounded-lg border border-amber-500/25 bg-amber-500/[0.08] px-2 py-1 text-[11px] text-amber-200 transition hover:bg-amber-500/[0.14]"
                onClick={() => onReview(work)}
              >
                去确认 {pending} 个文件
              </button>
            ) : null}
          </div>
        );
      })}
    </div>
  );
}

function ItemsTable({
  items,
  selected,
  onToggle,
  onOpen,
  onPreview,
  onEditTarget,
  emptyText,
  showSelection,
}: {
  items: MediaItem[];
  selected: number[];
  onToggle: (id: number) => void;
  onOpen: (item: MediaItem) => void;
  onPreview: (id: number) => void;
  onEditTarget: (item: MediaItem) => void;
  emptyText: string;
  showSelection?: boolean;
}) {
  if (!items.length) {
    return <EmptyState icon={<Search size={26} />} title={emptyText} />;
  }
  return (
    <Card className="overflow-hidden p-0">
      <div className="overflow-x-auto">
        <table className="w-full min-w-[880px] text-left text-sm">
          <thead>
            <tr className="border-b border-white/[0.06] text-[11px] uppercase tracking-wider text-slate-500">
              {showSelection ? <th className="w-10 px-4 py-3" /> : null}
              <th className="px-4 py-3">文件</th>
              <th className="px-4 py-3">识别结果</th>
              <th className="px-4 py-3">季/集</th>
              <th className="px-4 py-3">状态</th>
              <th className="px-4 py-3 text-right">操作</th>
            </tr>
          </thead>
          <tbody>
            {items.map((item) => (
              <tr key={item.id} className="table-row">
                {showSelection ? (
                  <td className="px-4 py-3">
                    <input
                      type="checkbox"
                      className="h-4 w-4 accent-teal-400"
                      checked={selected.includes(item.id)}
                      onChange={() => onToggle(item.id)}
                    />
                  </td>
                ) : null}
                <td className="max-w-[320px] px-4 py-3">
                  <div className="truncate text-[13px] text-slate-200" title={item.file_name}>
                    {item.file_name}
                  </div>
                  {item.match && item.planned_name ? (
                    <div className="mt-0.5 flex min-w-0 items-center gap-1 text-[10px] text-slate-500">
                      <span className="shrink-0">整理后</span>
                      <span className="truncate text-mint-300/80" title={item.planned_name}>{item.planned_name}</span>
                      <button
                        type="button"
                        className="shrink-0 rounded p-0.5 text-slate-600 transition hover:bg-white/[0.08] hover:text-mint-300"
                        title="修改整理后名称"
                        onClick={() => onEditTarget(item)}
                      >
                        <PencilLine size={10} />
                      </button>
                    </div>
                  ) : null}
                  <div className="mt-0.5 text-[11px] text-slate-600">
                    {formatSize(item.size)} · {item.parsed?.resolution || "—"}
                  </div>
                </td>
                <td className="max-w-[260px] px-4 py-3">
                  <div className="flex items-center gap-2.5">
                    <Poster
                      url={item.match?.poster_url}
                      title={item.match?.title || item.parsed?.title}
                      rounded="rounded-md"
                      className="h-12 w-8 shrink-0"
                    />
                    <div className="min-w-0">
                      <div className="truncate text-[13px] text-slate-200">
                        {item.match?.title || item.parsed?.title || "—"}
                      </div>
                      <div className="mt-0.5 text-[11px] text-slate-500">
                        {item.match?.year || item.parsed?.year || "—"}
                        {item.match?.confidence != null ? ` · ${Math.round(item.match.confidence * 100)}%` : ""}
                        {item.manual ? " · 手动" : ""}
                      </div>
                    </div>
                  </div>
                </td>
                <td className="px-4 py-3 text-[12px] text-slate-400">{seasonEpisode(item.parsed?.season, item.parsed?.episode) || "—"}</td>
                <td className="px-4 py-3">
                  <StatusBadge status={item.status} />
                  {item.error ? <div className="mt-1 max-w-[200px] truncate text-[10px] text-rose-300/80" title={item.error}>{item.error}</div> : null}
                </td>
                <td className="px-4 py-3">
                  <div className="flex justify-end gap-1.5">
                    <Button className="px-2 py-1 text-[11px]" onClick={() => onOpen(item)}>
                      <Eye size={12} /> 详情
                    </Button>
                    {item.status !== "organized" && item.match ? (
                      <Button
                        className="px-2 py-1 text-[11px]"
                        title="修改整理后名称"
                        onClick={() => onEditTarget(item)}
                      >
                        <PencilLine size={12} />
                      </Button>
                    ) : null}
                    {item.status !== "organized" && item.match ? (
                      <Button className="px-2 py-1 text-[11px]" onClick={() => onPreview(item.id)}>
                        <Wand2 size={12} /> 整理
                      </Button>
                    ) : null}
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Card>
  );
}

function ItemDrawer({
  item,
  onClose,
  onUpdated,
  onPreview,
  onRename,
}: {
  item: MediaItem | null;
  onClose: () => void;
  onUpdated: (item: MediaItem) => void;
  onPreview: (id: number) => void;
  onRename: (id: number) => void;
}) {
  const toast = useToast();
  const [detail, setDetail] = useState<MediaItem | null>(item);
  const [attachments, setAttachments] = useState<AttachmentPayload | null>(null);
  const [attachmentsLoading, setAttachmentsLoading] = useState(false);
  const [query, setQuery] = useState("");
  const [mediaType, setMediaType] = useState("");
  const [season, setSeason] = useState<number | "">("");
  const [episode, setEpisode] = useState<number | "">("");
  const [results, setResults] = useState<any[] | null>(null);
  const [searchPage, setSearchPage] = useState(1);
  const [searchPages, setSearchPages] = useState(1);
  const [searching, setSearching] = useState(false);
  const [busy, setBusy] = useState(false);
  const [confirmSkip, setConfirmSkip] = useState(false);
  const [targetName, setTargetName] = useState("");

  useEffect(() => {
    setDetail(item);
    setTargetName(item?.target_name_override || item?.planned_name || item?.file_name || "");
    setSeason(item?.parsed?.season ?? "");
    setEpisode(item?.parsed?.episode ?? "");
    setResults(null);
    setSearchPage(1);
    setSearchPages(1);
    setQuery("");
    setMediaType(item?.match?.media_type || "");
    setAttachments(null);
    setAttachmentsLoading(false);
    if (!item) return;
    let mounted = true;
    api
      .get<MediaItem>(`/api/items/${item.id}`)
      .then((updated) => {
        if (mounted) {
          setDetail(updated);
          setTargetName(
            updated.target_name_override || updated.planned_name || updated.file_name || "",
          );
        }
      })
      .catch(() => {
        /* 列表数据仍然可用 */
      });
    setAttachmentsLoading(true);
    api
      .get<AttachmentPayload>(`/api/items/${item.id}/attachments`)
      .then((data) => {
        if (mounted) setAttachments(data);
      })
      .catch((err) => {
        if (mounted) {
          setAttachments({
            items: [],
            folder_items: [],
            unmatched: [],
            counts: { subtitle: 0, audio: 0, image: 0, meta: 0 },
            total: 0,
            error: err instanceof Error ? err.message : "读取失败",
          });
        }
      })
      .finally(() => {
        if (mounted) setAttachmentsLoading(false);
      });
    return () => {
      mounted = false;
    };
  }, [item?.id]);

  if (!item || !detail) return null;

  const refresh = async () => {
    const updated = await api.get<MediaItem>(`/api/items/${item.id}`);
    setDetail(updated);
    onUpdated(updated);
  };

  const linkAttachment = async (path: string, targetVideoPath?: string) => {
    try {
      const data = await api.post<AttachmentPayload>(`/api/items/${item.id}/attachments/link`, {
        path,
        ...(targetVideoPath ? { target_video_path: targetVideoPath } : {}),
      });
      setAttachments(data);
      const changedTarget = targetVideoPath && targetVideoPath !== attachments?.current_path;
      toast.success(changedTarget ? "已关联到所选视频" : "已关联到当前视频");
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "关联失败");
    }
  };

  const unlinkAttachment = async (path: string) => {
    try {
      const data = await api.post<AttachmentPayload>(`/api/items/${item.id}/attachments/unlink`, { path });
      setAttachments(data);
      toast.success("已取消手动关联");
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "取消关联失败");
    }
  };

  const search = async (page = 1) => {
    if (!query.trim()) return;
    const target = typeof page === "number" && page > 0 ? page : 1;
    setSearching(true);
    try {
      const result = await api.post<{ results: any[]; page?: number; total_pages?: number }>(
        `/api/items/${item.id}/search`,
        {
          query: query.trim(),
          media_type: mediaType,
          page: target,
        },
      );
      setResults(result.results);
      setSearchPage(result.page || target);
      setSearchPages(result.total_pages || 1);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "搜索失败");
    } finally {
      setSearching(false);
    }
  };

  const choose = async (candidate: any) => {
    setBusy(true);
    try {
      const updated = await api.post<MediaItem>(`/api/items/${item.id}/match`, {
        tmdb_id: candidate.tmdb_id,
        media_type: candidate.media_type,
        season: season === "" ? undefined : Number(season),
        episode: episode === "" ? undefined : Number(episode),
        status: "matched",
      });
      setDetail(updated);
      onUpdated(updated);
      toast.success(`已匹配到《${candidate.title}》`);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "匹配失败");
    } finally {
      setBusy(false);
    }
  };

  const patch = async (payload: Record<string, unknown>, message: string) => {
    setBusy(true);
    try {
      const updated = await api.put<MediaItem>(`/api/items/${item.id}`, payload);
      setDetail(updated);
      if ("target_name" in payload) {
        setTargetName(
          updated.target_name_override || updated.planned_name || updated.file_name || "",
        );
      }
      onUpdated(updated);
      toast.success(message);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "保存失败");
    } finally {
      setBusy(false);
    }
  };

  const rematch = async () => {
    setBusy(true);
    try {
      const updated = await api.post<MediaItem>(`/api/items/${item.id}/rematch`);
      setDetail(updated);
      onUpdated(updated);
      toast.success("已重新执行自动匹配");
      setResults(updated.candidates || null);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "重新匹配失败");
    } finally {
      setBusy(false);
    }
  };

  const skip = async () => {
    await api.post(`/api/items/${item.id}/skip`);
    await refresh();
    setConfirmSkip(false);
    toast.success("已忽略该文件");
  };

  const restore = async () => {
    await api.post(`/api/items/${item.id}/restore`);
    await refresh();
    toast.success("已恢复该文件");
  };

  const candidates = results || detail.candidates || [];

  return (
    <Drawer open={Boolean(item)} onClose={onClose} title="文件识别详情" width="max-w-4xl">
      <div className="space-y-5">
        <div className="flex flex-wrap items-start gap-5">
          <Poster
            url={detail.match?.poster_url}
            title={detail.match?.title || detail.parsed?.title}
            className="h-56 w-38 shrink-0"
            rounded="rounded-2xl"
          />
          <div className="min-w-0 flex-1 space-y-2.5">
            <div className="flex flex-wrap items-center gap-2">
              <StatusBadge status={detail.status} />
              {detail.manual ? <Badge tone="violet">手动指定</Badge> : null}
              {detail.parsed?.is_extra ? <Badge tone="amber">疑似花絮</Badge> : null}
            </div>
            <div>
              <div className="text-base font-semibold text-white">
                {detail.match?.title || detail.parsed?.title || "未识别标题"}
                {detail.match?.year ? <span className="ml-2 text-sm text-slate-400">({detail.match.year})</span> : null}
              </div>
              {detail.match?.original_title && detail.match.original_title !== detail.match.title ? (
                <div className="text-xs text-slate-500">{detail.match.original_title}</div>
              ) : null}
            </div>
            <div className="rounded-xl border border-white/[0.06] bg-white/[0.02] px-3.5 py-3 text-[11px] leading-relaxed text-slate-400">
              <div className="flex gap-2">
                <span className="h-fit shrink-0 select-none rounded bg-white/[0.06] px-1.5 py-px text-[10px] text-slate-500">源</span>
                <span className="min-w-0 break-all font-mono">{detail.source_path}</span>
              </div>
              {detail.target_path ? (
                <div className="mt-1.5 flex gap-2">
                  <span className="h-fit shrink-0 select-none rounded bg-mint-400/10 px-1.5 py-px text-[10px] text-mint-300">目标</span>
                  <span className="min-w-0 break-all font-mono text-mint-300/90">{detail.target_path}</span>
                </div>
              ) : null}
            </div>
            {detail.status !== "organized" && detail.match ? (
              <div className="rounded-xl border border-white/[0.06] bg-white/[0.02] px-3.5 py-3">
                <Field
                  label="整理后名称"
                  hint="保存后会覆盖当前文件的自动命名；之后整理或仅重命名都会使用这个名称。"
                >
                  <div className="flex flex-wrap items-center gap-2">
                    <Input
                      value={targetName}
                      onChange={(event) => setTargetName(event.target.value)}
                      placeholder={detail.planned_name || detail.file_name}
                      className="min-w-[220px] flex-1 font-mono text-[11px]"
                    />
                    <Button
                      className="px-2.5 py-1.5 text-xs"
                      loading={busy}
                      onClick={() => patch({ target_name: targetName }, "已保存整理后名称")}
                    >
                      保存名称
                    </Button>
                    <Button
                      className="px-2.5 py-1.5 text-xs"
                      onClick={() => {
                        setTargetName(detail.planned_name || detail.file_name || "");
                        patch({ target_name: "" }, "已恢复自动命名");
                      }}
                    >
                      恢复自动
                    </Button>
                  </div>
                </Field>
                {detail.target_name_override ? (
                  <div className="mt-1.5 text-[10px] text-mint-300/80">
                    当前使用手动名称；「恢复自动」会清除。
                  </div>
                ) : null}
              </div>
            ) : null}
            {detail.error ? (
              <div className="rounded-xl border border-rose-500/25 bg-rose-500/[0.08] px-3.5 py-2.5 text-[11px] text-rose-200">{detail.error}</div>
            ) : null}
            <div className="flex flex-wrap gap-2 text-[11px] text-slate-500">
              {detail.parsed?.resolution ? <Badge>{detail.parsed.resolution}</Badge> : null}
              {detail.parsed?.source ? <Badge>{detail.parsed.source}</Badge> : null}
              {detail.parsed?.video ? <Badge>{detail.parsed.video}</Badge> : null}
              {detail.parsed?.audio ? <Badge>{detail.parsed.audio}</Badge> : null}
              {detail.parsed?.group ? <Badge>{detail.parsed.group}</Badge> : null}
            </div>
          </div>
        </div>

        <AttachmentPanel
          data={attachments}
          loading={attachmentsLoading}
          onLink={linkAttachment}
          onUnlink={unlinkAttachment}
        />

        {/* 季集信息 */}
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
          <Field label="季号">
            <Input
              type="number"
              value={season}
              onChange={(event) => setSeason(event.target.value === "" ? "" : Number(event.target.value))}
              placeholder="—"
            />
          </Field>
          <Field label="集号">
            <Input
              type="number"
              value={episode}
              onChange={(event) => setEpisode(event.target.value === "" ? "" : Number(event.target.value))}
              placeholder="—"
            />
          </Field>
          <Field label="类型">
            <Select
              value={detail.match?.media_type || detail.parsed?.media_type || "movie"}
              onChange={(event) => patch({ media_type: event.target.value, season: season === "" ? undefined : season, episode: episode === "" ? undefined : episode }, "已保存")}
            >
              <option value="movie">电影</option>
              <option value="tv">剧集</option>
            </Select>
          </Field>
          <Field label="操作">
            <Button className="w-full" onClick={() => patch({ season: season === "" ? undefined : season, episode: episode === "" ? undefined : episode, status: detail.status === "unmatched" || detail.status === "failed" ? "review" : detail.status }, "季集信息已保存")}>
              <Check size={14} /> 保存季/集
            </Button>
          </Field>
        </div>

        {/* 自动匹配操作 */}
        <div className="flex flex-wrap gap-2">
          {detail.match && detail.status !== "organized" ? (
            <>
              <Button variant="primary" onClick={() => onPreview(detail.id)}>
                <Wand2 size={15} /> 整理这个文件
              </Button>
              <Button onClick={() => onRename(detail.id)}>
                <TextCursorInput size={15} /> 仅重命名
              </Button>
            </>
          ) : null}
          {detail.status === "review" && detail.match ? (
            <Button
              onClick={() => patch({ status: "matched" }, "已确认识别结果")}
              loading={busy}
            >
              <CheckCheck size={15} /> 确认识别
            </Button>
          ) : null}
          <Button onClick={rematch} loading={busy}>
            <RefreshCw size={15} /> 重新自动匹配
          </Button>
          {detail.status === "ignored" ? (
            <Button onClick={restore}>
              <Undo2 size={15} /> 取消忽略
            </Button>
          ) : (
            <Button onClick={() => setConfirmSkip(true)}>
              <SkipForward size={15} /> 忽略文件
            </Button>
          )}
        </div>

        {/* 搜索候选 */}
        <div className="space-y-3 border-t border-white/[0.06] pt-5">
          <div className="text-xs font-medium text-slate-300">手动搜索 TMDb</div>
          <div className="flex flex-wrap gap-2">
            <Input
              className="flex-1 min-w-[200px]"
              value={query}
              placeholder="输入电影 / 剧集名称，支持中英文"
              onChange={(event) => setQuery(event.target.value)}
              onKeyDown={(event) => event.key === "Enter" && void search()}
            />
            <Select className="w-28" value={mediaType} onChange={(event) => setMediaType(event.target.value)}>
              <option value="">全部</option>
              <option value="movie">电影</option>
              <option value="tv">剧集</option>
            </Select>
            <Button onClick={() => void search()} loading={searching}>
              <Search size={15} /> 搜索
            </Button>
          </div>

          <div className="space-y-2">
            {candidates.length === 0 ? (
              <p className="text-[11px] text-slate-500">没有候选结果。可以调整关键词或到设置中检查 TMDb API Key。</p>
            ) : (
              candidates.map((candidate: any) => {
                const isCurrent =
                  detail.match?.tmdb_id === candidate.tmdb_id && detail.match?.media_type === candidate.media_type;
                return (
                  <div
                    key={`${candidate.media_type}-${candidate.tmdb_id}`}
                    className={clsx(
                      "flex items-center gap-3 rounded-xl border px-3 py-2.5 transition",
                      isCurrent ? "border-mint-500/40 bg-mint-500/[0.07]" : "border-white/[0.06] bg-white/[0.02] hover:border-white/15",
                    )}
                  >
                    <Poster url={candidate.poster_url} title={candidate.title} rounded="rounded-lg" className="h-16 w-11 shrink-0" />
                    <div className="min-w-0 flex-1">
                      <div className="flex flex-wrap items-center gap-2">
                        <span className="truncate text-[13px] text-slate-100">{candidate.title}</span>
                        <Badge tone={candidate.media_type === "tv" ? "sky" : "mint"}>
                          {candidate.media_type === "tv" ? "剧集" : "电影"}
                        </Badge>
                        {candidate.year ? <span className="text-[11px] text-slate-500">{candidate.year}</span> : null}
                        {candidate.score != null ? (
                          <span className="text-[11px] text-slate-500">匹配度 {Math.round(candidate.score * 100)}%</span>
                        ) : null}
                      </div>
                      <div className="mt-0.5 line-clamp-2 text-[11px] text-slate-500">
                        {candidate.original_title && candidate.original_title !== candidate.title ? `${candidate.original_title} · ` : ""}
                        {candidate.overview || "暂无简介"}
                      </div>
                    </div>
                    <Button
                      variant={isCurrent ? "ghost" : "primary"}
                      className="px-2.5 py-1.5 text-[11px]"
                      loading={busy}
                      onClick={() => choose(candidate)}
                    >
                      {isCurrent ? "当前" : "选择"}
                    </Button>
                  </div>
                );
              })
            )}
          </div>
          {results !== null && searchPages > 1 ? (
            <div className="flex items-center justify-between text-[11px] text-slate-400">
              <Button
                className="px-2.5 py-1.5 text-[11px]"
                disabled={searching || searchPage <= 1}
                onClick={() => void search(searchPage - 1)}
              >
                上一页
              </Button>
              <span>
                第 {searchPage} / {searchPages} 页
              </span>
              <Button
                className="px-2.5 py-1.5 text-[11px]"
                disabled={searching || searchPage >= searchPages}
                onClick={() => void search(searchPage + 1)}
              >
                下一页
              </Button>
            </div>
          ) : null}
        </div>
      </div>

      <ConfirmDialog
        open={confirmSkip}
        title="忽略这个文件"
        message="忽略后该文件不再出现在待确认列表，但仍保留在数据库里，可以随时恢复。"
        confirmText="忽略"
        danger
        onConfirm={skip}
        onClose={() => setConfirmSkip(false)}
      />
    </Drawer>
  );
}

type BatchItem = {
  id: number;
  file_name: string;
  season?: number | null;
  episode?: number | null;
  media_type?: string;
};

type BatchMode = "offset" | "sequential" | "keep";

const BATCH_MODES: { value: BatchMode; label: string; hint: string }[] = [
  {
    value: "offset",
    label: "按偏移顺延",
    hint: "以第一个文件为基准整体平移：第一个设为第 5 集，其余按相同差值顺延（E01→E05、E02→E06…）。",
  },
  {
    value: "sequential",
    label: "从起始集依次排列",
    hint: "按文件顺序从起始集开始连续编号，适合缺失集或编号不连续的文件。",
  },
  {
    value: "keep",
    label: "保持原集数",
    hint: "只更换作品，季 / 集沿用文件名里识别出的结果。",
  },
];

/** 批量纠错：整组换作品 + 多选 + 集数偏移。 */
function BatchMatchModal({
  open,
  items,
  defaultMode = "offset",
  onClose,
  onApplied,
}: {
  open: boolean;
  items: BatchItem[];
  defaultMode?: BatchMode;
  onClose: () => void;
  onApplied: (count: number) => void;
}) {
  const toast = useToast();
  const [query, setQuery] = useState("");
  const [mediaType, setMediaType] = useState("tv");
  const [results, setResults] = useState<any[] | null>(null);
  const [searchPage, setSearchPage] = useState(1);
  const [searchPages, setSearchPages] = useState(1);
  const [chosen, setChosen] = useState<any | null>(null);
  const [seasons, setSeasons] = useState<any[]>([]);
  const [season, setSeason] = useState<number>(1);
  const [startEpisode, setStartEpisode] = useState<number>(1);
  const [mode, setMode] = useState<BatchMode>(defaultMode);
  const [episodes, setEpisodes] = useState<any[]>([]);
  const [searching, setSearching] = useState(false);
  const [busy, setBusy] = useState(false);
  const first = items[0];

  useEffect(() => {
    if (!open) return;
    setQuery("");
    setResults(null);
    setSearchPage(1);
    setSearchPages(1);
    setChosen(null);
    setSeasons([]);
    setEpisodes([]);
    setSearching(false);
    setBusy(false);
    setMediaType(items.some((item) => item.media_type === "tv") ? "tv" : "movie");
    setSeason(first?.season || 1);
    setStartEpisode(first?.episode || 1);
    setMode(defaultMode);
  }, [open, items, defaultMode]);

  useEffect(() => {
    if (!chosen || chosen.media_type !== "tv") {
      setEpisodes([]);
      return;
    }
    let alive = true;
    api
      .get<{ episodes: any[] }>(`/api/items/tmdb/tv/${chosen.tmdb_id}/season/${season}`)
      .then((data) => {
        if (alive) setEpisodes(data.episodes || []);
      })
      .catch(() => {
        if (alive) setEpisodes([]);
      });
    return () => {
      alive = false;
    };
  }, [chosen, season]);

  const search = async (page = 1) => {
    const target = typeof page === "number" && page > 0 ? page : 1;
    if (!query.trim()) return;
    setSearching(true);
    try {
      const result = await api.post<{ results: any[]; page?: number; total_pages?: number }>(
        "/api/items/search",
        {
          query: query.trim(),
          media_type: mediaType,
          page: target,
        },
      );
      setResults(result.results);
      setSearchPage(result.page || target);
      setSearchPages(result.total_pages || 1);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "搜索失败");
    } finally {
      setSearching(false);
    }
  };

  const choose = async (candidate: any) => {
    setChosen(candidate);
    setResults(null);
    if (candidate.media_type !== "tv") {
      setSeasons([]);
      return;
    }
    try {
      const details = await api.get<any>(`/api/items/tmdb/tv/${candidate.tmdb_id}`);
      const list = Array.isArray(details.seasons) ? details.seasons : [];
      setSeasons(list);
      const preferred = first?.season;
      const matched = list.find((entry: any) => entry.season_number === preferred);
      const fallback = list.find((entry: any) => entry.season_number === 1) || list[0];
      setSeason(Number(matched?.season_number ?? fallback?.season_number ?? preferred ?? 1));
    } catch {
      setSeasons([]);
    }
  };

  const tvTarget = chosen?.media_type === "tv";
  const anchorEpisode = first?.episode || 1;
  const delta = startEpisode - anchorEpisode;
  const rows = items.map((item, index) => {
    if (!tvTarget) {
      return { ...item, newSeason: null as number | null, newEpisode: null as number | null };
    }
    const oldSeason = item.season ?? null;
    const oldEpisode = item.episode ?? null;
    if (mode === "keep") {
      return { ...item, newSeason: oldSeason, newEpisode: oldEpisode ?? 1 };
    }
    const baseEpisode = oldEpisode ?? anchorEpisode + index;
    const newEpisode = mode === "sequential" ? startEpisode + index : baseEpisode + delta;
    return { ...item, newSeason: season, newEpisode };
  });
  const invalid = tvTarget && rows.some((row) => !row.newEpisode || row.newEpisode < 1);
  const episodeName = (number: number | null) =>
    number ? episodes.find((entry) => entry.episode_number === number)?.name || "" : "";

  const apply = async () => {
    if (!chosen) return;
    setBusy(true);
    try {
      const result = await api.post<{ updated: number }>("/api/items/batch-match", {
        tmdb_id: chosen.tmdb_id,
        media_type: chosen.media_type,
        items: rows.map((row) => ({
          item_id: row.id,
          ...(tvTarget
            ? { season: row.newSeason ?? undefined, episode: row.newEpisode ?? undefined }
            : {}),
        })),
      });
      toast.success(`已把 ${result.updated} 个文件更新为《${chosen.title}》`);
      onApplied(result.updated);
      onClose();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "批量纠错失败");
    } finally {
      setBusy(false);
    }
  };

  return (
    <Modal
      open={open}
      onClose={onClose}
      title={`批量纠错（${items.length} 个文件）`}
      width="max-w-3xl"
      footer={
        <>
          <Button onClick={onClose}>取消</Button>
          <Button variant="primary" loading={busy} disabled={!chosen || invalid} onClick={apply}>
            <Check size={15} /> 应用纠错
          </Button>
        </>
      }
    >
      <div className="space-y-4">
        {!chosen ? (
          <>
            <div className="flex flex-wrap gap-2">
              <Input
                className="min-w-[220px] flex-1"
                value={query}
                placeholder="搜索正确的电影 / 剧集名称，支持中英文"
                onChange={(event) => setQuery(event.target.value)}
                onKeyDown={(event) => event.key === "Enter" && void search()}
              />
              <Select className="w-28" value={mediaType} onChange={(event) => setMediaType(event.target.value)}>
                <option value="">全部</option>
                <option value="movie">电影</option>
                <option value="tv">剧集</option>
              </Select>
              <Button onClick={() => void search()} loading={searching}>
                <Search size={15} /> 搜索
              </Button>
            </div>
            {results === null ? (
              <p className="text-[11px] leading-relaxed text-slate-500">
                选中多个文件后，可以一次把它们改成同一部作品；剧集还能设置季与起始集，其余文件按偏移量自动顺延。
              </p>
            ) : results.length === 0 ? (
              <p className="text-[11px] text-slate-500">没有找到结果，换个关键词或切换类型再试。</p>
            ) : (
              <>
              <div className="max-h-80 space-y-2 overflow-y-auto pr-1">
                {results.map((candidate: any) => (
                  <div
                    key={`${candidate.media_type}-${candidate.tmdb_id}`}
                    className="flex items-center gap-3 rounded-xl border border-white/[0.06] bg-white/[0.02] px-3 py-2.5"
                  >
                    <Poster url={candidate.poster_url} title={candidate.title} rounded="rounded-lg" className="h-16 w-11 shrink-0" />
                    <div className="min-w-0 flex-1">
                      <div className="flex flex-wrap items-center gap-2">
                        <span className="truncate text-[13px] text-slate-100">{candidate.title}</span>
                        <Badge tone={candidate.media_type === "tv" ? "sky" : "mint"}>
                          {candidate.media_type === "tv" ? "剧集" : "电影"}
                        </Badge>
                        {candidate.year ? <span className="text-[11px] text-slate-500">{candidate.year}</span> : null}
                      </div>
                      <div className="mt-0.5 line-clamp-2 text-[11px] text-slate-500">
                        {candidate.original_title && candidate.original_title !== candidate.title
                          ? `${candidate.original_title} · `
                          : ""}
                        {candidate.overview || "暂无简介"}
                      </div>
                    </div>
                    <Button variant="primary" className="px-2.5 py-1.5 text-[11px]" onClick={() => choose(candidate)}>
                      选择
                    </Button>
                  </div>
                ))}
              </div>
              {searchPages > 1 ? (
                <div className="flex items-center justify-between text-[11px] text-slate-400">
                  <Button
                    className="px-2.5 py-1.5 text-[11px]"
                    disabled={searching || searchPage <= 1}
                    onClick={() => void search(searchPage - 1)}
                  >
                    上一页
                  </Button>
                  <span>
                    第 {searchPage} / {searchPages} 页
                  </span>
                  <Button
                    className="px-2.5 py-1.5 text-[11px]"
                    disabled={searching || searchPage >= searchPages}
                    onClick={() => void search(searchPage + 1)}
                  >
                    下一页
                  </Button>
                </div>
              ) : null}
              </>
            )}
          </>
        ) : (
          <>
            <div className="flex items-center gap-3 rounded-xl border border-mint-500/30 bg-mint-500/[0.06] px-3 py-2.5">
              <Poster url={chosen.poster_url} title={chosen.title} rounded="rounded-lg" className="h-16 w-11 shrink-0" />
              <div className="min-w-0 flex-1">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="truncate text-[13px] text-slate-100">{chosen.title}</span>
                  <Badge tone={chosen.media_type === "tv" ? "sky" : "mint"}>
                    {chosen.media_type === "tv" ? "剧集" : "电影"}
                  </Badge>
                  {chosen.year ? <span className="text-[11px] text-slate-500">{chosen.year}</span> : null}
                </div>
                <div className="mt-0.5 line-clamp-1 text-[11px] text-slate-500">
                  {chosen.original_title && chosen.original_title !== chosen.title
                    ? `${chosen.original_title} · `
                    : ""}
                  {chosen.overview || "暂无简介"}
                </div>
              </div>
              <Button className="px-2 py-1 text-[11px]" onClick={() => setChosen(null)}>
                重新选择
              </Button>
            </div>

            {tvTarget ? (
              <div className="space-y-3 rounded-xl border border-white/[0.06] bg-white/[0.02] px-3.5 py-3">
                <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
                  <span className="text-[11px] text-slate-500">季：</span>
                  <Select
                    className="w-44"
                    value={String(season)}
                    disabled={mode === "keep"}
                    onChange={(event) => setSeason(Number(event.target.value))}
                  >
                    {(seasons.length ? seasons : [{ season_number: season }]).map((entry: any) => (
                      <option key={entry.season_number} value={String(entry.season_number)}>
                        {entry.season_number === 0
                          ? `特别篇${entry.episode_count ? ` · ${entry.episode_count} 集` : ""}`
                          : `第 ${entry.season_number} 季${entry.episode_count ? ` · ${entry.episode_count} 集` : ""}`}
                      </option>
                    ))}
                  </Select>
                  <span className="text-[11px] text-slate-500">起始集：</span>
                  <Input
                    type="number"
                    className="w-20"
                    value={startEpisode}
                    disabled={mode === "keep"}
                    onChange={(event) => setStartEpisode(Number(event.target.value) || 1)}
                  />
                  <span className="text-[10px] text-slate-500">
                    第一个文件（{seasonEpisode(first?.season, first?.episode) || "无季集"}）对应这一集
                  </span>
                </div>
                <div className="flex flex-wrap gap-1.5">
                  {BATCH_MODES.map((entry) => {
                    const active = mode === entry.value;
                    return (
                      <button
                        key={entry.value}
                        type="button"
                        onClick={() => setMode(entry.value)}
                        className={`rounded-lg border px-2 py-1 text-[11px] transition ${
                          active
                            ? "border-mint-400/40 bg-mint-400/10 text-mint-200"
                            : "border-white/[0.08] text-slate-400 hover:text-slate-200"
                        }`}
                      >
                        {entry.label}
                      </button>
                    );
                  })}
                </div>
                <p className="text-[10px] leading-relaxed text-slate-500">
                  {BATCH_MODES.find((entry) => entry.value === mode)?.hint}
                </p>
              </div>
            ) : null}

            <div className="overflow-hidden rounded-xl border border-white/[0.06]">
              <div className="max-h-72 overflow-y-auto">
                <table className="w-full text-left text-[11px]">
                  <thead className="sticky top-0 bg-ink-900">
                    <tr className="border-b border-white/[0.06] text-[10px] text-slate-500">
                      <th className="px-3 py-2 font-normal">文件</th>
                      <th className="px-3 py-2 font-normal">原季集</th>
                      <th className="px-3 py-2 font-normal">纠错后</th>
                    </tr>
                  </thead>
                  <tbody>
                    {rows.map((row) => (
                      <tr key={row.id} className="border-b border-white/[0.04] last:border-0">
                        <td className="max-w-[320px] px-3 py-2">
                          <div className="truncate text-slate-300" title={row.file_name}>
                            {row.file_name}
                          </div>
                        </td>
                        <td className="whitespace-nowrap px-3 py-2 font-mono text-slate-500">
                          {seasonEpisode(row.season, row.episode) || "—"}
                        </td>
                        <td className="px-3 py-2">
                          {tvTarget ? (
                            <div className="flex flex-wrap items-center gap-2">
                              <span
                                className={clsx(
                                  "whitespace-nowrap font-mono",
                                  !row.newEpisode || row.newEpisode < 1 ? "text-rose-300" : "text-mint-200",
                                )}
                              >
                                {seasonEpisode(row.newSeason, row.newEpisode) || "—"}
                              </span>
                              {episodeName(row.newEpisode) ? (
                                <span className="truncate text-slate-500">{episodeName(row.newEpisode)}</span>
                              ) : null}
                            </div>
                          ) : (
                            <span className="text-mint-200">{chosen.title}</span>
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
            {invalid ? (
              <p className="text-[11px] text-rose-300">有文件的集号小于 1，请调整起始集或改用「保持原集数」。</p>
            ) : null}
          </>
        )}
      </div>
    </Modal>
  );
}

const DEFAULT_MOVIE_FILE = "{title} ({year}){release}";
const DEFAULT_EPISODE_FILE = "{title} S{season:02d}E{episode:02d}{episode_title}{release}";
const DEFAULT_TAG_ORDER = [
  "resolution",
  "hdr",
  "source",
  "edition",
  "release_notes",
  "video",
  "audio",
  "group",
];
const CUSTOM_TAG_PREFIX = "custom:";
const DEFAULT_TMDB_TEMPLATE = "{tmdbid-{id}}";

// 与 Filmix 附加信息表对齐：分辨率 / 视频质量 / 来源 / 影视版本 / 发布标签 / 视频编码 / 音频信息 / 发布组
const TAG_FIELD_LABELS: Array<[string, string]> = [
  ["resolution", "分辨率"],
  ["hdr", "视频质量"],
  ["source", "来源"],
  ["edition", "影视版本"],
  ["release_notes", "发布标签"],
  ["video", "视频编码"],
  ["audio", "音频信息"],
  ["group", "发布组"],
];

const SEPARATOR_OPTIONS = [
  { value: ".", label: "符号 .（推荐）" },
  { value: "-", label: "符号 -" },
  { value: "_", label: "符号 _" },
  { value: " ", label: "空格" },
];

const SEASON_STYLES = [
  { label: "S01E02", template: "S{season:02d}E{episode:02d}", recommended: true },
  { label: "s01e02", template: "s{season:02d}e{episode:02d}", recommended: false },
  { label: "s1e2", template: "s{season}e{episode}", recommended: false },
  { label: "1x02", template: "{season}x{episode:02d}", recommended: false },
  { label: "s01.e02", template: "s{season:02d}.e{episode:02d}", recommended: false },
  { label: "season1.episode2", template: "season{season}.episode{episode}", recommended: false },
];

type FolderStyle = { label: string; template: string; recommended?: boolean };

/** 作品文件夹：点选式，避免在界面上暴露模板占位符。 */
function workFolderStyles(name: string): FolderStyle[] {
  return [
    { label: `${name} (年份)`, template: "{title} ({year})", recommended: true },
    { label: name, template: "{title}" },
    { label: `${name} 年份`, template: "{title} {year}" },
  ];
}

const SEASON_FOLDER_STYLES: FolderStyle[] = [
  { label: "Season 1", template: "Season {season}", recommended: true },
  { label: "S1", template: "S{season}" },
  { label: "第1季", template: "第{season}季" },
  { label: "不建季文件夹", template: "" },
];

/** 文件夹命名选择器：推荐样式点选，特殊需求才展开自定义输入。 */
function FolderStylePicker({
  styles,
  value,
  onChange,
  custom,
  onCustom,
}: {
  styles: FolderStyle[];
  value: string;
  onChange: (value: string) => void;
  custom: boolean;
  onCustom: (value: boolean) => void;
}) {
  return (
    <div className="space-y-2">
      <div className="flex flex-wrap gap-1.5">
        {styles.map((item) => {
          const active = !custom && value === item.template;
          return (
            <button
              key={item.label}
              type="button"
              onClick={() => {
                onCustom(false);
                onChange(item.template);
              }}
              className={`flex items-center gap-1 rounded-lg border px-2 py-1 text-[11px] transition ${
                active
                  ? "border-mint-400/40 bg-mint-400/10 text-mint-200"
                  : "border-white/[0.08] text-slate-400 hover:text-slate-200"
              }`}
            >
              {item.label}
              {item.recommended ? (
                <span className="rounded bg-mint-400/15 px-1 text-[9px] text-mint-300">推荐</span>
              ) : null}
            </button>
          );
        })}
        <button
          type="button"
          onClick={() => onCustom(true)}
          className={`rounded-lg border px-2 py-1 text-[11px] transition ${
            custom
              ? "border-mint-400/40 bg-mint-400/10 text-mint-200"
              : "border-white/[0.08] text-slate-400 hover:text-slate-200"
          }`}
        >
          自定义…
        </button>
      </div>
      {custom ? (
        <Input
          value={value}
          placeholder="支持多级目录，如 电影/欧美电影"
          onChange={(event) => onChange(event.target.value)}
        />
      ) : null}
    </div>
  );
}

const TMDB_TAG_STYLES = [
  { label: "{tmdbid-1668}", template: "{tmdbid-{id}}", recommended: true },
  { label: "[tmdbid-1668]", template: "[tmdbid-{id}]", recommended: false },
  { label: "{tmdbid=1668}", template: "{tmdbid={id}}", recommended: false },
  { label: "[tmdbid=1668]", template: "[tmdbid={id}]", recommended: false },
];

type YearStyle = "paren" | "plain" | "none";
type InfoMode = "release" | "tags" | "none";

type InfoOptions = {
  mode: InfoMode;
  /** 勾选的附加信息字段（含 custom: 开头的自定义条目），数组顺序即拼接顺序 */
  fields: string[];
  separator: string;
};

type NameOptions = {
  movieYear: YearStyle;
  tvYear: YearStyle;
  seasonTemplate: string;
  episodeTitle: boolean;
  movieInfo: InfoOptions;
  tvInfo: InfoOptions;
  tmdbOn: boolean;
  tmdbTemplate: string;
};

const DEFAULT_INFO_OPTIONS: InfoOptions = {
  mode: "release",
  fields: [...DEFAULT_TAG_ORDER],
  separator: ".",
};

const DEFAULT_NAME_OPTIONS: NameOptions = {
  movieYear: "paren",
  tvYear: "none",
  seasonTemplate: "S{season:02d}E{episode:02d}",
  episodeTitle: true,
  movieInfo: { ...DEFAULT_INFO_OPTIONS, fields: [...DEFAULT_TAG_ORDER] },
  tvInfo: { ...DEFAULT_INFO_OPTIONS, fields: [...DEFAULT_TAG_ORDER] },
  tmdbOn: false,
  tmdbTemplate: DEFAULT_TMDB_TEMPLATE,
};

const INFO_MODE_OPTIONS: Array<[InfoMode, string, string]> = [
  [
    "release",
    "沿用原发布信息",
    "直接保留原文件名里的画质、音轨等信息，保留原始大小写，观感最自然（推荐）。",
  ],
  [
    "tags",
    "自选信息拼接",
    "从原文件名中识别并提取勾选的信息重新拼接，同样保留原始大小写，如 UHD.BluRay.REMUX、DTS-HD.MA、年糕。",
  ],
  ["none", "不添加附加信息", "文件名只保留标题、年份、季集等内容。"],
];

const YEAR_SEGMENTS: Record<YearStyle, string> = {
  paren: " ({year})",
  plain: " {year}",
  none: "",
};

/** 把库里存的命名模板反解成可视化选项；解析不了（真正自定义的模板）返回 null。 */
function decodeNameOptions(naming: any): NameOptions | null {
  const movie = String(naming?.movie_file || DEFAULT_MOVIE_FILE);
  const episode = String(naming?.episode_file || DEFAULT_EPISODE_FILE);

  const decodeTech = (tail: string): InfoMode | null => {
    if (tail === "{release}") return "release";
    if (tail === "{tags}") return "tags";
    if (tail === "") return "none";
    return null;
  };
  const decodeYear = (value: string): { style: YearStyle; rest: string } | null => {
    if (value.startsWith(" ({year})")) {
      return { style: "paren", rest: value.slice(" ({year})".length) };
    }
    if (value.startsWith(" {year}")) {
      return { style: "plain", rest: value.slice(" {year}".length) };
    }
    return { style: "none", rest: value };
  };
  // 电影 / 剧集的附加信息字段与拼接符各自独立存储，缺省回退到旧版 tag_order / tag_separator
  const decodeInfo = (kind: "movie" | "tv", mode: InfoMode): InfoOptions => {
    const stored = naming?.[`tag_fields_${kind}`];
    const raw = Array.isArray(stored)
      ? stored
      : Array.isArray(naming?.tag_order)
        ? naming.tag_order
        : [];
    const fields = Array.from(
      new Set<string>(
        raw.filter(
          (key: any) =>
            typeof key === "string" &&
            (TAG_FIELD_LABELS.some(([id]) => id === key) || key.startsWith(CUSTOM_TAG_PREFIX)),
        ),
      ),
    );
    return {
      mode,
      fields: fields.length ? fields : [...DEFAULT_TAG_ORDER],
      separator: String(naming?.[`tag_separator_${kind}`] ?? naming?.tag_separator ?? "."),
    };
  };

  let rest = movie;
  if (!rest.startsWith("{title}")) return null;
  rest = rest.slice("{title}".length);
  const movieYear = decodeYear(rest);
  if (!movieYear) return null;
  const movieTech = decodeTech(movieYear.rest);
  if (movieTech === null) return null;

  let tv = episode;
  if (!tv.startsWith("{title}")) return null;
  tv = tv.slice("{title}".length);
  const tvYear = decodeYear(tv);
  if (!tvYear) return null;
  tv = tvYear.rest;
  if (!tv.startsWith(" - ")) {
    if (!tv.startsWith(" ")) return null;
    tv = tv.slice(1);
  } else {
    tv = tv.slice(3);
  }
  const season = SEASON_STYLES.find((item) => tv.startsWith(item.template));
  if (!season) return null;
  tv = tv.slice(season.template.length);
  let episodeTitle = false;
  if (tv.startsWith("{episode_title}")) {
    episodeTitle = true;
    tv = tv.slice("{episode_title}".length);
  }
  const tvTech = decodeTech(tv);
  if (tvTech === null) return null;

  return {
    movieYear: movieYear.style,
    tvYear: tvYear.style,
    seasonTemplate: season.template,
    episodeTitle,
    movieInfo: decodeInfo("movie", movieTech),
    tvInfo: decodeInfo("tv", tvTech),
    tmdbOn: Boolean(naming?.include_tmdb_id),
    tmdbTemplate: String(naming?.tmdb_id_template || DEFAULT_TMDB_TEMPLATE),
  };
}

/** 由可视化选项生成电影 / 剧集文件名模板。 */
function buildFileTemplates(options: NameOptions): { movie_file: string; episode_file: string } {
  const segment = (info: InfoOptions) =>
    info.mode === "release" ? "{release}" : info.mode === "tags" ? "{tags}" : "";
  const movie_file = `{title}${YEAR_SEGMENTS[options.movieYear]}${segment(options.movieInfo)}`;
  const episode_file = `{title}${YEAR_SEGMENTS[options.tvYear]} ${options.seasonTemplate}${
    options.episodeTitle ? "{episode_title}" : ""
  }${segment(options.tvInfo)}`;
  return { movie_file, episode_file };
}

/** 预览用示例：原名（带扩展名）+ 解析后的字段值，模拟真实文件的解析结果。 */
type PreviewSample = {
  original: string;
  extension: string;
  values: Record<string, string>;
};

const PREVIEW_MOVIE_SAMPLE: PreviewSample = {
  original: "盗梦空间.2010.2160p.Dolby.BluRay.DC.PROPER.x265.AC3.EVO.mp4",
  extension: ".mp4",
  values: {
    title: "盗梦空间",
    original_title: "Inception",
    year: "2010",
    release: " 2160p.Dolby.BluRay.DC.PROPER.x265.AC3.EVO",
    resolution: "2160p",
    hdr: "Dolby",
    source: "BluRay",
    edition: "DC",
    release_notes: "PROPER",
    video: "x265",
    audio: "AC3",
    group: "EVO",
    tmdb_id: "27205",
  },
};

const PREVIEW_TV_SAMPLE: PreviewSample = {
  original: "老友记.1994.S02E03.2160p.Dolby.BluRay.DC.PROPER.x265.AC3.EVO.mp4",
  extension: ".mp4",
  values: {
    title: "老友记",
    original_title: "Friends",
    year: "1994",
    season: "02",
    episode: "03",
    episode_title: " - 海克尔斯先生去世",
    release: ".2160p.Dolby.BluRay.DC.PROPER.x265.AC3.EVO",
    resolution: "2160p",
    hdr: "Dolby",
    source: "BluRay",
    edition: "DC",
    release_notes: "PROPER",
    video: "x265",
    audio: "AC3",
    group: "EVO",
    tmdb_id: "1668",
  },
};

/** 用示例数据渲染命名模板，供实时预览。 */
function renderPreview(template: string, sample: Record<string, string>, tagsValue: string): string {
  return (template || "").replace(/\{([a-z_]+)(?::[^}]*)?\}/g, (whole, key: string) => {
    if (key === "tags") {
      return tagsValue;
    }
    return key in sample ? sample[key] : whole;
  });
}

/**
 * 附加信息编辑器：字段勾选 + 显示顺序 + 拼接符 + 自定义条目。
 * 电影与剧集各实例化一份，互不影响。
 */
function InfoEditor({
  info,
  onChange,
}: {
  info: InfoOptions;
  onChange: (patch: Partial<InfoOptions>) => void;
}) {
  const [draft, setDraft] = useState("");
  const customFields = info.fields.filter((key) => key.startsWith(CUSTOM_TAG_PREFIX));

  const labelOf = (key: string) =>
    key.startsWith(CUSTOM_TAG_PREFIX)
      ? key.slice(CUSTOM_TAG_PREFIX.length)
      : TAG_FIELD_LABELS.find(([id]) => id === key)?.[1] || key;

  const toggleField = (key: string) => {
    const has = info.fields.includes(key);
    onChange({ fields: has ? info.fields.filter((item) => item !== key) : [...info.fields, key] });
  };

  const moveField = (key: string, delta: number) => {
    const fields = [...info.fields];
    const index = fields.indexOf(key);
    const target = index + delta;
    if (index < 0 || target < 0 || target >= fields.length) return;
    [fields[index], fields[target]] = [fields[target], fields[index]];
    onChange({ fields });
  };

  const addCustom = () => {
    const text = draft.trim();
    if (!text) return;
    const key = CUSTOM_TAG_PREFIX + text;
    if (!info.fields.includes(key)) {
      onChange({ fields: [...info.fields, key] });
    }
    setDraft("");
  };

  return (
    <div className="mt-2 space-y-3 rounded-lg bg-white/[0.04] px-3 py-2.5">
      <div className="flex flex-wrap gap-1.5">
        {TAG_FIELD_LABELS.map(([id, label]) => {
          const checked = info.fields.includes(id);
          return (
            <label
              key={id}
              className={`flex cursor-pointer items-center gap-1.5 rounded-lg border px-2 py-1 text-[11px] transition ${
                checked
                  ? "border-mint-400/40 bg-mint-400/10 text-mint-200"
                  : "border-white/[0.08] text-slate-400 hover:text-slate-200"
              }`}
            >
              <input
                type="checkbox"
                className="accent-mint-400"
                checked={checked}
                onChange={() => toggleField(id)}
              />
              {label}
            </label>
          );
        })}
        {customFields.map((key) => (
          <span
            key={key}
            className="flex items-center gap-1 rounded-lg border border-mint-400/40 bg-mint-400/10 px-2 py-1 text-[11px] text-mint-200"
          >
            {labelOf(key)}
            <button
              type="button"
              className="text-mint-300/70 transition hover:text-white"
              title="移除自定义信息"
              onClick={() => toggleField(key)}
            >
              ✕
            </button>
          </span>
        ))}
      </div>
      <p className="text-[10px] leading-relaxed text-slate-500">
        信息直接取自原文件名并保留原始大小写；识别不到的信息会自动省略。
        影视版本、发布标签、发布组的识别词条可在「设置 → 附加信息词库」中增删。
      </p>
      {info.fields.length > 1 ? (
        <div className="flex flex-wrap items-center gap-1.5">
          <span className="text-[11px] text-slate-500">显示顺序：</span>
          {info.fields.map((key, index) => (
            <span
              key={key}
              className="inline-flex items-center gap-1 rounded-md bg-white/[0.06] px-1.5 py-0.5 text-[11px] text-slate-300"
            >
              {labelOf(key)}
              <button
                type="button"
                className="text-slate-500 transition hover:text-mint-300 disabled:opacity-30"
                disabled={index === 0}
                onClick={() => moveField(key, -1)}
              >
                ↑
              </button>
              <button
                type="button"
                className="text-slate-500 transition hover:text-mint-300 disabled:opacity-30"
                disabled={index === info.fields.length - 1}
                onClick={() => moveField(key, 1)}
              >
                ↓
              </button>
            </span>
          ))}
        </div>
      ) : null}
      <div className="flex flex-wrap items-center gap-3">
        <div className="flex items-center gap-2">
          <span className="shrink-0 text-[11px] text-slate-500">拼接符：</span>
          <div className="w-40">
            <Select
              value={info.separator}
              onChange={(event) => onChange({ separator: event.target.value })}
            >
              {SEPARATOR_OPTIONS.map((item) => (
                <option key={item.value} value={item.value}>
                  {item.label}
                </option>
              ))}
            </Select>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <div className="w-44">
            <Input
              value={draft}
              placeholder="增加附加信息，如 国语中字"
              onChange={(event) => setDraft(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter") {
                  event.preventDefault();
                  addCustom();
                }
              }}
            />
          </div>
          <Button onClick={addCustom}>添加</Button>
        </div>
      </div>
    </div>
  );
}

function NamingModal({
  open,
  library,
  onClose,
  onSaved,
}: {
  open: boolean;
  library: LibraryType;
  onClose: () => void;
  onSaved: (library: LibraryType) => void;
}) {
  const toast = useToast();
  const [naming, setNaming] = useState<any>(library.naming);
  const [scrape, setScrape] = useState<any>(library.scrape);
  const [saving, setSaving] = useState(false);
  const [options, setOptions] = useState<NameOptions | null>(() => decodeNameOptions(library.naming));
  const [customMode, setCustomMode] = useState<boolean>(
    () => decodeNameOptions(library.naming) === null,
  );
  const [structure, setStructure] = useState<{ name: string; children: string[] }[] | null>(null);
  const [structureLoading, setStructureLoading] = useState(false);
  const [nameTab, setNameTab] = useState<"movie" | "tv">("movie");
  const [folderCustom, setFolderCustom] = useState({ movie: false, tv: false, season: false });
  const scrapeEnabled = Boolean(scrape.enabled);

  useEffect(() => {
    setNaming(library.naming);
    setScrape(library.scrape);
    const decoded = decodeNameOptions(library.naming);
    setOptions(decoded);
    setCustomMode(decoded === null);
    setNameTab("movie");
    setFolderCustom({ movie: false, tv: false, season: false });
  }, [library.id, open]);

  useEffect(() => {
    if (!open || naming.auto_category === false) {
      return;
    }
    let alive = true;
    setStructureLoading(true);
    api
      .get<{ target_root: string; tree: { name: string; children: string[] }[] }>(
        `/api/libraries/${library.id}/target-structure`,
      )
      .then((data) => {
        if (alive) {
          setStructure(data.tree || []);
        }
      })
      .catch(() => {
        if (alive) {
          setStructure(null);
        }
      })
      .finally(() => {
        if (alive) {
          setStructureLoading(false);
        }
      });
    return () => {
      alive = false;
    };
  }, [open, library.id, naming.auto_category]);

  const updateOptions = (patch: Partial<NameOptions>) =>
    setOptions((current) => (current ? { ...current, ...patch } : current));

  const updateMovieInfo = (patch: Partial<InfoOptions>) =>
    setOptions((current) =>
      current ? { ...current, movieInfo: { ...current.movieInfo, ...patch } } : current,
    );

  const updateTvInfo = (patch: Partial<InfoOptions>) =>
    setOptions((current) =>
      current ? { ...current, tvInfo: { ...current.tvInfo, ...patch } } : current,
    );

  const generated = options ? buildFileTemplates(options) : null;
  const movieTemplate = customMode ? naming.movie_file || "" : generated?.movie_file || "";
  const episodeTemplate = customMode ? naming.episode_file || "" : generated?.episode_file || "";

  const effectiveEpisodeTitle = customMode
    ? naming.episode_title !== false
    : Boolean(options?.episodeTitle);
  const effectiveTmdbOn = customMode ? Boolean(naming.include_tmdb_id) : Boolean(options?.tmdbOn);
  const effectiveTmdbTemplate = customMode
    ? String(naming.tmdb_id_template || DEFAULT_TMDB_TEMPLATE)
    : String(options?.tmdbTemplate || DEFAULT_TMDB_TEMPLATE);

  const previewTagsValue = (info: InfoOptions, sample: Record<string, string>, lead: string) => {
    const parts = info.fields
      .map((key) =>
        key.startsWith(CUSTOM_TAG_PREFIX)
          ? key.slice(CUSTOM_TAG_PREFIX.length)
          : sample[key] || "",
      )
      .filter(Boolean);
    return parts.length ? lead + parts.join(info.separator) : "";
  };

  const storedInfoFor = (kind: "movie" | "tv"): InfoOptions => {
    const stored = naming?.[`tag_fields_${kind}`];
    const raw = Array.isArray(stored)
      ? stored
      : Array.isArray(naming?.tag_order) && naming.tag_order.length
        ? naming.tag_order
        : DEFAULT_TAG_ORDER;
    return {
      mode: "tags",
      fields: raw.filter((key: any) => typeof key === "string"),
      separator: String(naming?.[`tag_separator_${kind}`] ?? naming?.tag_separator ?? "."),
    };
  };

  const buildPreview = (
    template: string,
    sample: PreviewSample,
    withEpisodeTitle: boolean,
    info: InfoOptions,
    lead: string,
  ) => {
    if (!template.trim()) return "";
    const values = {
      ...sample.values,
      episode_title: withEpisodeTitle ? sample.values.episode_title || "" : "",
    };
    let name = renderPreview(template, values, previewTagsValue(info, sample.values, lead));
    const tail = /\{(release|tags|resolution|hdr|source|edition|release_notes|video|audio|group)\}\s*$/.exec(
      template,
    );
    const tailKey = tail?.[1];
    const tailValue =
      tailKey === "tags"
        ? previewTagsValue(info, sample.values, lead)
        : tailKey
          ? sample.values[tailKey] || ""
          : "";
    if (effectiveTmdbOn) {
      const joiner = tailValue.trim() ? "." : " ";
      name = `${name}${joiner}${effectiveTmdbTemplate.replace("{id}", sample.values.tmdb_id)}`;
    }
    return name ? `${name}${sample.extension}` : "";
  };

  const effectiveMovieInfo: InfoOptions =
    customMode || !options ? storedInfoFor("movie") : options.movieInfo;
  const effectiveTvInfo: InfoOptions =
    customMode || !options ? storedInfoFor("tv") : options.tvInfo;

  const moviePreview = buildPreview(
    movieTemplate,
    PREVIEW_MOVIE_SAMPLE,
    false,
    effectiveMovieInfo,
    " ",
  );
  const tvPreview = buildPreview(
    episodeTemplate,
    PREVIEW_TV_SAMPLE,
    effectiveEpisodeTitle,
    effectiveTvInfo,
    effectiveTvInfo.separator,
  );

  const resetDefaults = () => {
    setNaming({
      movie_folder: "{title} ({year})",
      tv_folder: "{title} ({year})",
      season_folder: "Season {season}",
      title_language: "zh",
      cleanup_empty_dirs: false,
      auto_category: true,
    });
    setOptions({
      ...DEFAULT_NAME_OPTIONS,
      movieInfo: { ...DEFAULT_NAME_OPTIONS.movieInfo, fields: [...DEFAULT_TAG_ORDER] },
      tvInfo: { ...DEFAULT_NAME_OPTIONS.tvInfo, fields: [...DEFAULT_TAG_ORDER] },
    });
    setCustomMode(false);
  };

  const enterCustomMode = () => {
    if (options && !customMode) {
      setNaming({ ...naming, ...buildFileTemplates(options) });
    }
    setCustomMode(true);
  };

  const exitCustomMode = () => {
    const decoded = decodeNameOptions(naming);
    if (!decoded) {
      toast.error("当前模板无法转换为可视化选项，请先按标准结构手写模板");
      return;
    }
    setOptions(decoded);
    setCustomMode(false);
  };

  const setTmdbOn = (value: boolean) => {
    if (customMode) {
      setNaming({ ...naming, include_tmdb_id: value });
    } else {
      updateOptions({ tmdbOn: value });
    }
  };

  const setTmdbTemplate = (value: string) => {
    if (customMode) {
      setNaming({ ...naming, tmdb_id_template: value });
    } else {
      updateOptions({ tmdbTemplate: value });
    }
  };

  const save = async () => {
    setSaving(true);
    try {
      const namingPayload =
        customMode || !options
          ? {
              ...naming,
              include_tmdb_id: effectiveTmdbOn,
              tmdb_id_template: effectiveTmdbTemplate,
              episode_title: effectiveEpisodeTitle,
            }
          : {
              ...naming,
              ...buildFileTemplates(options),
              tag_fields_movie: options.movieInfo.fields,
              tag_fields_tv: options.tvInfo.fields,
              tag_separator_movie: options.movieInfo.separator,
              tag_separator_tv: options.tvInfo.separator,
              episode_title: options.episodeTitle,
              include_tmdb_id: options.tmdbOn,
              tmdb_id_template: options.tmdbTemplate,
            };
      const updated = await api.put<LibraryType>(`/api/libraries/${library.id}`, {
        name: library.name,
        source_id: library.source_id,
        root_path: library.root_path,
        target_root: library.target_root,
        media_type: library.media_type,
        naming: namingPayload,
        scrape,
      });
      onSaved(updated);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "保存失败");
    } finally {
      setSaving(false);
    }
  };

  const tmdbOn = customMode ? Boolean(naming.include_tmdb_id) : Boolean(options?.tmdbOn);
  const tmdbTemplate = customMode
    ? String(naming.tmdb_id_template || DEFAULT_TMDB_TEMPLATE)
    : String(options?.tmdbTemplate || DEFAULT_TMDB_TEMPLATE);

  const movieFolderStyles = workFolderStyles("电影名");
  const tvFolderStyles = workFolderStyles("剧名");
  const movieFolderValue =
    typeof naming.movie_folder === "string" ? naming.movie_folder : "{title} ({year})";
  const tvFolderValue =
    typeof naming.tv_folder === "string" ? naming.tv_folder : "{title} ({year})";
  const seasonFolderValue =
    typeof naming.season_folder === "string" ? naming.season_folder : "Season {season}";
  const movieFolderCustom =
    folderCustom.movie || !movieFolderStyles.some((item) => item.template === movieFolderValue);
  const tvFolderCustom =
    folderCustom.tv || !tvFolderStyles.some((item) => item.template === tvFolderValue);
  const seasonFolderCustom =
    folderCustom.season ||
    !SEASON_FOLDER_STYLES.some((item) => item.template === seasonFolderValue);

  const variables = [
    "{title}",
    "{original_title}",
    "{year}",
    "{season}",
    "{season:02d}",
    "{episode}",
    "{episode:02d}",
    "{episode_title}",
    "{release}",
    "{tags}",
    "{resolution}",
    "{hdr}",
    "{source}",
    "{video}",
    "{audio}",
    "{edition}",
    "{release_notes}",
    "{group}",
    "{tmdb_id}",
  ];

  return (
    <Modal
      open={open}
      onClose={onClose}
      title="命名显示与刮削设置"
      width="max-w-3xl"
      footer={
        <>
          <Button className="mr-auto" onClick={resetDefaults}>
            恢复默认设置
          </Button>
          <Button onClick={onClose}>取消</Button>
          <Button variant="primary" loading={saving} onClick={save}>
            保存设置
          </Button>
        </>
      }
      top={
        <div className="space-y-3">
          {!customMode && options ? (
            <div className="grid grid-cols-2 gap-1.5 rounded-xl border border-white/[0.06] bg-white/[0.02] p-1.5">
              {(
                [
                  ["movie", "电影命名", "年份与附加信息"],
                  ["tv", "剧集命名", "季集与附加信息"],
                ] as Array<["movie" | "tv", string, string]>
              ).map(([id, label, hint]) => (
                <button
                  key={id}
                  type="button"
                  onClick={() => setNameTab(id)}
                  className={`rounded-lg border px-3 py-2 text-center transition ${
                    nameTab === id
                      ? "border-mint-400/40 bg-mint-400/10"
                      : "border-transparent text-slate-400 hover:bg-white/[0.04] hover:text-slate-200"
                  }`}
                >
                  <span className={`block text-xs font-medium ${nameTab === id ? "text-mint-200" : ""}`}>
                    {label}
                  </span>
                  <span className="mt-0.5 block text-[10px] text-slate-500">{hint}</span>
                </button>
              ))}
            </div>
          ) : null}
          <div className="rounded-xl border border-white/[0.06] bg-white/[0.02] px-3.5 py-3">
            <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
              <div className="text-xs font-medium text-slate-300">
                命名效果预览
                {!customMode && options ? (
                  <span className="ml-1.5 text-mint-300">{nameTab === "movie" ? "电影" : "剧集"}</span>
                ) : null}
              </div>
              <div className="text-[10px] text-slate-500">示例数据，实际以文件信息为准</div>
            </div>
            <div className="space-y-3 rounded-lg bg-black/30 px-3 py-2.5">
              {(customMode || !options
                ? ([
                    ["电影", PREVIEW_MOVIE_SAMPLE, moviePreview],
                    ["剧集", PREVIEW_TV_SAMPLE, tvPreview],
                  ] as Array<[string, PreviewSample, string]>)
                : nameTab === "movie"
                  ? ([["电影", PREVIEW_MOVIE_SAMPLE, moviePreview]] as Array<[string, PreviewSample, string]>)
                  : ([["剧集", PREVIEW_TV_SAMPLE, tvPreview]] as Array<[string, PreviewSample, string]>)
              ).map(([label, sample, result]) => (
              <div key={label} className="flex items-start gap-2">
                <span className="mt-px w-8 shrink-0 rounded bg-white/[0.08] px-1.5 py-0.5 text-center text-[10px] text-slate-400">
                  {label}
                </span>
                <div className="min-w-0 flex-1 space-y-1">
                  <div className="flex items-start gap-2">
                    <span className="w-10 shrink-0 text-[10px] leading-relaxed text-slate-500">原名</span>
                    <span className="break-all font-mono text-[11px] leading-relaxed text-slate-500">
                      {sample.original}
                    </span>
                  </div>
                  <div className="flex items-start gap-2">
                    <span className="w-10 shrink-0 text-[10px] leading-relaxed text-slate-500">整理后</span>
                    <span className="break-all font-mono text-[11px] leading-relaxed text-mint-200">
                      {result || "（未设置）"}
                    </span>
                  </div>
                </div>
              </div>
            ))}
            </div>
          </div>
        </div>
      }
    >
      <div className="space-y-5">
        {!customMode && options ? (
          <>
            {nameTab === "movie" ? (
            <div className="rounded-xl border border-white/[0.06] bg-white/[0.02] px-3.5 py-3">
              <div className="mb-3 flex flex-wrap items-center gap-3">
                <span className="text-[11px] text-slate-500">年份：</span>
                {(
                  [
                    ["paren", "(2010)"],
                    ["plain", "2010"],
                    ["none", "不显示"],
                  ] as Array<[YearStyle, string]>
                ).map(([id, label]) => (
                  <label
                    key={id}
                    className="flex cursor-pointer items-center gap-1.5 text-[11px] text-slate-300"
                  >
                    <input
                      type="radio"
                      className="accent-mint-400"
                      checked={options.movieYear === id}
                      onChange={() => updateOptions({ movieYear: id })}
                    />
                    {label}
                  </label>
                ))}
              </div>
              <div className="mb-1 px-2.5 text-[11px] text-slate-500">
                附加信息（仅从原文件名识别提取）：
              </div>
              <div className="space-y-0.5">
                {INFO_MODE_OPTIONS.map(([id, label, hint]) => (
                  <label
                    key={id}
                    className="flex cursor-pointer items-start gap-2.5 rounded-lg px-2.5 py-2 transition hover:bg-white/[0.04]"
                  >
                    <input
                      type="radio"
                      className="mt-0.5 accent-mint-400"
                      checked={options.movieInfo.mode === id}
                      onChange={() => updateMovieInfo({ mode: id })}
                    />
                    <span>
                      <span className="block text-xs text-slate-200">{label}</span>
                      <span className="block text-[11px] leading-relaxed text-slate-500">{hint}</span>
                    </span>
                  </label>
                ))}
              </div>
              {options.movieInfo.mode === "tags" ? (
                <InfoEditor info={options.movieInfo} onChange={updateMovieInfo} />
              ) : null}
            </div>
            ) : (
            <div className="rounded-xl border border-white/[0.06] bg-white/[0.02] px-3.5 py-3">
              <div className="mb-3 flex flex-wrap items-center gap-3">
                <span className="text-[11px] text-slate-500">年份：</span>
                {(
                  [
                    ["paren", "(1994)"],
                    ["plain", "1994"],
                    ["none", "不显示"],
                  ] as Array<[YearStyle, string]>
                ).map(([id, label]) => (
                  <label
                    key={id}
                    className="flex cursor-pointer items-center gap-1.5 text-[11px] text-slate-300"
                  >
                    <input
                      type="radio"
                      className="accent-mint-400"
                      checked={options.tvYear === id}
                      onChange={() => updateOptions({ tvYear: id })}
                    />
                    {label}
                  </label>
                ))}
              </div>
              <div className="mb-3">
                <div className="mb-1.5 text-[11px] text-slate-500">季集样式：</div>
                <div className="grid grid-cols-2 gap-1.5 sm:grid-cols-3">
                  {SEASON_STYLES.map((item) => (
                    <label
                      key={item.label}
                      className={`flex cursor-pointer items-center justify-center gap-1 rounded-lg border px-2 py-1.5 font-mono text-[11px] transition ${
                        options.seasonTemplate === item.template
                          ? "border-mint-400/40 bg-mint-400/10 text-mint-200"
                          : "border-white/[0.08] text-slate-400 hover:text-slate-200"
                      }`}
                    >
                      <input
                        type="radio"
                        className="hidden"
                        checked={options.seasonTemplate === item.template}
                        onChange={() => updateOptions({ seasonTemplate: item.template })}
                      />
                      {item.label}
                      {item.recommended ? (
                        <span className="rounded bg-mint-400/15 px-1 text-[9px] text-mint-300">推荐</span>
                      ) : null}
                    </label>
                  ))}
                </div>
              </div>
              <label className="mb-3 flex cursor-pointer items-center gap-2 text-[11px] text-slate-300">
                <input
                  type="checkbox"
                  className="accent-mint-400"
                  checked={options.episodeTitle}
                  onChange={(event) => updateOptions({ episodeTitle: event.target.checked })}
                />
                文件名包含集标题
                <span className="text-[10px] text-slate-500">整理时自动获取（识别不到则省略）</span>
              </label>
              <div className="mb-1 px-2.5 text-[11px] text-slate-500">
                附加信息（仅从原文件名识别提取）：
              </div>
              <div className="space-y-0.5">
                {INFO_MODE_OPTIONS.map(([id, label, hint]) => (
                  <label
                    key={id}
                    className="flex cursor-pointer items-start gap-2.5 rounded-lg px-2.5 py-2 transition hover:bg-white/[0.04]"
                  >
                    <input
                      type="radio"
                      className="mt-0.5 accent-mint-400"
                      checked={options.tvInfo.mode === id}
                      onChange={() => updateTvInfo({ mode: id })}
                    />
                    <span>
                      <span className="block text-xs text-slate-200">{label}</span>
                      <span className="block text-[11px] leading-relaxed text-slate-500">{hint}</span>
                    </span>
                  </label>
                ))}
              </div>
              {options.tvInfo.mode === "tags" ? (
                <InfoEditor info={options.tvInfo} onChange={updateTvInfo} />
              ) : null}
            </div>
            )}
          </>
        ) : null}

        <div className="rounded-xl border border-white/[0.06] bg-white/[0.02] px-3.5 py-3">
          <label className="flex cursor-pointer items-start gap-2.5">
            <input
              type="checkbox"
              className="mt-0.5 accent-mint-400"
              checked={tmdbOn}
              onChange={(event) => setTmdbOn(event.target.checked)}
            />
            <span>
              <span className="block text-xs text-slate-200">在文件名末尾添加 TMDB ID 标签</span>
              <span className="block text-[11px] leading-relaxed text-slate-500">
                提高播放器（Infuse / Emby / Jellyfin）的识别准确率，将追加形如{" "}
                {tmdbTemplate.replace("{id}", "1668")} 的标签。
              </span>
            </span>
          </label>
          {tmdbOn ? (
            <div className="mt-2.5 grid grid-cols-2 gap-1.5 sm:grid-cols-4">
              {TMDB_TAG_STYLES.map((item) => (
                <label
                  key={item.template}
                  className={`flex cursor-pointer items-center justify-center gap-1 rounded-lg border px-2 py-1.5 font-mono text-[11px] transition ${
                    tmdbTemplate === item.template
                      ? "border-mint-400/40 bg-mint-400/10 text-mint-200"
                      : "border-white/[0.08] text-slate-400 hover:text-slate-200"
                  }`}
                >
                  <input
                    type="radio"
                    className="hidden"
                    checked={tmdbTemplate === item.template}
                    onChange={() => setTmdbTemplate(item.template)}
                  />
                  {item.label}
                  {item.recommended ? (
                    <span className="rounded bg-mint-400/15 px-1 text-[9px] text-mint-300">推荐</span>
                  ) : null}
                </label>
              ))}
            </div>
          ) : null}
        </div>

        <div className="rounded-xl border border-white/[0.06] bg-white/[0.02] px-3.5 py-3">
          <div className="mb-3 text-xs font-medium text-slate-300">文件夹与标题</div>
          <div className="grid gap-4 sm:grid-cols-2">
            <Field label="电影文件夹" hint="整理后的作品文件夹">
              <FolderStylePicker
                styles={movieFolderStyles}
                value={movieFolderValue}
                onChange={(value) => setNaming({ ...naming, movie_folder: value })}
                custom={movieFolderCustom}
                onCustom={(value) => setFolderCustom((current) => ({ ...current, movie: value }))}
              />
            </Field>
            <Field label="剧集文件夹" hint="整理后的剧名文件夹">
              <FolderStylePicker
                styles={tvFolderStyles}
                value={tvFolderValue}
                onChange={(value) => setNaming({ ...naming, tv_folder: value })}
                custom={tvFolderCustom}
                onCustom={(value) => setFolderCustom((current) => ({ ...current, tv: value }))}
              />
            </Field>
            <Field label="季文件夹" hint="剧集按季分目录">
              <FolderStylePicker
                styles={SEASON_FOLDER_STYLES}
                value={seasonFolderValue}
                onChange={(value) => setNaming({ ...naming, season_folder: value })}
                custom={seasonFolderCustom}
                onCustom={(value) => setFolderCustom((current) => ({ ...current, season: value }))}
              />
            </Field>
            <Field label="标题语言">
              <Select value={naming.title_language || "zh"} onChange={(event) => setNaming({ ...naming, title_language: event.target.value })}>
                <option value="zh">中文优先</option>
                <option value="en">原文 / 英文优先</option>
              </Select>
            </Field>
          </div>
        </div>

        <div className="grid gap-2 sm:grid-cols-2">
          <Switch
            checked={Boolean(naming.cleanup_empty_dirs)}
            onChange={(value) => setNaming({ ...naming, cleanup_empty_dirs: value })}
            label="清理空目录"
            hint="整理后尝试删除空文件夹"
          />
          <Switch
            checked={naming.auto_category !== false}
            onChange={(value) => setNaming({ ...naming, auto_category: value })}
            label="自动分类目录"
            hint="识别整理后目录的已有结构，按作品的地区与题材自动归类"
          />
        </div>

        {naming.auto_category !== false ? (
          <div className="rounded-xl border border-white/[0.06] bg-white/[0.02] px-3.5 py-3">
            <div className="mb-2 text-[11px] font-medium text-slate-400">
              识别到的整理后目录结构
              {structureLoading ? <span className="ml-2 text-slate-500">读取中…</span> : null}
            </div>
            {structure && structure.length ? (
              <div className="flex flex-wrap gap-2">
                {structure.map((dir) => (
                  <div key={dir.name} className="rounded-lg bg-white/[0.05] px-2.5 py-1.5">
                    <div className="text-[11px] text-slate-200">{dir.name}</div>
                    {dir.children.length ? (
                      <div className="mt-0.5 text-[10px] leading-relaxed text-slate-500">{dir.children.join(" · ")}</div>
                    ) : null}
                  </div>
                ))}
              </div>
            ) : structureLoading ? null : (
              <p className="text-[11px] leading-relaxed text-slate-500">
                暂未读取到子目录（整理后目录为空或不可访问）。整理时会自动跳过分类，保持默认路径。
              </p>
            )}
          </div>
        ) : null}

        {customMode ? (
          <div className="space-y-4 rounded-xl border border-white/[0.06] bg-white/[0.02] px-3.5 py-3">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <div className="text-xs font-medium text-slate-300">自定义模板（高级）</div>
              <button
                type="button"
                className="text-[11px] text-mint-300 transition hover:text-mint-200"
                onClick={exitCustomMode}
              >
                ← 返回可视化设置
              </button>
            </div>
            <p className="text-[11px] leading-relaxed text-slate-500">
              当前模板无法用可视化选项表示，或你主动选择了自定义编辑。修改下方模板时，上方预览会实时更新。
            </p>
            <div className="grid gap-4 sm:grid-cols-2">
              <Field label="电影文件名" hint="例：{title} ({year}){release}">
                <Input value={naming.movie_file || ""} onChange={(event) => setNaming({ ...naming, movie_file: event.target.value })} />
              </Field>
              <Field label="剧集文件名" hint="例：{title} S{season:02d}E{episode:02d}{episode_title}{release}">
                <Input value={naming.episode_file || ""} onChange={(event) => setNaming({ ...naming, episode_file: event.target.value })} />
              </Field>
              <Field label="标签拼接符" hint="仅影响 {tags}">
                <Input
                  value={String(naming.tag_separator_movie ?? naming.tag_separator ?? ".")}
                  onChange={(event) =>
                    setNaming({
                      ...naming,
                      tag_separator_movie: event.target.value,
                      tag_separator_tv: event.target.value,
                      tag_separator: event.target.value,
                    })
                  }
                />
              </Field>
              <Field label="集标题" hint="模板含 {episode_title} 时需要开启">
                <label className="flex cursor-pointer items-center gap-2 rounded-lg border border-white/[0.08] px-2.5 py-2 text-[11px] text-slate-300">
                  <input
                    type="checkbox"
                    className="accent-mint-400"
                    checked={naming.episode_title !== false}
                    onChange={(event) => setNaming({ ...naming, episode_title: event.target.checked })}
                  />
                  文件名包含集标题
                </label>
              </Field>
            </div>
            <div>
              <div className="mb-2 text-[11px] font-medium text-slate-400">可用变量</div>
              <div className="flex flex-wrap gap-1.5">
                {variables.map((variable) => (
                  <code key={variable} className="rounded-md bg-white/[0.06] px-1.5 py-0.5 font-mono text-[11px] text-mint-300">
                    {variable}
                  </code>
                ))}
              </div>
              <p className="mt-2 text-[11px] leading-relaxed text-slate-500">
                {"{release}"} 直接沿用原文件名里年份（或季集标记）之后的发布信息，{"{tags}"}{" "}
                按附加信息勾选项拼接，两者都保留原始大小写。电影与剧集的附加信息各自独立设置。
              </p>
            </div>
          </div>
        ) : (
          <button
            type="button"
            className="text-[11px] text-slate-500 underline-offset-2 transition hover:text-slate-300 hover:underline"
            onClick={enterCustomMode}
          >
            使用自定义模板（高级）…
          </button>
        )}

        <div className="border-t border-white/[0.06] pt-5">
          <div className="mb-3 text-xs font-medium text-slate-300">同名文件处理</div>
          <Field
            label="目标目录已有同名视频时"
            hint="只影响整理；跳过会保留源文件，覆盖会先备份旧目标，失败时恢复。"
          >
            <Select
              value={String(naming.collision || "suffix")}
              onChange={(event) => setNaming({ ...naming, collision: event.target.value })}
            >
              <option value="suffix">自动加 (1)</option>
              <option value="skip">跳过，不移动</option>
              <option value="overwrite">覆盖同名文件</option>
            </Select>
          </Field>
        </div>

        <div className="border-t border-white/[0.06] pt-5">
          <div className="mb-3 text-xs font-medium text-slate-300">刮削输出</div>
          <div className="grid gap-2 sm:grid-cols-2">
            <Switch
              checked={scrapeEnabled}
              onChange={(value) => setScrape({ ...scrape, enabled: value })}
              label="启用刮削"
              hint={scrapeEnabled ? undefined : "关闭后，下方输出选项不生效"}
            />
            <Switch
              checked={Boolean(scrape.write_nfo)}
              onChange={(value) => setScrape({ ...scrape, write_nfo: value })}
              label="写入 NFO"
              hint="电影和单集与视频同名"
              disabled={!scrapeEnabled}
            />
            <Switch
              checked={Boolean(scrape.download_poster)}
              onChange={(value) => setScrape({ ...scrape, download_poster: value })}
              label="下载海报"
              disabled={!scrapeEnabled}
            />
            <Switch
              checked={Boolean(scrape.download_fanart)}
              onChange={(value) => setScrape({ ...scrape, download_fanart: value })}
              label="下载背景图"
              disabled={!scrapeEnabled}
            />
            <Switch
              checked={Boolean(scrape.download_logo)}
              onChange={(value) => setScrape({ ...scrape, download_logo: value })}
              label="下载 Logo"
              disabled={!scrapeEnabled}
            />
            <Switch
              checked={Boolean(scrape.download_season_poster)}
              onChange={(value) => setScrape({ ...scrape, download_season_poster: value })}
              label="下载季海报"
              disabled={!scrapeEnabled}
            />
            <Switch
              checked={Boolean(scrape.download_episode_thumb)}
              onChange={(value) => setScrape({ ...scrape, download_episode_thumb: value })}
              label="下载剧集缩略图"
              disabled={!scrapeEnabled}
            />
            <Switch
              checked={Boolean(scrape.folder_image)}
              onChange={(value) => setScrape({ ...scrape, folder_image: value })}
              label="生成 folder.jpg"
              hint="Infuse 文件夹封面"
              disabled={!scrapeEnabled}
            />
            <Switch
              checked={Boolean(scrape.overwrite)}
              onChange={(value) => setScrape({ ...scrape, overwrite: value })}
              label="覆盖已有素材"
              disabled={!scrapeEnabled}
            />
          </div>
        </div>
      </div>
    </Modal>
  );
}

