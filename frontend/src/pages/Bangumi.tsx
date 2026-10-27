import { useState } from "react";
import {
  CalendarDays,
  CalendarPlus,
  Check,
  Clock,
  Eye,
  FolderOpen,
  Pencil,
  Play,
  Plus,
  RefreshCw,
  Search,
  Trash2,
  Tv,
} from "lucide-react";
import clsx from "clsx";
import type {
  BangumiCalendar,
  BangumiCalendarItem,
  BangumiFollow,
  BangumiFollowsPayload,
  Library,
  MikanDetail,
  NotificationChannel,
  RssPreview,
  Subscription,
} from "../types";
import { useAsync } from "../lib/hooks";
import { api } from "../lib/api";
import { useToast } from "../lib/toast";
import { formatDateTime, formatSize, statusLabel } from "../lib/format";
import {
  Badge,
  Button,
  Card,
  ConfirmDialog,
  EmptyState,
  Field,
  Input,
  Modal,
  Poster,
  Progress,
  Select,
  Spinner,
  StatusBadge,
  Switch,
} from "../components/ui";
import CloudFolderPicker from "../components/CloudFolderPicker";

const WEEKDAYS = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"];

type FilterMode = "keyword" | "regex";

const FILTER_MODES: { value: FilterMode; label: string }[] = [
  { value: "keyword", label: "关键词" },
  { value: "regex", label: "正则" },
];

function FilterModeToggle({
  mode,
  onChange,
}: {
  mode: FilterMode;
  onChange: (mode: FilterMode) => void;
}) {
  return (
    <div className="flex shrink-0 overflow-hidden rounded-xl border border-white/[0.08]">
      {FILTER_MODES.map((item) => (
        <button
          key={item.value}
          type="button"
          className={clsx(
            "px-3 py-2 text-xs transition",
            mode === item.value
              ? "bg-mint-500/15 text-mint-200"
              : "text-slate-500 hover:text-slate-300",
          )}
          onClick={() => onChange(item.value)}
        >
          {item.label}
        </button>
      ))}
    </div>
  );
}
const INTERVAL_PRESETS = [15, 30, 60, 120, 360, 720];

interface FollowForm {
  title: string;
  title_original: string;
  poster: string;
  bangumi_id: string;
  mikan_bangumi_id: string;
  mikan_input: string;
  subgroup_id: string;
  subgroup_name: string;
  feed_url: string;
  weekday: string;
  total_episodes: string;
  status: string;
  refresh: boolean;
  library_id: number;
  save_subdir: string;
  p115_folder_id: string;
  p115_folder_path: string;
  filter_mode: FilterMode;
  keyword: string;
  regex: string;
  min_size_mb: number;
  max_per_run: number;
  schedule_mode: "interval" | "cron";
  interval_minutes: number;
  cron: string;
  timezone: string;
  auto_organize: boolean;
  replace_versions: boolean;
  notify_channel_ids: number[];
}

const EMPTY_FORM: FollowForm = {
  title: "",
  title_original: "",
  poster: "",
  bangumi_id: "",
  mikan_bangumi_id: "",
  mikan_input: "",
  subgroup_id: "",
  subgroup_name: "",
  feed_url: "",
  weekday: "",
  total_episodes: "",
  status: "following",
  refresh: false,
  library_id: 0,
  save_subdir: "",
  p115_folder_id: "",
  p115_folder_path: "",
  filter_mode: "keyword",
  keyword: "",
  regex: "",
  min_size_mb: 0,
  max_per_run: 20,
  schedule_mode: "interval",
  interval_minutes: 60,
  cron: "0 * * * *",
  timezone: "Asia/Shanghai",
  auto_organize: false,
  replace_versions: true,
  notify_channel_ids: [],
};

function formFromFollow(follow: BangumiFollow): FollowForm {
  return {
    ...EMPTY_FORM,
    title: follow.title,
    title_original: follow.title_original,
    poster: follow.poster,
    bangumi_id: follow.bangumi_id ? String(follow.bangumi_id) : "",
    mikan_bangumi_id: follow.mikan_bangumi_id ? String(follow.mikan_bangumi_id) : "",
    mikan_input: follow.mikan_bangumi_id ? String(follow.mikan_bangumi_id) : "",
    subgroup_id: follow.subgroup_id != null ? String(follow.subgroup_id) : "",
    subgroup_name: follow.subgroup_name,
    feed_url: follow.feed_url,
    weekday: follow.weekday != null ? String(follow.weekday) : "",
    total_episodes: follow.total_episodes != null ? String(follow.total_episodes) : "",
    status: follow.status,
    schedule_mode: follow.schedule_mode === "cron" ? "cron" : "interval",
    interval_minutes: follow.check_interval_minutes ?? 60,
    cron: follow.cron || "0 * * * *",
    timezone: follow.timezone || "Asia/Shanghai",
  };
}

function parseMikanId(value: string): number | null {
  const match =
    value.match(/(?:Home\/Bangumi\/|bangumiId=)(\d+)/i) || value.trim().match(/^(\d+)$/);
  return match ? Number(match[1]) : null;
}

function FormSection({
  title,
  description,
  children,
}: {
  title: string;
  description?: string;
  children: React.ReactNode;
}) {
  return (
    <section className="rounded-2xl border border-white/[0.07] bg-white/[0.02] p-4">
      <h4 className="text-xs font-semibold text-slate-100">{title}</h4>
      {description ? (
        <p className="mt-1 text-[11px] leading-relaxed text-slate-500">{description}</p>
      ) : null}
      <div className="mt-3">{children}</div>
    </section>
  );
}

export default function BangumiPage() {
  const {
    data: followPayload,
    loading: followsLoading,
    reload: reloadFollows,
  } = useAsync<BangumiFollowsPayload>("/api/bangumi/follows");
  const {
    data: calendar,
    loading: calendarLoading,
    error: calendarError,
    reload: reloadCalendar,
    setData: setCalendar,
  } = useAsync<BangumiCalendar>("/api/bangumi/calendar");
  const { data: libraries } = useAsync<Library[]>("/api/libraries");
  const { data: channelPayload } = useAsync<{ channels: NotificationChannel[] }>(
    "/api/notifications/channels",
  );
  const toast = useToast();

  const [statusFilter, setStatusFilter] = useState<"all" | "following" | "paused" | "completed">(
    "all",
  );
  const [selectedWeekday, setSelectedWeekday] = useState<number | null>(null);
  const [formOpen, setFormOpen] = useState(false);
  const [editing, setEditing] = useState<BangumiFollow | null>(null);
  const [form, setForm] = useState<FollowForm>(EMPTY_FORM);
  const [mikanDetail, setMikanDetail] = useState<MikanDetail | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailError, setDetailError] = useState("");
  const [previewOpen, setPreviewOpen] = useState(false);
  const [preview, setPreview] = useState<RssPreview | null>(null);
  const [previewLoading, setPreviewLoading] = useState(false);
  const [previewError, setPreviewError] = useState("");
  const [pickerOpen, setPickerOpen] = useState(false);
  const [saving, setSaving] = useState(false);
  const [actingId, setActingId] = useState<number | null>(null);
  const [refreshingCalendar, setRefreshingCalendar] = useState(false);
  const [refreshingAll, setRefreshingAll] = useState(false);
  const [deleteTarget, setDeleteTarget] = useState<BangumiFollow | null>(null);

  const follows = followPayload?.items || [];
  const channels = channelPayload?.channels || [];
  const visibleFollows = follows.filter((follow) => {
    if (statusFilter === "all") return true;
    return follow.status === statusFilter;
  });

  const todayWeekday = followPayload?.today_weekday ?? calendar?.today_weekday ?? -1;

  const calendarDays = calendar?.days || [];
  const activeWeekday =
    selectedWeekday ?? (todayWeekday >= 0 ? todayWeekday : calendarDays[0]?.weekday ?? 0);
  const activeDay =
    calendarDays.find((day) => day.weekday === activeWeekday) || calendarDays[0] || null;

  const defaultLibrary = () =>
    (libraries || []).find((entry) => String(entry.root_path || "").includes("115")) ||
    libraries?.[0];

  const patch = (values: Partial<FollowForm>) =>
    setForm((current) => ({ ...current, ...values }));

  const loadMikanDetail = async (
    mikanId: number,
    subgroupId?: string | null,
    preserveFeed = false,
  ) => {
    setDetailLoading(true);
    setDetailError("");
    try {
      const detail = await api.get<MikanDetail>(`/api/bangumi/mikan/${mikanId}`);
      setMikanDetail(detail);
      const wanted = subgroupId ?? "";
      const subgroup = detail.subgroups.find((entry) => String(entry.id) === wanted);
      setForm((current) => ({
        ...current,
        title: current.title || detail.title,
        title_original: current.title_original || detail.title_original,
        poster: current.poster || detail.poster,
        bangumi_id: detail.bangumi_id ? String(detail.bangumi_id) : current.bangumi_id,
        mikan_bangumi_id: String(detail.mikan_id),
        subgroup_id: subgroup ? String(subgroup.id) : "",
        subgroup_name: subgroup?.name || "",
        feed_url:
          preserveFeed && current.feed_url
            ? current.feed_url
            : subgroup
              ? subgroup.feed_url
              : detail.feed_url,
        weekday: current.weekday || (detail.weekday != null ? String(detail.weekday) : ""),
        total_episodes:
          detail.total_episodes != null
            ? String(detail.total_episodes)
            : current.total_episodes,
      }));
    } catch (err) {
      setDetailError(err instanceof Error ? err.message : "获取蜜柑番剧信息失败");
    } finally {
      setDetailLoading(false);
    }
  };

  const openCreate = (item?: BangumiCalendarItem) => {
    setEditing(null);
    setMikanDetail(null);
    setDetailError("");
    setPreviewOpen(false);
    setForm({
      ...EMPTY_FORM,
      title: item?.title || "",
      poster: item?.poster || "",
      weekday: item ? String(item.weekday) : "",
      mikan_bangumi_id: item?.mikan_id ? String(item.mikan_id) : "",
      mikan_input: item?.mikan_id ? String(item.mikan_id) : "",
      library_id: defaultLibrary()?.id || 0,
    });
    setPickerOpen(false);
    setFormOpen(true);
    if (item?.mikan_id) void loadMikanDetail(item.mikan_id);
  };

  const openEdit = async (follow: BangumiFollow) => {
    setEditing(follow);
    setMikanDetail(null);
    setDetailError("");
    setPreviewOpen(false);
    setForm({ ...formFromFollow(follow), library_id: defaultLibrary()?.id || 0 });
    setPickerOpen(false);
    setFormOpen(true);
    if (follow.mikan_bangumi_id) {
      void loadMikanDetail(
        follow.mikan_bangumi_id,
        follow.subgroup_id != null ? String(follow.subgroup_id) : "",
        true,
      );
    }
    if (follow.subscription_id) {
      try {
        const sub = await api.get<Subscription>(`/api/subscriptions/${follow.subscription_id}`);
        const config = sub.config || {};
        setForm((current) => ({
          ...current,
          library_id: sub.library_id || current.library_id,
          save_subdir: String(config.save_subdir || ""),
          p115_folder_id: String(config.p115_folder_id || ""),
          p115_folder_path: String(config.p115_folder_path || ""),
          filter_mode: config.regex && !config.keyword ? "regex" : "keyword",
          keyword: String(config.keyword || ""),
          regex: String(config.regex || ""),
          min_size_mb: Number(config.min_size_mb || 0),
          max_per_run:
            config.max_per_run === undefined || config.max_per_run === null
              ? 20
              : Number(config.max_per_run),
          schedule_mode: sub.schedule?.mode === "cron" ? "cron" : "interval",
          interval_minutes: Number(sub.schedule?.interval_minutes || 60),
          cron: sub.schedule?.cron || "0 * * * *",
          timezone: sub.schedule?.timezone || "Asia/Shanghai",
          auto_organize: Boolean(sub.auto_organize),
          replace_versions: Boolean(sub.replace_versions),
          notify_channel_ids: [...(sub.notify_channel_ids || [])],
        }));
      } catch (err) {
        toast.error(err instanceof Error ? err.message : "读取订阅配置失败");
      }
    }
  };

  const fetchDetailFromInput = () => {
    const mikanId = parseMikanId(form.mikan_input);
    if (!mikanId) {
      toast.error("请填写蜜柑番剧链接或数字 ID");
      return;
    }
    void loadMikanDetail(mikanId, form.subgroup_id);
  };

  const selectSubgroup = (value: string) => {
    const subgroup = mikanDetail?.subgroups.find((entry) => String(entry.id) === value);
    patch({
      subgroup_id: value,
      subgroup_name: subgroup?.name || "",
      feed_url: subgroup ? subgroup.feed_url : mikanDetail?.feed_url || form.feed_url,
    });
  };

  const previewRss = async () => {
    if (!form.feed_url.trim()) {
      toast.error("请先选择字幕组或填写 RSS 地址");
      return;
    }
    setPreviewOpen(true);
    setPreview(null);
    setPreviewError("");
    setPreviewLoading(true);
    try {
      const result = await api.post<RssPreview>("/api/bangumi/rss/preview", {
        feed_url: form.feed_url.trim(),
      });
      setPreview(result);
    } catch (err) {
      setPreviewError(err instanceof Error ? err.message : "预览失败");
    } finally {
      setPreviewLoading(false);
    }
  };

  const save = async () => {
    if (!form.feed_url.trim()) {
      toast.error("请选择字幕组或填写蜜柑 RSS 地址");
      return;
    }
    if (!form.library_id) {
      toast.error("请选择要整理到的影视库");
      return;
    }
    setSaving(true);
    try {
      const base = {
        title: form.title.trim(),
        title_original: form.title_original,
        poster: form.poster,
        bangumi_id: form.bangumi_id ? Number(form.bangumi_id) : null,
        mikan_bangumi_id: form.mikan_bangumi_id ? Number(form.mikan_bangumi_id) : null,
        subgroup_id: form.subgroup_id ? Number(form.subgroup_id) : null,
        subgroup_name: form.subgroup_name.trim(),
        feed_url: form.feed_url.trim(),
        weekday: form.weekday === "" ? null : Number(form.weekday),
        total_episodes: form.total_episodes ? Number(form.total_episodes) : null,
        library_id: form.library_id,
        save_subdir: form.save_subdir,
        save_root: !form.save_subdir.trim(),
        p115_folder_id: form.p115_folder_id,
        p115_folder_path: form.p115_folder_path,
        keyword: form.filter_mode === "keyword" ? form.keyword : "",
        regex: form.filter_mode === "regex" ? form.regex : "",
        min_size_mb: form.min_size_mb,
        max_per_run: form.max_per_run,
        schedule_mode: form.schedule_mode,
        interval_minutes: form.interval_minutes,
        cron: form.cron,
        timezone: form.timezone,
        auto_organize: form.auto_organize,
        replace_versions: form.replace_versions,
        notify_channel_ids: form.notify_channel_ids,
      };
      if (editing) {
        await api.put(`/api/bangumi/follows/${editing.id}`, {
          ...base,
          status: form.status,
          refresh: form.refresh,
        });
        toast.success(form.refresh ? "追更已保存并刷新" : "追更与订阅已更新");
      } else {
        await api.post("/api/bangumi/follows", base);
        toast.success("已添加追更并创建 RSS 订阅，稍后自动检查更新");
      }
      setFormOpen(false);
      reloadFollows();
      reloadCalendar();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "保存失败");
    } finally {
      setSaving(false);
    }
  };

  const refreshOne = async (follow: BangumiFollow) => {
    setActingId(follow.id);
    try {
      const result = await api.post<BangumiFollow>(
        `/api/bangumi/follows/${follow.id}/refresh`,
      );
      if (result.last_error) toast.error(result.last_error);
      else toast.success("追更进度已刷新");
      reloadFollows();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "刷新失败");
    } finally {
      setActingId(null);
    }
  };

  const refreshAll = async () => {
    setRefreshingAll(true);
    try {
      const result = await api.post<{ checked: number; updated: number; failed: number }>(
        "/api/bangumi/follows/refresh",
      );
      toast.success(
        `检查 ${result.checked} 个追更，更新 ${result.updated} 个${
          result.failed ? `，失败 ${result.failed} 个` : ""
        }`,
      );
      reloadFollows();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "刷新失败");
    } finally {
      setRefreshingAll(false);
    }
  };

  const toggleStatus = async (follow: BangumiFollow) => {
    setActingId(follow.id);
    try {
      await api.put(`/api/bangumi/follows/${follow.id}`, {
        status: follow.status === "following" ? "paused" : "following",
      });
      toast.success(follow.status === "following" ? "已暂停追更" : "已恢复追更");
      reloadFollows();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "操作失败");
    } finally {
      setActingId(null);
    }
  };

  const remove = async () => {
    if (!deleteTarget) return;
    setActingId(deleteTarget.id);
    try {
      await api.del(`/api/bangumi/follows/${deleteTarget.id}`);
      toast.success("追更记录与对应 RSS 订阅已删除");
      setDeleteTarget(null);
      reloadFollows();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "删除失败");
    } finally {
      setActingId(null);
    }
  };

  const forceCalendar = async () => {
    setRefreshingCalendar(true);
    try {
      const result = await api.get<BangumiCalendar>("/api/bangumi/calendar?force=true");
      setCalendar(result);
      if (result.error) toast.push(`日历使用缓存：${result.error}`);
      else toast.success("放送日历已更新");
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "刷新日历失败");
    } finally {
      setRefreshingCalendar(false);
    }
  };

  const openCalendarItem = (item: BangumiCalendarItem) => {
    if (item.follow_id) {
      const follow = follows.find((entry) => entry.id === item.follow_id);
      if (follow) {
        void openEdit(follow);
        return;
      }
    }
    openCreate(item);
  };

  return (
    <div className="mx-auto max-w-6xl space-y-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-lg font-semibold text-white">追更与放送日历</h1>
          <p className="mt-1 text-xs text-slate-500">
            蜜柑计划每周放送表与蜜柑 RSS 追更进度；添加追更会自动创建 RSS 订阅并下载新集。
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <Button onClick={refreshAll} loading={refreshingAll}>
            <RefreshCw size={15} /> 刷新全部追更
          </Button>
          <Button variant="primary" onClick={() => openCreate()}>
            <Plus size={15} /> 添加追更
          </Button>
        </div>
      </div>

      <Card className="p-4 sm:p-5">
        <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
          <div>
            <h2 className="flex items-center gap-1.5 text-sm font-semibold text-slate-100">
              <CalendarDays size={16} className="text-mint-400" /> 本周放送日历
            </h2>
            <p className="mt-0.5 text-[10px] text-slate-500">
              {calendar?.fetched_at
                ? `数据更新于 ${formatDateTime(calendar.fetched_at)}，缓存 6 小时`
                : ""}
            </p>
          </div>
          <div className="flex items-center gap-2">
            {calendar?.stale ? <Badge tone="amber">使用缓存</Badge> : null}
            <Button
              className="px-2.5 py-1.5 text-xs"
              onClick={forceCalendar}
              loading={refreshingCalendar}
            >
              <RefreshCw size={12} /> 更新日历
            </Button>
          </div>
        </div>

        {calendar?.error ? (
          <div className="mb-3 rounded-xl border border-amber-500/25 bg-amber-500/[0.06] px-3 py-2 text-[11px] text-amber-200">
            日历刷新失败，当前展示缓存内容：{calendar.error}
          </div>
        ) : null}
        {!calendar?.days?.length && calendarError ? (
          <div className="rounded-xl border border-rose-500/25 bg-rose-500/[0.06] px-3 py-2 text-[11px] text-rose-200">
            无法获取放送日历：{calendarError}。仍可以点「添加追更」手动填写蜜柑 RSS。
          </div>
        ) : null}

        {calendarLoading && !calendar ? (
          <div className="flex h-28 items-center justify-center gap-2 text-xs text-slate-500">
            <Spinner size={13} /> 加载放送日历…
          </div>
        ) : calendarDays.length ? (
          <>
            <div className="grid grid-cols-7 gap-1 rounded-xl border border-white/[0.06] bg-white/[0.015] p-1.5">
              {calendarDays.map((day) => {
                const isActive = activeDay?.weekday === day.weekday;
                const isToday = day.weekday === todayWeekday;
                return (
                  <button
                    key={day.weekday}
                    type="button"
                    onClick={() => setSelectedWeekday(day.weekday)}
                    className={clsx(
                      "rounded-lg px-1 py-1.5 text-center transition",
                      isActive
                        ? "bg-mint-500/15 text-mint-200 ring-1 ring-inset ring-mint-500/40"
                        : "text-slate-400 hover:bg-white/[0.05] hover:text-slate-200",
                    )}
                  >
                    <span className="block truncate text-[11px] font-medium sm:text-xs">
                      {day.label}
                    </span>
                    <span
                      className={clsx(
                        "mt-0.5 block truncate text-[9px]",
                        isActive ? "text-mint-300/80" : "text-slate-600",
                      )}
                    >
                      {day.items.length} 部{isToday ? " · 今天" : ""}
                    </span>
                  </button>
                );
              })}
            </div>
            <div className="mt-2.5 max-h-[30rem] overflow-y-auto rounded-xl border border-white/[0.06] bg-white/[0.015] p-2">
              {activeDay?.items.length ? (
                <div className="grid grid-cols-3 gap-x-2 gap-y-3 sm:grid-cols-4 md:grid-cols-5 lg:grid-cols-6">
                  {activeDay.items.map((item, index) => (
                    <button
                      key={item.mikan_id ?? item.bangumi_id ?? `${activeDay.weekday}-${index}`}
                      type="button"
                      onClick={() => openCalendarItem(item)}
                      title={item.summary || item.title}
                      className="group flex min-w-0 flex-col text-left"
                    >
                      <span className="relative block">
                        <Poster
                          url={item.poster}
                          title={item.title}
                          className="aspect-[2/3] w-full transition group-hover:brightness-110"
                          rounded="rounded-lg"
                        />
                        {item.followed ? (
                          <span className="absolute right-1 top-1 rounded-md bg-black/70 px-1 py-0.5 text-[9px] text-mint-300 backdrop-blur">
                            {item.follow_status === "paused" ? "已暂停" : "已追更"}
                          </span>
                        ) : null}
                      </span>
                      <span
                        className="mt-1.5 line-clamp-2 h-[2.8em] w-full break-words text-[13px] leading-[1.4] text-slate-300 transition group-hover:text-white"
                        title={item.title}
                      >
                        {item.title}
                      </span>
                    </button>
                  ))}
                </div>
              ) : (
                <div className="flex h-24 items-center justify-center text-[11px] text-slate-600">
                  这一天暂无放送
                </div>
              )}
            </div>
          </>
        ) : null}
      </Card>

      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex flex-wrap gap-2">
          {[
            { key: "all" as const, label: "全部" },
            { key: "following" as const, label: `追更中 ${followPayload?.counts.following ?? 0}` },
            { key: "paused" as const, label: `已暂停 ${followPayload?.counts.paused ?? 0}` },
            { key: "completed" as const, label: `已完结 ${followPayload?.counts.completed ?? 0}` },
          ].map((item) => (
            <button
              key={item.key}
              onClick={() => setStatusFilter(item.key)}
              className={clsx(
                "rounded-full border px-3 py-1.5 text-xs transition",
                statusFilter === item.key
                  ? "border-mint-500/40 bg-mint-500/10 text-mint-300"
                  : "border-white/10 bg-white/[0.03] text-slate-400 hover:text-slate-200",
              )}
            >
              {item.label}
            </button>
          ))}
        </div>
        <Button className="px-2.5 py-1.5 text-xs" onClick={reloadFollows}>
          <RefreshCw size={12} /> 刷新列表
        </Button>
      </div>

      {followsLoading && !followPayload ? (
        <div className="flex h-52 items-center justify-center gap-2 text-sm text-slate-400">
          <Spinner /> 加载追更列表…
        </div>
      ) : visibleFollows.length ? (
        <div className="grid gap-3 md:grid-cols-2">
          {visibleFollows.map((follow) => {
            const progress =
              follow.total_episodes && follow.latest_episode != null
                ? Math.min(100, (follow.latest_episode / follow.total_episodes) * 100)
                : 0;
            return (
              <Card key={follow.id} className="space-y-2.5 p-4">
                <div className="flex gap-3">
                  <Poster
                    url={follow.poster}
                    title={follow.title}
                    subtitle={follow.subgroup_name}
                    className="h-24 w-16 shrink-0"
                  />
                  <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-center gap-1.5">
                      <span className="min-w-0 truncate text-sm font-semibold text-white">
                        {follow.title}
                      </span>
                      <StatusBadge status={follow.status} />
                    </div>
                    {follow.title_original && follow.title_original !== follow.title ? (
                      <div className="truncate text-[10px] text-slate-500">
                        {follow.title_original}
                      </div>
                    ) : null}
                    <div className="mt-1 flex flex-wrap gap-1.5">
                      {follow.weekday_label ? (
                        <Badge tone="slate">{follow.weekday_label}</Badge>
                      ) : null}
                      <Badge tone="slate">
                        <Clock size={9} className="mr-0.5" />
                        {follow.schedule_mode === "cron"
                          ? `Cron ${follow.cron}`
                          : `每 ${follow.check_interval_minutes ?? 60} 分钟检查`}
                      </Badge>
                      {follow.subgroup_name ? (
                        <Badge tone="violet">{follow.subgroup_name}</Badge>
                      ) : null}
                    </div>
                    <div className="mt-2 text-[11px] text-slate-400">
                      最新 {follow.latest_episode ?? "—"}
                      {follow.total_episodes ? ` / ${follow.total_episodes}` : ""} 集
                      {follow.next_episode ? ` · 下一集 ${follow.next_episode}` : ""}
                    </div>
                    {follow.total_episodes ? (
                      <Progress value={progress} className="mt-1.5 h-1" />
                    ) : null}
                    <div className="mt-1.5 flex flex-wrap gap-x-3 gap-y-0.5 text-[10px] text-slate-500">
                      <span>
                        最近检查：
                        {follow.last_synced_at ? formatDateTime(follow.last_synced_at) : "—"}
                      </span>
                    </div>
                    {follow.subscription_id ? (
                      <div className="mt-1 truncate text-[10px] text-sky-300/80">
                        RSS 订阅：{follow.subscription_name || `#${follow.subscription_id}`}
                        {follow.subscription_enabled ? "" : "（已停用）"}
                        {follow.subscription_last_status
                          ? ` · ${statusLabel(follow.subscription_last_status)}`
                          : ""}
                      </div>
                    ) : (
                      <div className="mt-1 text-[10px] text-slate-600">未创建 RSS 订阅</div>
                    )}
                    {follow.last_error ? (
                      <div className="mt-1 rounded-lg border border-rose-500/25 bg-rose-500/[0.06] px-2 py-1 text-[10px] leading-relaxed text-rose-200">
                        {follow.last_error}
                      </div>
                    ) : null}
                  </div>
                </div>
                <div className="flex flex-wrap items-center gap-1.5 border-t border-white/[0.05] pt-2.5">
                  <Button
                    className="px-2 py-1 text-[10px]"
                    loading={actingId === follow.id}
                    disabled={!follow.feed_url}
                    onClick={() => refreshOne(follow)}
                    title={follow.feed_url ? "刷新 RSS 进度" : "没有 RSS 地址"}
                  >
                    <RefreshCw size={11} /> 刷新
                  </Button>
                  <Button
                    className="px-2 py-1 text-[10px]"
                    onClick={() => toggleStatus(follow)}
                    title={follow.status === "following" ? "暂停追更" : "恢复追更"}
                  >
                    {follow.status === "following" ? <Clock size={11} /> : <Play size={11} />}
                    {follow.status === "following" ? "暂停" : "继续"}
                  </Button>
                  <Button
                    className="px-2 py-1 text-[10px]"
                    onClick={() => openEdit(follow)}
                  >
                    <Pencil size={11} /> 编辑
                  </Button>
                  <Button
                    className="ml-auto px-2 py-1 text-[10px]"
                    onClick={() => setDeleteTarget(follow)}
                  >
                    <Trash2 size={11} /> 删除
                  </Button>
                </div>
              </Card>
            );
          })}
        </div>
      ) : (
        <EmptyState
          icon={<Tv size={28} />}
          title={follows.length ? "没有符合筛选条件的追更" : "还没有追更记录"}
          description="可以从放送日历点击番剧添加，也可以直接填写蜜柑 RSS 地址。添加追更会同时创建 RSS 订阅，下载进度自动同步到这里。"
          action={
            <Button variant="primary" onClick={() => openCreate()}>
              <CalendarPlus size={14} /> 添加第一部番剧
            </Button>
          }
        />
      )}

      <Modal
        open={formOpen}
        onClose={() => {
          if (!saving) setFormOpen(false);
        }}
        title={editing ? `编辑追更 · ${editing.title}` : "添加追更"}
        width="max-w-2xl"
        footer={
          <>
            <Button onClick={() => setFormOpen(false)} disabled={saving}>
              取消
            </Button>
            <Button variant="primary" onClick={save} loading={saving}>
              <Check size={14} /> 保存
            </Button>
          </>
        }
      >
        <div className="space-y-4">
          <FormSection
            title="番剧与字幕组"
            description="填入蜜柑番剧链接或 ID 后会自动读取标题、Bangumi 条目和字幕组，并生成对应的 RSS 地址。"
          >
            <div className="space-y-3">
              <Field
                label="蜜柑番剧链接 / ID"
                hint="从放送日历点进来会自动填入；也可以粘贴蜜柑番剧页链接或数字 ID。"
              >
                <div className="flex gap-2">
                  <Input
                    value={form.mikan_input}
                    onChange={(event) => patch({ mikan_input: event.target.value })}
                    placeholder="https://mikanani.me/Home/Bangumi/4063 或 4063"
                  />
                  <Button
                    type="button"
                    className="shrink-0 px-3 py-2 text-xs"
                    onClick={fetchDetailFromInput}
                    loading={detailLoading}
                    disabled={!form.mikan_input.trim()}
                  >
                    <Search size={13} /> 获取
                  </Button>
                </div>
              </Field>
              {detailError ? (
                <div className="rounded-xl border border-rose-500/25 bg-rose-500/[0.06] px-3 py-2 text-[11px] text-rose-200">
                  {detailError}
                </div>
              ) : null}
              <div className="grid gap-3 sm:grid-cols-2">
                <Field label="番剧名称">
                  <Input
                    value={form.title}
                    onChange={(event) => patch({ title: event.target.value })}
                    placeholder="可从蜜柑页面自动带入"
                  />
                </Field>
                <Field
                  label="Bangumi 条目 ID"
                  hint="自动识别蜜柑页面上关联的 Bangumi subject ID。"
                >
                  <Input
                    value={form.bangumi_id}
                    onChange={(event) => patch({ bangumi_id: event.target.value })}
                    placeholder="例如：418229"
                    inputMode="numeric"
                  />
                </Field>
                <Field
                  label="字幕组"
                  hint="选项来自蜜柑番剧页；「全部字幕组」会包含该番所有字幕组。"
                >
                  <Select
                    value={form.subgroup_id}
                    onChange={(event) => selectSubgroup(event.target.value)}
                    disabled={!mikanDetail?.subgroups.length}
                  >
                    <option value="">全部字幕组</option>
                    {(mikanDetail?.subgroups || []).map((subgroup) => (
                      <option key={subgroup.id} value={subgroup.id}>
                        {subgroup.name}
                      </option>
                    ))}
                  </Select>
                </Field>
                <Field label="放送星期" hint="从蜜柑番剧页自动识别，可手动修正。">
                  <Select
                    value={form.weekday}
                    onChange={(event) => patch({ weekday: event.target.value })}
                  >
                    <option value="">未设置</option>
                    {WEEKDAYS.map((label, index) => (
                      <option key={label} value={index}>
                        {label}
                      </option>
                    ))}
                  </Select>
                </Field>
                <Field label="总集数" hint="自动从 Bangumi 获取，获取不到时可手动填写；达到总集数会自动标记为已完结。">
                  <Input
                    type="number"
                    min="0"
                    value={form.total_episodes}
                    onChange={(event) => patch({ total_episodes: event.target.value })}
                    placeholder="自动获取"
                  />
                </Field>
                {editing ? (
                  <Field label="追更状态">
                    <Select
                      value={form.status}
                      onChange={(event) => patch({ status: event.target.value })}
                    >
                      <option value="following">追更中</option>
                      <option value="paused">已暂停</option>
                      <option value="completed">已完结</option>
                    </Select>
                  </Field>
                ) : null}
              </div>
              <Field label="蜜柑 RSS 地址" hint="选择字幕组后自动生成，可手动修改。">
                <div className="flex gap-2">
                  <Input
                    value={form.feed_url}
                    onChange={(event) => patch({ feed_url: event.target.value })}
                    placeholder="https://mikanani.me/RSS/Bangumi?bangumiId=4063"
                  />
                  <Button
                    type="button"
                    className="shrink-0 px-3 py-2 text-xs"
                    onClick={previewRss}
                    disabled={!form.feed_url.trim()}
                  >
                    <Eye size={13} /> 预览
                  </Button>
                </div>
              </Field>
            </div>
          </FormSection>

          <FormSection
            title="下载与整理"
            description="添加追更会同时创建一个 RSS 订阅：新集先下载到暂存目录，再进入识别与整理流程。"
          >
            <div className="space-y-3">
              <Field label="整理到影视库">
                <Select
                  value={form.library_id}
                  onChange={(event) => patch({ library_id: Number(event.target.value) })}
                >
                  <option value={0}>请选择影视库</option>
                  {(libraries || []).map((library) => (
                    <option key={library.id} value={library.id}>
                      {library.name} · {library.root_path}
                    </option>
                  ))}
                </Select>
              </Field>
              <Field
                label="115 转存目录"
                hint={
                  form.p115_folder_path
                    ? `影视库内路径：${form.save_subdir || "根目录"}，保存后会从该路径扫描整理。`
                    : "可直接浏览 115 网盘目录；留空直接保存到影视库根目录。"
                }
              >
                <div className="flex gap-2">
                  <Input
                    value={
                      form.p115_folder_path
                        ? `/${form.p115_folder_path}`
                        : form.save_subdir
                    }
                    onChange={(event) =>
                      patch({
                        save_subdir: event.target.value.replace(/^\/+/, ""),
                        p115_folder_id: "",
                        p115_folder_path: "",
                      })
                    }
                    placeholder="留空保存到根目录"
                  />
                  <Button
                    type="button"
                    className="shrink-0 px-3 py-2 text-xs"
                    onClick={() => setPickerOpen(true)}
                    disabled={!form.library_id}
                  >
                    <FolderOpen size={13} /> 浏览网盘
                  </Button>
                </div>
              </Field>
              <div className="grid gap-3 sm:grid-cols-2">
                <Field
                  className="sm:col-span-2"
                  label="过滤规则"
                  hint={
                    form.filter_mode === "keyword"
                      ? "关键词过滤：多个关键词用逗号分隔，需全部命中条目标题。"
                      : "正则过滤：匹配条目标题，留空表示不启用。"
                  }
                >
                  <div className="flex gap-2">
                    <FilterModeToggle
                      mode={form.filter_mode}
                      onChange={(mode) => patch({ filter_mode: mode })}
                    />
                    <Input
                      value={form.filter_mode === "keyword" ? form.keyword : form.regex}
                      onChange={(event) =>
                        patch(
                          form.filter_mode === "keyword"
                            ? { keyword: event.target.value }
                            : { regex: event.target.value },
                        )
                      }
                      placeholder={
                        form.filter_mode === "keyword"
                          ? "例如：1080p,简体"
                          : "例如：第\\d+集"
                      }
                    />
                  </div>
                </Field>
                <Field label="最小体积（MB）" hint="0 表示不过滤。">
                  <Input
                    type="number"
                    min="0"
                    value={form.min_size_mb}
                    onChange={(event) => patch({ min_size_mb: Number(event.target.value) })}
                  />
                </Field>
                <Field label="每次最多下载" hint="0 表示不限制，一次运行处理所有匹配条目。">
                  <Input
                    type="number"
                    min="0"
                    max="200"
                    value={form.max_per_run}
                    onChange={(event) =>
                      patch({ max_per_run: Math.max(0, Number(event.target.value) || 0) })
                    }
                  />
                </Field>
              </div>
            </div>
          </FormSection>

          <FormSection
            title="执行计划与自动化"
            description="按固定间隔或 Cron 表达式检查蜜柑 RSS 并下载新集；只有匹配成功的文件才会自动整理。"
          >
            <div className="space-y-3">
              <div className="grid gap-3 sm:grid-cols-2">
                <Field label="调度方式">
                  <Select
                    value={form.schedule_mode}
                    onChange={(event) =>
                      patch({ schedule_mode: event.target.value as "interval" | "cron" })
                    }
                  >
                    <option value="interval">固定间隔</option>
                    <option value="cron">Cron 表达式</option>
                  </Select>
                </Field>
                <Field label="时区" hint="IANA 时区名称，例如 Asia/Shanghai。">
                  <Input
                    value={form.timezone}
                    onChange={(event) => patch({ timezone: event.target.value })}
                    placeholder="Asia/Shanghai"
                  />
                </Field>
              </div>
              {form.schedule_mode === "interval" ? (
                <Field label="检查间隔" hint="最短 5 分钟，最长 7 天。">
                  <div className="flex flex-wrap items-center gap-1.5">
                    {INTERVAL_PRESETS.map((minutes) => (
                      <button
                        key={minutes}
                        type="button"
                        onClick={() => patch({ interval_minutes: minutes })}
                        className={clsx(
                          "rounded-lg border px-2.5 py-1.5 text-[11px] transition",
                          form.interval_minutes === minutes
                            ? "border-mint-500/40 bg-mint-500/10 text-mint-200"
                            : "border-white/10 bg-white/[0.02] text-slate-400 hover:text-slate-200",
                        )}
                      >
                        {minutes >= 60 ? `${minutes / 60} 小时` : `${minutes} 分钟`}
                      </button>
                    ))}
                    <Input
                      type="number"
                      min="5"
                      max="10080"
                      className="w-24"
                      value={form.interval_minutes}
                      onChange={(event) =>
                        patch({ interval_minutes: Number(event.target.value) })
                      }
                    />
                    <span className="text-[11px] text-slate-500">分钟</span>
                  </div>
                </Field>
              ) : (
                <Field
                  label="Cron 表达式"
                  hint="分 时 日 月 周，支持 *、范围、步长与 @hourly 等宏。"
                >
                  <Input
                    value={form.cron}
                    onChange={(event) => patch({ cron: event.target.value })}
                    placeholder="0 * * * *"
                  />
                </Field>
              )}
              <Switch
                checked={form.auto_organize}
                onChange={(value) => patch({ auto_organize: value })}
                label="自动整理匹配成功的文件"
                hint="默认关闭。首次使用建议先观察识别结果。"
              />
              <Switch
                checked={form.replace_versions}
                onChange={(value) => patch({ replace_versions: value })}
                label="检测到更高版本时替换旧集"
                hint="需要自动整理已开启；只处理同一 TMDb + 季 + 集。"
                disabled={!form.auto_organize}
              />
              {editing ? (
                <Switch
                  checked={form.refresh}
                  onChange={(value) => patch({ refresh: value })}
                  label="保存后立即刷新 RSS"
                  hint="会重新读取订阅源并更新最新集。"
                />
              ) : null}
              <div>
                <div className="label">通知渠道</div>
                {channels.length ? (
                  <div className="flex flex-wrap gap-1.5">
                    {channels.map((channel) => {
                      const selected = form.notify_channel_ids.includes(channel.id);
                      return (
                        <button
                          key={channel.id}
                          type="button"
                          onClick={() =>
                            patch({
                              notify_channel_ids: selected
                                ? form.notify_channel_ids.filter((id) => id !== channel.id)
                                : [...form.notify_channel_ids, channel.id],
                            })
                          }
                          className={clsx(
                            "rounded-lg border px-2.5 py-1.5 text-[11px] transition",
                            selected
                              ? "border-mint-500/40 bg-mint-500/10 text-mint-200"
                              : "border-white/10 bg-white/[0.02] text-slate-400 hover:text-slate-200",
                            !channel.enabled ? "opacity-50" : "",
                          )}
                        >
                          {channel.name}
                          <span className="ml-1 text-[9px] text-slate-500">
                            {channel.enabled ? "" : "（已停用）"}
                          </span>
                        </button>
                      );
                    })}
                  </div>
                ) : (
                  <div className="rounded-xl border border-dashed border-white/10 px-3 py-4 text-[11px] text-slate-500">
                    还没有通知渠道。可先保存追更，再到「通知渠道」页添加。
                  </div>
                )}
                <p className="mt-1.5 text-[10px] leading-relaxed text-slate-500">
                  不选择表示使用所有已启用渠道；选择后只向所选渠道发送。
                </p>
              </div>
            </div>
          </FormSection>
        </div>
      </Modal>

      <CloudFolderPicker
        open={pickerOpen}
        kind="p115"
        libraryId={form.library_id}
        initialFid={form.p115_folder_id}
        initialPath={form.p115_folder_path}
        onClose={() => setPickerOpen(false)}
        onSelect={(value) => {
          patch({
            save_subdir: value.libraryPath,
            p115_folder_id: value.fid,
            p115_folder_path: value.path,
          });
          setPickerOpen(false);
        }}
      />

      <Modal
        open={previewOpen}
        onClose={() => setPreviewOpen(false)}
        title={preview?.title ? `RSS 预览 · ${preview.title}` : "RSS 预览"}
        width="max-w-2xl"
        footer={<Button onClick={() => setPreviewOpen(false)}>关闭</Button>}
      >
        {previewLoading ? (
          <div className="flex h-32 items-center justify-center gap-2 text-xs text-slate-500">
            <Spinner size={13} /> 读取 RSS…
          </div>
        ) : previewError ? (
          <div className="rounded-xl border border-rose-500/25 bg-rose-500/[0.06] px-3 py-2 text-[11px] text-rose-200">
            {previewError}
          </div>
        ) : preview ? (
          <div>
            <div className="text-[11px] text-slate-500">
              共 {preview.total} 条{preview.truncated ? "，仅显示前 50 条" : ""}
            </div>
            <div className="mt-2 max-h-[55vh] divide-y divide-white/[0.05] overflow-y-auto">
              {preview.items.map((item, index) => (
                <div key={`${item.link}-${index}`} className="py-2">
                  <div className="text-[11px] leading-relaxed text-slate-200">{item.title}</div>
                  <div className="mt-0.5 flex flex-wrap gap-x-3 text-[9px] text-slate-500">
                    {item.published_at ? (
                      <span>{formatDateTime(item.published_at)}</span>
                    ) : null}
                    {item.size ? <span>{formatSize(item.size)}</span> : null}
                  </div>
                </div>
              ))}
            </div>
          </div>
        ) : null}
      </Modal>

      <ConfirmDialog
        open={Boolean(deleteTarget)}
        title="删除追更记录"
        danger
        confirmText="删除"
        message={`确定删除「${deleteTarget?.title || ""}」的追更记录吗？对应的 RSS 订阅会一并删除（已下载和已整理的文件不会删除）。`}
        onConfirm={remove}
        onClose={() => {
          if (!actingId) setDeleteTarget(null);
        }}
      />
    </div>
  );
}
