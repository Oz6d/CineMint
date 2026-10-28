import { useEffect, useMemo, useRef, useState } from "react";
import {
  ChevronRight,
  ClipboardPaste,
  CloudDownload,
  Copy,
  FileJson,
  FolderOpen,
  History,
  Pencil,
  Play,
  Plus,
  RefreshCw,
  Rss,
  Trash2,
} from "lucide-react";
import clsx from "clsx";
import type {
  Library,
  NotificationChannel,
  ScheduleConfig,
  Subscription,
  SubscriptionEntry,
  SubscriptionPreview,
  SubscriptionRun,
} from "../types";
import { useAsync } from "../lib/hooks";
import { api } from "../lib/api";
import { useToast } from "../lib/toast";
import { formatDateTime, formatSize, formatTime, seasonEpisode, statusLabel } from "../lib/format";
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
  Progress,
  Select,
  Spinner,
  StatusBadge,
  Switch,
} from "../components/ui";
import CloudFolderPicker from "../components/CloudFolderPicker";
import QuarkSharePicker from "../components/QuarkSharePicker";

type SubType = "quark" | "rss";
type DetailTab = "runs" | "entries";
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

type CollisionChoice = "inherit" | "suffix" | "skip" | "overwrite";

interface SubForm {
  name: string;
  type: SubType;
  library_id: number;
  enabled: boolean;
  auto_organize: boolean;
  replace_versions: boolean;
  collision: CollisionChoice;
  deadline: string;
  schedule_mode: "interval" | "cron";
  interval_minutes: number;
  window_start: string;
  window_end: string;
  cron: string;
  timezone: string;
  notify_channel_ids: number[];
  share_url: string;
  passcode: string;
  passcode_set: boolean;
  share_subdir: string;
  quark_root: string;
  filter_mode: FilterMode;
  keyword: string;
  regex: string;
  file_extensions: string;
  max_per_run: number;
  visibility_timeout: number;
  save_subdir: string;
  save_root: boolean;
  quark_folder_id: string;
  quark_folder_path: string;
  feed_urls: string;
  p115_root: string;
  p115_folder_id: string;
  p115_folder_path: string;
  task_timeout: number;
  min_size_mb: number;
}

const ENTRY_STATUS_LABELS: Record<string, string> = {
  pending: "待处理",
  saved: "已转存",
  matched: "待整理",
  organized: "已整理",
  replaced: "已替换",
  duplicate: "重复",
  skipped: "跳过",
  review: "待确认",
  unmatched: "未识别",
  failed: "失败",
  ignored: "已忽略",
};

const COLLISION_LABELS: Record<string, string> = {
  inherit: "跟随影视库",
  suffix: "加 (1)",
  skip: "跳过",
  overwrite: "覆盖",
};

const DOWNLOAD_KIND_LABELS: Record<string, string> = {
  magnet: "磁力",
  torrent: "种子 → 磁力",
  ed2k: "电驴",
  http: "直链",
  page: "详情页",
};

const RUN_STAT_LABELS: Record<string, string> = {
  discovered: "发现",
  filtered: "过滤",
  matched_filter: "筛选后",
  transferred: "转存",
  adopted: "复用",
  organized: "整理",
  replaced: "替换",
  duplicate: "重复",
  review: "待确认",
  unmatched: "未识别",
  failed: "失败",
  skipped: "跳过",
  deferred: "延后",
  elapsed: "耗时秒",
};

function emptyForm(type: SubType = "quark", libraryId = 0): SubForm {
  return {
    name: "",
    type,
    library_id: libraryId,
    enabled: true,
    auto_organize: false,
    replace_versions: true,
    collision: "inherit",
    deadline: "",
    schedule_mode: "interval",
    interval_minutes: 60,
    window_start: "",
    window_end: "",
    cron: "0 * * * *",
    timezone: "Asia/Shanghai",
    notify_channel_ids: [],
    share_url: "",
    passcode: "",
    passcode_set: false,
    share_subdir: "",
    quark_root: "",
    filter_mode: "keyword",
    keyword: "",
    regex: "",
    file_extensions: "",
    max_per_run: 20,
    visibility_timeout: 120,
    save_subdir: "",
    save_root: true,
    quark_folder_id: "",
    quark_folder_path: "",
    feed_urls: "",
    p115_root: "",
    p115_folder_id: "",
    p115_folder_path: "",
    task_timeout: 300,
    min_size_mb: 0,
  };
}

function numberValue(value: unknown, fallback: number): number {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : fallback;
}

function formFromSubscription(sub: Subscription): SubForm {
  const config = sub.config || {};
  const schedule = sub.schedule || ({} as ScheduleConfig);
  const feeds = Array.isArray(config.feed_urls)
    ? config.feed_urls.join("\n")
    : String(config.feed_urls || "");
  return {
    name: sub.name,
    type: sub.type === "rss" ? "rss" : "quark",
    library_id: sub.library_id,
    enabled: sub.enabled,
    auto_organize: sub.auto_organize,
    replace_versions: sub.replace_versions,
    collision: ["suffix", "skip", "overwrite"].includes(String(config.collision))
      ? (String(config.collision) as CollisionChoice)
      : "inherit",
    deadline: sub.deadline || "",
    schedule_mode: schedule.mode === "cron" ? "cron" : "interval",
    interval_minutes: numberValue(schedule.interval_minutes, 60),
    window_start: schedule.window_start || "",
    window_end: schedule.window_end || "",
    cron: schedule.cron || "0 * * * *",
    timezone: schedule.timezone || "Asia/Shanghai",
    notify_channel_ids: [...(sub.notify_channel_ids || [])],
    share_url: String(config.share_url || ""),
    passcode: "",
    passcode_set: Boolean(config.passcode_set),
    share_subdir: String(config.share_subdir || ""),
    quark_root: String(config.quark_root || ""),
    filter_mode:
      config.regex && !config.keyword ? "regex" : "keyword",
    keyword: String(config.keyword || ""),
    regex: String(config.regex || ""),
    file_extensions: String(config.file_extensions || ""),
    max_per_run: numberValue(config.max_per_run, 20),
    visibility_timeout: numberValue(config.visibility_timeout, 120),
    save_subdir: String(config.save_subdir || ""),
    save_root: Boolean(config.save_root),
    quark_folder_id: String(config.quark_folder_id || ""),
    quark_folder_path: String(config.quark_folder_path || ""),
    feed_urls: feeds,
    p115_root: String(config.p115_root || ""),
    p115_folder_id: String(config.p115_folder_id || ""),
    p115_folder_path: String(config.p115_folder_path || ""),
    task_timeout: numberValue(config.task_timeout, 300),
    min_size_mb: numberValue(config.min_size_mb, 0),
  };
}

function buildConfig(form: SubForm): Record<string, unknown> {
  if (form.type === "rss") {
    return {
      feed_urls: form.feed_urls
        .split(/[\n,，;；]+/)
        .map((value) => value.trim())
        .filter(Boolean),
      keyword: form.filter_mode === "keyword" ? form.keyword : "",
      regex: form.filter_mode === "regex" ? form.regex : "",
      max_per_run: form.max_per_run,
      visibility_timeout: form.visibility_timeout,
      task_timeout: form.task_timeout,
      min_size_mb: form.min_size_mb,
      save_subdir: form.save_subdir,
      save_root: !form.save_subdir.trim(),
      p115_root: form.p115_root,
      p115_folder_id: form.p115_folder_id,
      p115_folder_path: form.p115_folder_path,
      ...(form.collision !== "inherit" ? { collision: form.collision } : {}),
    };
  }
  return {
    share_url: form.share_url,
    passcode: form.passcode,
    share_subdir: form.share_subdir,
    quark_root: form.quark_root,
    keyword: form.filter_mode === "keyword" ? form.keyword : "",
    regex: form.filter_mode === "regex" ? form.regex : "",
    file_extensions: form.file_extensions,
    max_per_run: form.max_per_run,
    visibility_timeout: form.visibility_timeout,
    save_subdir: form.save_subdir,
    save_root: !form.save_subdir.trim(),
    quark_folder_id: form.quark_folder_id,
    quark_folder_path: form.quark_folder_path,
    ...(form.collision !== "inherit" ? { collision: form.collision } : {}),
  };
}

interface ImportReport {
  recognized: string[];
  ignored: string[];
  notes?: string[];
  source?: "cinemint" | "quark-auto-save";
}

type ImportParseResult =
  | { ok: true; values: Partial<SubForm>; report: ImportReport }
  | { ok: false; error: string };

const QUARK_AUTOSAVE_IGNORED: Array<{ key: string; label: string }> = [
  { key: "savepath", label: "savepath（夸克网盘保存路径，影序转存目录规则不同）" },
  { key: "replace", label: "replace（重命名模板，由影视库命名设置统一管理）" },
  { key: "runweek", label: "runweek（每周运行日，请在执行计划中设置）" },
  { key: "startfid", label: "startfid（分享内目录 ID，请用「浏览分享」选择）" },
  { key: "ignore_extension", label: "ignore_extension（是否忽略扩展名）" },
];

function hasImportValue(value: unknown): boolean {
  if (value === null || value === undefined || value === "" || value === false) return false;
  if (Array.isArray(value)) return value.length > 0;
  if (typeof value === "object") return Object.keys(value as object).length > 0;
  return true;
}

function normalizeDeadline(raw: string): string | null {
  const value = raw.trim();
  if (!value) return null;
  if (/^\d{4}-\d{2}-\d{2}$/.test(value)) return value;
  const normalized = value.replace(/\//g, "-").replace(" ", "T");
  if (/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}/.test(normalized)) {
    return normalized.slice(0, 10);
  }
  return null;
}

const SHARE_FID_RE = /#\/list\/share\/([A-Za-z0-9_-]+)/;

function shareFidFromUrl(url: string): string {
  const match = SHARE_FID_RE.exec(url || "");
  return match ? match[1] : "";
}

function shareUrlWithFid(url: string, fid: string): string {
  const base = (url || "").split("#")[0];
  if (!fid || fid === "0") return base;
  return `${base}#/list/share/${fid}`;
}

function extractTaskObject(
  data: unknown,
): { ok: true; task: Record<string, unknown> } | { ok: false; error: string } {
  let task: unknown = data;
  if (Array.isArray(data)) {
    if (data.length !== 1) {
      return { ok: false, error: "一次只能导入一个任务，请粘贴单个任务对象" };
    }
    task = data[0];
  } else if (
    data &&
    typeof data === "object" &&
    Array.isArray((data as { tasklist?: unknown }).tasklist)
  ) {
    const list = (data as { tasklist: unknown[] }).tasklist;
    if (list.length !== 1) {
      return { ok: false, error: "一次只能导入一个任务，请只保留一个任务条目" };
    }
    task = list[0];
  }
  if (!task || typeof task !== "object" || Array.isArray(task)) {
    return { ok: false, error: "未找到任务对象" };
  }
  return { ok: true, task: task as Record<string, unknown> };
}

/** 解析影序自己的任务参数（完整配置，可跨任务快速新建）。 */
function parseCinemintTask(task: Record<string, unknown>): ImportParseResult {
  const typeRaw = String(task.type || "").toLowerCase();
  if (typeRaw !== "quark" && typeRaw !== "rss") {
    return { ok: false, error: "影序任务参数的 type 只能是 quark 或 rss" };
  }
  const rawConfig = task.config;
  if (!rawConfig || typeof rawConfig !== "object" || Array.isArray(rawConfig)) {
    return { ok: false, error: "影序任务参数缺少 config 配置对象" };
  }
  const config = rawConfig as Record<string, unknown>;
  const name = typeof task.name === "string" ? task.name.trim() : "";
  const schedule =
    task.schedule && typeof task.schedule === "object"
      ? (task.schedule as ScheduleConfig)
      : ({} as ScheduleConfig);
  const notify = Array.isArray(task.notify_channel_ids)
    ? task.notify_channel_ids.map((item) => Number(item)).filter((item) => Number.isFinite(item))
    : [];
  const fake = {
    name,
    type: typeRaw,
    enabled: task.enabled !== false,
    library_id: Number(task.library_id) || 0,
    auto_organize: Boolean(task.auto_organize),
    replace_versions: task.replace_versions !== false,
    deadline: typeof task.deadline === "string" ? task.deadline : "",
    schedule,
    notify_channel_ids: notify,
    config,
  } as unknown as Subscription;
  const values = formFromSubscription(fake);
  values.name = name;
  values.library_id = Number(task.library_id) || 0;
  const passcode = typeof config.passcode === "string" ? config.passcode : "";
  values.passcode = passcode;
  values.passcode_set = Boolean(passcode || config.passcode_set);

  const notes: string[] = [];
  if (task.passcode_set === true && !passcode) {
    notes.push("该任务参数没有携带提取码，这个分享有提取码时请在表单里重新填写。");
  }
  const ignored: string[] = [];
  const known = new Set([
    "app",
    "version",
    "name",
    "type",
    "enabled",
    "library_id",
    "library_name",
    "config",
    "schedule",
    "deadline",
    "auto_organize",
    "replace_versions",
    "notify_channel_ids",
    "passcode_set",
  ]);
  for (const key of Object.keys(task)) {
    if (!known.has(key)) ignored.push(`未知参数：${key}`);
  }
  return {
    ok: true,
    values,
    report: {
      recognized: ["影序任务参数（完整配置）"],
      ignored,
      notes,
      source: "cinemint",
    },
  };
}

/** 解析 quark-auto-save 的单个任务对象；只取影序能对应的字段，其余忽略。 */
function parseQuarkAutoSaveTask(task: Record<string, unknown>): ImportParseResult {
  const lower: Record<string, unknown> = {};
  Object.entries(task).forEach(([key, value]) => {
    lower[key.toLowerCase()] = value;
  });
  const text = (value: unknown) => (typeof value === "string" ? value.trim() : "");
  const name = text(lower.taskname) || text(lower.task_name) || text(lower.name);
  const shareUrl = text(lower.shareurl) || text(lower.share_url);
  if (!shareUrl) {
    return { ok: false, error: "未识别到分享链接（shareurl），请检查粘贴的任务参数内容" };
  }

  const values: Partial<SubForm> = {
    type: "quark",
    share_url: shareUrl,
    passcode_set: false,
  };
  const recognized: string[] = ["分享链接（shareurl）"];
  const ignored: string[] = [];

  if (name) {
    values.name = name.slice(0, 80);
    recognized.push("任务名称（taskname）");
  }
  const pattern = text(lower.pattern);
  if (pattern) {
    values.filter_mode = "regex";
    values.regex = pattern;
    recognized.push("过滤正则（pattern）");
  }
  const enddate = text(lower.enddate);
  if (enddate) {
    const normalized = normalizeDeadline(enddate);
    if (normalized) {
      values.deadline = normalized;
      recognized.push("截止日期（enddate）");
    } else {
      ignored.push("enddate（日期格式无法识别）");
    }
  }
  for (const item of QUARK_AUTOSAVE_IGNORED) {
    if (item.key in lower && hasImportValue(lower[item.key])) {
      ignored.push(item.label);
    }
  }
  const fragment = shareUrl.split("#")[1] || "";
  if (!("startfid" in lower) && /list\/share\/[A-Za-z0-9]+/.test(fragment)) {
    ignored.push("分享链接里的目录定位（请用「浏览分享」选择具体目录）");
  }
  const known = new Set([
    "taskname",
    "task_name",
    "name",
    "shareurl",
    "share_url",
    "pattern",
    "enddate",
    ...QUARK_AUTOSAVE_IGNORED.map((item) => item.key),
  ]);
  for (const key of Object.keys(lower)) {
    if (!known.has(key)) {
      ignored.push(`未知参数：${key}`);
    }
  }
  return {
    ok: true,
    values,
    report: {
      recognized,
      ignored,
      notes: [],
      source: "quark-auto-save",
    },
  };
}

function parseTaskParams(raw: string): ImportParseResult {
  let data: unknown;
  try {
    data = JSON.parse(raw);
  } catch {
    return { ok: false, error: "粘贴内容不是有效的 JSON，请检查格式" };
  }
  const extracted = extractTaskObject(data);
  if (!extracted.ok) return extracted;
  const task = extracted.task;
  const typeRaw = String(task.type || "").toLowerCase();
  const appRaw = String(task.app || "").toLowerCase();
  const isCinemint =
    appRaw === "cinemint" ||
    appRaw === "cineflow" ||
    (task.config &&
      typeof task.config === "object" &&
      !Array.isArray(task.config) &&
      (typeRaw === "quark" || typeRaw === "rss"));
  return isCinemint ? parseCinemintTask(task) : parseQuarkAutoSaveTask(task);
}

/** 把已有订阅导出成影序任务参数（不含提取码等敏感信息）。 */
function taskParamsFromSubscription(subscription: Subscription): Record<string, unknown> {
  const config = { ...(subscription.config || {}) };
  return {
    app: "cinemint",
    version: 1,
    name: subscription.name,
    type: subscription.type === "rss" ? "rss" : "quark",
    enabled: subscription.enabled,
    library_id: subscription.library_id,
    library_name: subscription.library_name,
    config,
    schedule: subscription.schedule,
    deadline: subscription.deadline || "",
    auto_organize: subscription.auto_organize,
    replace_versions: subscription.replace_versions,
    notify_channel_ids: subscription.notify_channel_ids,
    passcode_set: Boolean(subscription.config?.passcode_set || subscription.config?.passcode),
  };
}

function scheduleLabel(schedule?: ScheduleConfig): string {
  if (!schedule) return "—";
  if (schedule.mode === "cron") return `Cron ${schedule.cron}`;
  const window =
    schedule.window_start && schedule.window_end
      ? ` · ${schedule.window_start}–${schedule.window_end}`
      : "";
  return `每 ${schedule.interval_minutes} 分钟${window}`;
}

function triggerLabel(trigger: string): string {
  if (trigger === "manual") return "手动";
  if (trigger === "schedule" || trigger === "scheduler") return "定时";
  return trigger || "—";
}

function statsLine(stats?: Record<string, number>): string {
  if (!stats) return "";
  return Object.entries(stats)
    .filter(([key, value]) => RUN_STAT_LABELS[key] && value)
    .map(([key, value]) => `${RUN_STAT_LABELS[key]} ${value}`)
    .join(" · ");
}

function FormSection({
  title,
  description,
  action,
  children,
}: {
  title: string;
  description?: string;
  action?: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <section className="rounded-2xl border border-white/[0.07] bg-white/[0.02] p-4">
      <div className="mb-3 flex flex-wrap items-start justify-between gap-2">
        <div>
          <h4 className="text-xs font-semibold text-slate-100">{title}</h4>
          {description ? (
            <p className="mt-1 text-[11px] leading-relaxed text-slate-500">{description}</p>
          ) : null}
        </div>
        {action}
      </div>
      {children}
    </section>
  );
}

export default function SubscriptionsPage() {
  const { data: subscriptions, loading, reload } = useAsync<Subscription[]>(
    "/api/subscriptions",
    [],
    { pollMs: 5000 },
  );
  const { data: libraries } = useAsync<Library[]>("/api/libraries");
  const { data: channelPayload } = useAsync<{ channels: NotificationChannel[] }>(
    "/api/notifications",
  );
  const toast = useToast();

  const [filter, setFilter] = useState<"all" | "quark" | "rss" | "enabled">("all");
  const [formOpen, setFormOpen] = useState(false);
  const [editing, setEditing] = useState<Subscription | null>(null);
  const [form, setForm] = useState<SubForm>(emptyForm());
  const [saving, setSaving] = useState(false);
  const [preview, setPreview] = useState<SubscriptionPreview | null>(null);
  const [previewing, setPreviewing] = useState(false);
  const [pickerOpen, setPickerOpen] = useState(false);
  const [sharePickerOpen, setSharePickerOpen] = useState(false);
  const [actingId, setActingId] = useState<number | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<Subscription | null>(null);
  const [detailId, setDetailId] = useState<number | null>(null);
  const [detailTab, setDetailTab] = useState<DetailTab>("runs");
  const [entryFilter, setEntryFilter] = useState("all");
  const [runLogs, setRunLogs] = useState<SubscriptionRun | null>(null);
  const [logsLoading, setLogsLoading] = useState(false);
  const [importOpen, setImportOpen] = useState(false);
  const [importText, setImportText] = useState("");
  const [importReport, setImportReport] = useState<ImportReport | null>(null);
  const [paramsOpen, setParamsOpen] = useState(false);
  const [paramsSub, setParamsSub] = useState<Subscription | null>(null);
  const [paramsText, setParamsText] = useState("");
  const [paramsLoading, setParamsLoading] = useState(false);
  const [shareResolving, setShareResolving] = useState(false);
  const [shareResolveError, setShareResolveError] = useState("");
  const shareResolveRef = useRef<{ key: string; path: string } | null>(null);

  const channels = channelPayload?.channels || [];

  const { data: detailRuns, reload: reloadDetailRuns } = useAsync<SubscriptionRun[]>(
    detailId ? `/api/subscriptions/${detailId}/runs?limit=30` : null,
    [detailId],
    { pollMs: 4000 },
  );
  const { data: detailEntries } = useAsync<SubscriptionEntry[]>(
    detailId ? `/api/subscriptions/${detailId}/entries?limit=300` : null,
    [detailId],
    { pollMs: 8000 },
  );

  const detailSub = useMemo(
    () => (subscriptions || []).find((item) => item.id === detailId) || null,
    [subscriptions, detailId],
  );

  useEffect(() => {
    if (detailId && subscriptions && !detailSub) setDetailId(null);
  }, [detailId, detailSub, subscriptions]);

  const visible = (subscriptions || []).filter((sub) => {
    if (filter === "enabled") return sub.enabled;
    if (filter === "quark" || filter === "rss") return sub.type === filter;
    return true;
  });

  const pickLibrary = (type: SubType): number => {
    const list = libraries || [];
    const hint = type === "quark" ? "夸克" : "115";
    return (
      (list.find((item) => String(item.root_path || "").includes(hint)) || list[0])?.id || 0
    );
  };

  const openCreate = (type: SubType = "quark") => {
    setEditing(null);
    setForm(emptyForm(type, pickLibrary(type)));
    setPreview(null);
    setPickerOpen(false);
    setSharePickerOpen(false);
    setImportReport(null);
    setFormOpen(true);
  };

  const openEdit = (sub: Subscription) => {
    setEditing(sub);
    setForm(formFromSubscription(sub));
    setPreview(null);
    setPickerOpen(false);
    setSharePickerOpen(false);
    setImportReport(null);
    setFormOpen(true);
  };

  const patch = (values: Partial<SubForm>) =>
    setForm((current) => ({ ...current, ...values }));

  const changeType = (type: SubType) => {
    if (editing) return;
    setForm((current) => ({ ...current, type, library_id: pickLibrary(type) }));
    setPreview(null);
    setPickerOpen(false);
    setSharePickerOpen(false);
  };

  const closeForm = () => {
    if (saving) return;
    // 目录 / 分享选择器是嵌在订阅表单上的二级弹窗：两者都监听 Escape，
    // 先只关闭选择器，避免把整个订阅表单一起关掉、丢掉未保存内容。
    if (pickerOpen || sharePickerOpen) {
      setPickerOpen(false);
      setSharePickerOpen(false);
      return;
    }
    setFormOpen(false);
  };

  const applyParsedTask = (result: Extract<ImportParseResult, { ok: true }>) => {
    const type: SubType = result.values.type === "rss" ? "rss" : "quark";
    const list = libraries || [];
    let libraryId = Number(result.values.library_id) || 0;
    const report: ImportReport = { ...result.report, notes: [...(result.report.notes || [])] };
    if (libraryId && !list.some((library) => library.id === libraryId)) {
      libraryId = 0;
      report.notes?.push("原影视库在当前影序中不存在，已选择默认影视库，请确认。");
    }
    if (!libraryId) {
      libraryId = pickLibrary(type);
    }
    setEditing(null);
    setForm({ ...emptyForm(type, libraryId), ...result.values, type, library_id: libraryId });
    setPreview(null);
    setPickerOpen(false);
    setSharePickerOpen(false);
    setImportReport(report);
    setImportOpen(false);
    setImportText("");
    setParamsOpen(false);
    setFormOpen(true);
    toast.success("已识别任务参数，请补全剩余配置");
  };

  const applyImport = () => {
    const result = parseTaskParams(importText);
    if (!result.ok) {
      toast.error(result.error);
      return;
    }
    applyParsedTask(result);
  };

  const openParams = async (subscription: Subscription) => {
    setParamsSub(subscription);
    setParamsText(JSON.stringify(taskParamsFromSubscription(subscription), null, 2));
    setParamsOpen(true);
    setParamsLoading(true);
    try {
      const full = await api.get<Subscription>(
        `/api/subscriptions/${subscription.id}/task-params`,
      );
      setParamsText(JSON.stringify(taskParamsFromSubscription(full), null, 2));
    } catch (err) {
      toast.error(
        err instanceof Error
          ? `读取完整任务参数失败：${err.message}`
          : "读取完整任务参数失败，已使用列表中的配置（不含提取码）",
      );
    } finally {
      setParamsLoading(false);
    }
  };

  const copyParams = async () => {
    try {
      await navigator.clipboard.writeText(paramsText);
      toast.success("任务参数已复制");
    } catch {
      toast.error("复制失败，请手动全选文本复制");
    }
  };

  const createFromParams = () => {
    const result = parseTaskParams(paramsText);
    if (!result.ok) {
      toast.error(result.error);
      return;
    }
    applyParsedTask(result);
  };

  const editShareUrl = (value: string) => {
    const oldUrl = form.share_url;
    if (value === oldUrl) return;
    shareResolveRef.current = null;
    setShareResolveError("");
    patch({ share_url: value, share_subdir: "" });
  };

  const resolveShareFolder = async (
    silent = false,
  ): Promise<{ ok: true; path: string; fid: string } | { ok: false; error: string }> => {
    if (form.type !== "quark") {
      return { ok: true, path: form.share_subdir, fid: "" };
    }
    const fid = shareFidFromUrl(form.share_url);
    // 链接里没有 #/list/share/<fid> 时保留手工 / 已保存的分享内目录。
    if (!fid) return { ok: true, path: form.share_subdir, fid: "" };
    const key = `${fid}|${form.share_url.split("#")[0]}|${form.passcode}|${
      form.passcode_set ? 1 : 0
    }|${editing?.id || 0}`;
    if (shareResolveRef.current?.key === key) {
      return { ok: true, path: shareResolveRef.current.path, fid };
    }
    setShareResolving(true);
    try {
      const result = await api.post<{ path: string; fid: string }>(
        "/api/subscriptions/quark/share/resolve",
        {
          share_url: form.share_url,
          passcode: form.passcode,
          fid,
          subscription_id: editing?.id ?? null,
        },
      );
      const path = result?.path || "";
      shareResolveRef.current = { key, path };
      setShareResolveError("");
      patch({ share_subdir: path });
      return { ok: true, path, fid: result?.fid || fid };
    } catch (err) {
      const message =
        err instanceof Error ? err.message : "无法从分享链接识别转存目录";
      setShareResolveError(message);
      if (!silent) toast.error(message);
      return { ok: false, error: message };
    } finally {
      setShareResolving(false);
    }
  };

  useEffect(() => {
    if (!formOpen || form.type !== "quark") return;
    if (!shareFidFromUrl(form.share_url)) return;
    const timer = window.setTimeout(() => {
      void resolveShareFolder(true);
    }, 450);
    return () => window.clearTimeout(timer);
    // resolveShareFolder 读取当前表单快照；只依赖会影响分享链接解析的输入。
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [formOpen, form.type, form.share_url, form.passcode]);

  const save = async () => {
    if (!form.name.trim()) {
      toast.error("请填写订阅名称");
      return;
    }
    if (!form.library_id) {
      toast.error("请选择要整理的影视库");
      return;
    }
    if (form.type === "quark" && !form.share_url.trim()) {
      toast.error("请填写夸克分享链接");
      return;
    }
    if (form.type === "rss" && !form.feed_urls.trim()) {
      toast.error("请填写至少一个 RSS 地址");
      return;
    }
    setSaving(true);
    try {
      let values = form;
      if (form.type === "quark") {
        const folder = await resolveShareFolder(true);
        if (!folder.ok) throw new Error(folder.error);
        values = { ...form, share_subdir: folder.path };
      }
      const payload = {
        name: values.name.trim(),
        library_id: values.library_id,
        type: values.type,
        enabled: values.enabled,
        config: buildConfig(values),
        schedule: {
          mode: values.schedule_mode,
          interval_minutes: values.interval_minutes,
          window_start: values.window_start,
          window_end: values.window_end,
          cron: values.cron,
          timezone: values.timezone,
        },
        deadline: values.deadline,
        auto_organize: values.auto_organize,
        replace_versions: values.replace_versions,
        notify_channel_ids: values.notify_channel_ids,
      };
      if (editing) {
        await api.put(`/api/subscriptions/${editing.id}`, payload);
        toast.success("订阅已更新");
      } else {
        await api.post("/api/subscriptions", payload);
        toast.success("订阅已创建，稍后自动检查更新");
      }
      setFormOpen(false);
      setSharePickerOpen(false);
      reload();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "保存失败");
    } finally {
      setSaving(false);
    }
  };

  const runPreview = async () => {
    setPreviewing(true);
    setPreview(null);
    try {
      let values = form;
      if (form.type === "quark") {
        const folder = await resolveShareFolder(true);
        if (!folder.ok) throw new Error(folder.error);
        values = { ...form, share_subdir: folder.path };
      }
      const result = await api.post<SubscriptionPreview>("/api/subscriptions/preview", {
        name: values.name.trim() || "预览",
        type: values.type,
        config: buildConfig(values),
      });
      setPreview(result);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "预览失败");
    } finally {
      setPreviewing(false);
    }
  };

  const editSaveSubdir = (value: string) => {
    const relative = value.replace(/^\/+/, "");
    patch({
      save_subdir: relative,
      // 留空表示影视库根目录；填写路径时按该相对路径保存。
      save_root: !relative.trim(),
      quark_folder_id: "",
      quark_folder_path: "",
      p115_folder_id: "",
      p115_folder_path: "",
    });
  };

  const runNow = async (sub: Subscription) => {
    setActingId(sub.id);
    try {
      await api.post(`/api/subscriptions/${sub.id}/run`);
      toast.success("已加入执行队列");
      reload();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "启动失败");
    } finally {
      setActingId(null);
    }
  };

  const remove = async () => {
    if (!deleteTarget) return;
    setActingId(deleteTarget.id);
    try {
      await api.del(`/api/subscriptions/${deleteTarget.id}`);
      toast.success("订阅已删除");
      if (detailId === deleteTarget.id) setDetailId(null);
      setDeleteTarget(null);
      reload();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "删除失败");
    } finally {
      setActingId(null);
    }
  };

  const openDetail = (sub: Subscription) => {
    setDetailId(sub.id);
    setDetailTab("runs");
    setEntryFilter("all");
    setRunLogs(null);
  };

  const loadRunLogs = async (runId: number) => {
    if (runLogs?.id === runId) {
      setRunLogs(null);
      return;
    }
    setLogsLoading(true);
    try {
      setRunLogs(await api.get<SubscriptionRun>(`/api/subscriptions/runs/${runId}`));
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "读取日志失败");
    } finally {
      setLogsLoading(false);
    }
  };

  const filteredEntries = (detailEntries || []).filter((entry) => {
    if (entryFilter === "all") return true;
    return entry.status === entryFilter;
  });

  return (
    <div className="mx-auto max-w-6xl space-y-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-lg font-semibold text-white">订阅管理</h1>
          <p className="mt-1 text-xs text-slate-500">
            定时检查夸克分享或 RSS 更新，转存到暂存目录后自动扫描、匹配与整理。
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <Button onClick={reload}>
            <RefreshCw size={15} /> 刷新
          </Button>
          <Button onClick={() => setImportOpen(true)} title="从影序任务参数快速新建">
            <ClipboardPaste size={15} /> 粘贴任务参数
          </Button>
          <Button onClick={() => openCreate("rss")}>
            <Rss size={15} /> RSS 订阅
          </Button>
          <Button variant="primary" onClick={() => openCreate("quark")}>
            <Plus size={15} /> 夸克订阅
          </Button>
        </div>
      </div>

      <div className="flex flex-wrap gap-2">
        {[
          { key: "all" as const, label: "全部" },
          { key: "quark" as const, label: "夸克分享" },
          { key: "rss" as const, label: "RSS / 115" },
          { key: "enabled" as const, label: "已启用" },
        ].map((item) => (
          <button
            key={item.key}
            onClick={() => setFilter(item.key)}
            className={clsx(
              "rounded-full border px-3 py-1.5 text-xs transition",
              filter === item.key
                ? "border-mint-500/40 bg-mint-500/10 text-mint-300"
                : "border-white/10 bg-white/[0.03] text-slate-400 hover:text-slate-200",
            )}
          >
            {item.label}
          </button>
        ))}
        <span className="ml-auto self-center text-[11px] text-slate-500">
          共 {subscriptions?.length ?? 0} 个订阅
        </span>
      </div>

      {loading && !subscriptions ? (
        <div className="flex h-52 items-center justify-center gap-2 text-sm text-slate-400">
          <Spinner /> 加载订阅…
        </div>
      ) : visible.length ? (
        <div className="space-y-3">
          {visible.map((sub) => {
            const counts = sub.entry_counts || {};
            const active = sub.active_run;
            return (
              <Card key={sub.id} className="space-y-3 p-4 sm:p-5">
                <div className="flex flex-wrap items-start justify-between gap-3">
                  <div className="flex min-w-0 flex-1 items-start gap-3">
                    <div
                      className={clsx(
                        "flex h-10 w-10 shrink-0 items-center justify-center rounded-xl border",
                        sub.type === "rss"
                          ? "border-violet-500/25 bg-violet-500/10 text-violet-300"
                          : "border-sky-500/25 bg-sky-500/10 text-sky-300",
                      )}
                    >
                      {sub.type === "rss" ? <Rss size={18} /> : <CloudDownload size={18} />}
                    </div>
                    <div className="min-w-0 flex-1">
                      <div className="flex flex-wrap items-center gap-2">
                        <button
                          className="truncate text-sm font-semibold text-white transition hover:text-mint-300"
                          onClick={() => openDetail(sub)}
                        >
                          {sub.name}
                        </button>
                        <Badge tone={sub.type === "rss" ? "violet" : "sky"}>
                          {sub.type === "rss" ? "RSS / 115" : "夸克分享"}
                        </Badge>
                        <Badge tone={sub.enabled ? "mint" : "slate"}>
                          {sub.enabled ? "已启用" : "已暂停"}
                        </Badge>
                        {active ? <Badge tone="sky">{statusLabel(active.status)}</Badge> : null}
                      </div>
                      <div className="mt-1 text-[11px] text-slate-500">
                        {sub.library_name} · {scheduleLabel(sub.schedule)} · {sub.schedule.timezone}
                      </div>
                      <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-[11px] text-slate-400">
                        <span>
                          下次运行：
                          {sub.enabled && sub.next_run_at
                            ? formatDateTime(sub.next_run_at)
                            : "—"}
                        </span>
                        <span>自动整理：{sub.auto_organize ? "开" : "关"}</span>
                        <span>V2 替换：{sub.replace_versions ? "开" : "关"}</span>
                        <span>同名：{COLLISION_LABELS[String(sub.config?.collision || "inherit")] || "跟随影视库"}</span>
                        {sub.deadline ? <span>截止：{sub.deadline}</span> : null}
                      </div>
                      <div className="mt-2 flex flex-wrap gap-1.5">
                        {["organized", "replaced", "review", "unmatched", "duplicate", "skipped", "failed"].map(
                          (status) =>
                            counts[status] ? (
                              <Badge key={status} tone={status === "failed" ? "rose" : "slate"}>
                                {ENTRY_STATUS_LABELS[status] || status} {counts[status]}
                              </Badge>
                            ) : null,
                        )}
                        {!Object.keys(counts).length ? (
                          <span className="text-[11px] text-slate-600">还没有处理记录</span>
                        ) : null}
                      </div>
                      {sub.last_message ? (
                        <div className="mt-2 line-clamp-2 text-[11px] leading-relaxed text-slate-500">
                          上次：{formatTime(sub.last_run_at)} · {sub.last_message}
                        </div>
                      ) : null}
                    </div>
                  </div>
                  <div className="flex shrink-0 items-center gap-1.5">
                    <Button
                      className="px-2.5 py-1.5 text-xs"
                      loading={actingId === sub.id}
                      onClick={() => runNow(sub)}
                      disabled={Boolean(active)}
                    >
                      <Play size={13} /> 运行
                    </Button>
                    <Button
                      className="px-2 py-1.5 text-xs"
                      onClick={() => openParams(sub)}
                      title="提取任务参数（可复制或快速新建）"
                    >
                      <FileJson size={13} />
                    </Button>
                    <Button
                      className="px-2 py-1.5 text-xs"
                      onClick={() => openEdit(sub)}
                      title="编辑订阅"
                    >
                      <Pencil size={13} />
                    </Button>
                    <Button
                      className="px-2 py-1.5 text-xs"
                      onClick={() => setDeleteTarget(sub)}
                      title="删除订阅"
                    >
                      <Trash2 size={13} />
                    </Button>
                  </div>
                </div>

                {active ? (
                  <div className="rounded-xl border border-sky-500/20 bg-sky-500/[0.05] px-3 py-2">
                    <div className="mb-1.5 flex items-center justify-between text-[11px] text-sky-200">
                      <span className="truncate">
                        {statusLabel(active.status)} · {active.message || "正在执行…"}
                      </span>
                      <span>{Math.round(active.progress || 0)}%</span>
                    </div>
                    <Progress value={active.progress} className="h-1" />
                  </div>
                ) : null}

                <div className="flex items-center justify-between border-t border-white/[0.05] pt-2.5">
                  <span className="text-[10px] text-slate-600">
                    上次执行 {formatTime(sub.last_run_at)}
                  </span>
                  <button
                    className="inline-flex items-center gap-1 text-[11px] text-slate-400 transition hover:text-mint-300"
                    onClick={() => openDetail(sub)}
                  >
                    <History size={12} /> 运行记录与条目 <ChevronRight size={12} />
                  </button>
                </div>
              </Card>
            );
          })}
        </div>
      ) : (
        <EmptyState
          icon={<CloudDownload size={28} />}
          title={subscriptions?.length ? "没有符合筛选条件的订阅" : "还没有订阅"}
          description="夸克订阅会定时检查分享更新；RSS 订阅会通过 115 云添加下载新条目。两者都会复用影视库的扫描与整理流程。"
          action={
            <div className="flex gap-2">
              <Button onClick={() => openCreate("quark")}>
                <CloudDownload size={14} /> 新建夸克订阅
              </Button>
              <Button onClick={() => openCreate("rss")}>
                <Rss size={14} /> 新建 RSS 订阅
              </Button>
            </div>
          }
        />
      )}

      <Modal
        open={formOpen}
        onClose={closeForm}
        title={editing ? `编辑订阅 · ${editing.name}` : "新建订阅"}
        width="max-w-4xl"
        footer={
          <>
            <span className="mr-auto text-[10px] text-slate-500">
              保存后按计划自动执行；也可以随时手动运行。
            </span>
            <Button onClick={closeForm}>取消</Button>
            <Button onClick={runPreview} loading={previewing}>
              <RefreshCw size={14} /> 预览
            </Button>
            <Button variant="primary" onClick={save} loading={saving}>
              保存
            </Button>
          </>
        }
      >
        <div className="space-y-4">
          {importReport ? (
            <div className="rounded-2xl border border-sky-500/25 bg-sky-500/[0.06] px-3.5 py-3 text-[11px] leading-relaxed text-slate-300">
              <div className="font-medium text-sky-200">
                {importReport.source === "cinemint"
                  ? "已从影序任务参数识别并填入"
                  : "已从任务参数识别并填入"}
              </div>
              <div className="mt-1">
                识别：{importReport.recognized.length ? importReport.recognized.join("、") : "无"}
              </div>
              {importReport.ignored.length ? (
                <div className="mt-1 text-amber-200/90">
                  已忽略：{importReport.ignored.join("、")}
                </div>
              ) : null}
              {importReport.notes?.length ? (
                <div className="mt-1 text-amber-200/90">提示：{importReport.notes.join(" ")}</div>
              ) : null}
              <div className="mt-1 text-slate-500">
                请补全影视库、确认转存目录（分享内目录可用「浏览分享」选择）、检查执行计划与自动整理后再保存。
              </div>
            </div>
          ) : null}
          <FormSection title="基本信息" description="订阅名称用于生成默认暂存目录，也会出现在通知标题中。">
            <div className="grid gap-3 sm:grid-cols-2">
              <Field label="订阅名称">
                <Input
                  value={form.name}
                  maxLength={80}
                  onChange={(event) => patch({ name: event.target.value })}
                  placeholder="例如：某番剧 追更"
                />
              </Field>
              <Field label="整理到影视库">
                <Select
                  value={form.library_id}
                  onChange={(event) => {
                    patch({ library_id: Number(event.target.value) });
                    setPickerOpen(false);
                  }}
                >
                  <option value={0}>请选择影视库</option>
                  {(libraries || []).map((library) => (
                    <option key={library.id} value={library.id}>
                      {library.name} · {library.root_path}
                    </option>
                  ))}
                </Select>
              </Field>
            </div>
            {!editing ? (
              <div className="mt-3 grid gap-2 sm:grid-cols-2">
                {[
                  {
                    type: "quark" as const,
                    label: "夸克分享",
                    hint: "转存到夸克暂存目录，适合夸克网盘库",
                    icon: CloudDownload,
                  },
                  {
                    type: "rss" as const,
                    label: "RSS / 115",
                    hint: "通过 115 云添加下载，适合 115 库",
                    icon: Rss,
                  },
                ].map((item) => (
                  <button
                    key={item.type}
                    type="button"
                    onClick={() => changeType(item.type)}
                    className={clsx(
                      "flex items-start gap-2.5 rounded-xl border px-3.5 py-3 text-left transition",
                      form.type === item.type
                        ? "border-mint-500/40 bg-mint-500/[0.08]"
                        : "border-white/[0.07] bg-white/[0.02] hover:border-white/15",
                    )}
                  >
                    <item.icon
                      size={17}
                      className={form.type === item.type ? "text-mint-300" : "text-slate-400"}
                    />
                    <span>
                      <span className="block text-xs font-medium text-slate-200">{item.label}</span>
                      <span className="mt-0.5 block text-[10px] text-slate-500">{item.hint}</span>
                    </span>
                  </button>
                ))}
              </div>
            ) : (
              <div className="mt-3 text-[11px] text-slate-500">
                订阅类型创建后不可修改。如需更换来源，请新建订阅。
              </div>
            )}
            <div className="mt-3">
              <Switch
                checked={form.enabled}
                onChange={(value) => patch({ enabled: value })}
                label="启用定时检查"
                hint="关闭后不会自动运行，仍可手动执行"
              />
            </div>
          </FormSection>

          {form.type === "quark" ? (
            <FormSection title="夸克分享" description="支持夸克分享链接与提取码；暂存目录位于影视库根目录下。">
              <div className="space-y-3">
                <Field
                  label="分享链接"
                  hint="直接粘贴带 #/list/share/<目录ID> 的分享链接，粘贴后会自动识别目录；也可以点右侧按钮从分享文件树选择。"
                >
                  <div className="flex gap-2">
                    <Input
                      className="min-w-0"
                      value={form.share_url}
                      onChange={(event) => editShareUrl(event.target.value)}
                      onBlur={() => void resolveShareFolder(false)}
                      placeholder="https://pan.quark.cn/s/xxxxxxxx 或 ...#/list/share/xxxxxxxx"
                    />
                    <Button
                      type="button"
                      className="shrink-0 px-3 py-2 text-xs"
                      onClick={() => setSharePickerOpen(true)}
                      title="浏览分享文件，选择转存目录"
                    >
                      <FolderOpen size={13} />
                    </Button>
                  </div>
                  <div className="mt-1.5 flex flex-wrap items-center gap-x-2 gap-y-1 text-[10px] leading-relaxed text-slate-500">
                    <span>当前转存范围：</span>
                    <span className="text-slate-300">
                      {form.share_subdir || "整个分享（含子目录）"}
                    </span>
                    {shareResolving ? <span className="text-sky-300">正在识别目录…</span> : null}
                    {shareResolveError ? (
                      <span className="text-rose-300">自动识别失败：{shareResolveError}</span>
                    ) : null}
                  </div>
                </Field>
                <Field
                  label="提取码"
                  hint={
                    form.passcode_set
                      ? "已保存提取码，留空保持不变；重新填写可覆盖。"
                      : "没有提取码可以留空。"
                  }
                >
                  <Input
                    value={form.passcode}
                    onChange={(event) => patch({ passcode: event.target.value })}
                    placeholder={form.passcode_set ? "••••（留空保持不变）" : "可选"}
                    autoComplete="new-password"
                  />
                </Field>
                <Field
                  label="转存目录"
                  hint={
                    form.quark_folder_path
                      ? `影视库内路径：${form.save_subdir || "根目录"}，保存后会从该路径扫描整理。`
                      : "可直接浏览夸克网盘目录；留空直接保存到影视库根目录。"
                  }
                >
                  <div className="flex gap-2">
                    <Input
                      value={
                        form.quark_folder_path
                          ? `/${form.quark_folder_path}`
                          : form.save_subdir
                      }
                      onChange={(event) => editSaveSubdir(event.target.value)}
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
                        ? "关键词过滤：多个关键词用逗号分隔，需全部命中。"
                        : "正则过滤：匹配文件名，留空表示不启用。"
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
                            : "例如：S\\d{2}E\\d{2}"
                        }
                      />
                    </div>
                  </Field>
                  <Field label="扩展名" hint="逗号分隔；留空使用视频扩展名默认集合。">
                    <Input
                      value={form.file_extensions}
                      onChange={(event) => patch({ file_extensions: event.target.value })}
                      placeholder=".mkv,.mp4"
                    />
                  </Field>
                  <div className="grid grid-cols-2 gap-3">
                    <Field label="每次上限">
                      <Input
                        type="number"
                        min="1"
                        max="200"
                        value={form.max_per_run}
                        onChange={(event) => patch({ max_per_run: Number(event.target.value) })}
                      />
                    </Field>
                    <Field label="同步等待（秒）">
                      <Input
                        type="number"
                        min="10"
                        max="900"
                        value={form.visibility_timeout}
                        onChange={(event) =>
                          patch({ visibility_timeout: Number(event.target.value) })
                        }
                      />
                    </Field>
                  </div>
                </div>
              </div>
            </FormSection>
          ) : (
            <FormSection title="RSS 与 115" description="支持 RSS 2.0 / Atom / RDF 与蜜柑番剧 RSS；通过 115 云添加下载。">
              <div className="space-y-3">
                <Field label="RSS 地址" hint="每行一个，最多 20 个。留空会在保存时被拒绝。">
                  <textarea
                    className="input min-h-[92px] resize-y py-2 text-xs leading-relaxed"
                    value={form.feed_urls}
                    onChange={(event) => patch({ feed_urls: event.target.value })}
                    placeholder={"https://mikanani.me/RSS/MyBangumi?token=...\nhttps://example.com/feed.xml"}
                  />
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
                      onChange={(event) => editSaveSubdir(event.target.value)}
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
                            ? "例如：[SubGroup],1080p"
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
                  <div className="grid grid-cols-2 gap-3">
                    <Field label="每次上限" hint="0 表示不限制。">
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
                    <Field label="115 超时（秒）">
                      <Input
                        type="number"
                        min="10"
                        max="3600"
                        value={form.task_timeout}
                        onChange={(event) => patch({ task_timeout: Number(event.target.value) })}
                      />
                    </Field>
                  </div>
                </div>
              </div>
            </FormSection>
          )}

          <CloudFolderPicker
            open={pickerOpen}
            kind={form.type === "rss" ? "p115" : "quark"}
            libraryId={form.library_id}
            root={form.type === "rss" ? form.p115_root : form.quark_root}
            initialFid={
              form.type === "rss" ? form.p115_folder_id : form.quark_folder_id
            }
            initialPath={
              form.type === "rss" ? form.p115_folder_path : form.quark_folder_path
            }
            onClose={() => setPickerOpen(false)}
            onSelect={(value) => {
              if (form.type === "rss") {
                patch({
                  save_subdir: value.libraryPath,
                  save_root: !value.libraryPath,
                  p115_folder_id: value.fid,
                  p115_folder_path: value.path,
                });
              } else {
                patch({
                  save_subdir: value.libraryPath,
                  save_root: !value.libraryPath,
                  quark_folder_id: value.fid,
                  quark_folder_path: value.path,
                });
              }
              setPickerOpen(false);
            }}
          />

          <QuarkSharePicker
            open={form.type === "quark" && sharePickerOpen}
            shareUrl={form.share_url}
            passcode={form.passcode}
            subscriptionId={editing?.id}
            initialPath={form.share_subdir}
            initialFid={shareFidFromUrl(form.share_url)}
            onClose={() => setSharePickerOpen(false)}
            onSelect={(path, fid) => {
              shareResolveRef.current = null;
              patch({
                share_subdir: path,
                share_url: shareUrlWithFid(form.share_url, fid),
              });
              setSharePickerOpen(false);
            }}
          />

          {preview ? (
            <FormSection
              title="预览结果"
              description={`共 ${preview.total} 个条目，过滤后 ${preview.matched} 个匹配${preview.truncated ? "（仅展示前 200 条）" : ""}。预览不会下载或保存文件。`}
              action={
                <Button className="px-2 py-1 text-[11px]" onClick={() => setPreview(null)}>
                  收起
                </Button>
              }
            >
              {preview.feeds?.length ? (
                <div className="mb-2 space-y-1">
                  {preview.feeds.map((feed) => (
                    <div
                      key={feed.url}
                      className={clsx(
                        "rounded-lg border px-2.5 py-1.5 text-[10px]",
                        feed.error
                          ? "border-rose-500/25 bg-rose-500/[0.06] text-rose-200"
                          : "border-white/[0.06] bg-white/[0.02] text-slate-400",
                      )}
                    >
                      <span className="truncate">{feed.title || feed.url}</span>
                      <span className="ml-2 text-slate-500">
                        {feed.error ? feed.error : `${feed.count} 条`}
                      </span>
                    </div>
                  ))}
                </div>
              ) : null}
              {preview.samples.length ? (
                <div className="max-h-64 overflow-y-auto rounded-xl border border-white/[0.06]">
                  <table className="w-full text-left text-[11px]">
                    <thead className="sticky top-0 bg-ink-800/90 text-slate-500">
                      <tr>
                        <th className="px-2.5 py-1.5 font-normal">名称</th>
                        <th className="px-2.5 py-1.5 font-normal">识别</th>
                        <th className="px-2.5 py-1.5 font-normal">来源</th>
                      </tr>
                    </thead>
                    <tbody>
                      {preview.samples.map((sample, index) => (
                        <tr
                          key={`${sample.fid || sample.guid || index}`}
                          className="border-t border-white/[0.04] text-slate-300"
                        >
                          <td className="max-w-[360px] px-2.5 py-1.5">
                            <div className="truncate">{sample.name || sample.title}</div>
                            {sample.path ? (
                              <div className="truncate text-[10px] text-slate-500">{sample.path}</div>
                            ) : null}
                          </td>
                          <td className="whitespace-nowrap px-2.5 py-1.5 text-slate-400">
                            {sample.title_parsed || sample.title || "—"}
                            {sample.season != null || sample.episode != null
                              ? ` · ${seasonEpisode(sample.season, sample.episode)}`
                              : ""}
                            {sample.version_label ? ` · ${sample.version_label}` : ""}
                          </td>
                          <td className="whitespace-nowrap px-2.5 py-1.5 text-slate-500">
                            {DOWNLOAD_KIND_LABELS[sample.download_kind || ""] ||
                              sample.download_kind ||
                              (sample.fid ? "夸克文件" : "—")}
                            {sample.size ? ` · ${formatSize(sample.size)}` : ""}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              ) : (
                <div className="rounded-xl border border-dashed border-white/10 px-3 py-6 text-center text-[11px] text-slate-500">
                  没有匹配到条目。可以调整关键词、正则或扩展名后重试。
                </div>
              )}
            </FormSection>
          ) : null}

          <FormSection title="执行计划" description="支持固定间隔与标准 5 段 cron；所有时间按下面的时区计算。">
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
              {form.schedule_mode === "interval" ? (
                <>
                  <Field label="间隔（分钟）" hint="最小 5 分钟，最大 7 天。">
                    <Input
                      type="number"
                      min="5"
                      max="10080"
                      value={form.interval_minutes}
                      onChange={(event) =>
                        patch({ interval_minutes: Number(event.target.value) })
                      }
                    />
                  </Field>
                  <div className="grid grid-cols-2 gap-3">
                    <Field label="每日开始">
                      <Input
                        type="time"
                        value={form.window_start}
                        onChange={(event) => patch({ window_start: event.target.value })}
                      />
                    </Field>
                    <Field label="每日结束">
                      <Input
                        type="time"
                        value={form.window_end}
                        onChange={(event) => patch({ window_end: event.target.value })}
                      />
                    </Field>
                  </div>
                </>
              ) : (
                <Field
                  label="Cron 表达式"
                  hint="分 时 日 月 周，支持 *、范围、步长与 @hourly 等宏。"
                  className="sm:col-span-1"
                >
                  <Input
                    value={form.cron}
                    onChange={(event) => patch({ cron: event.target.value })}
                    placeholder="0 * * * *"
                  />
                </Field>
              )}
              <Field label="截止日期（可选）" hint="到截止时间后不再自动运行。">
                <Input
                  type="date"
                  value={form.deadline}
                  onChange={(event) => patch({ deadline: event.target.value })}
                />
              </Field>
            </div>
            <div className="mt-2 text-[10px] leading-relaxed text-slate-500">
              固定间隔可配合每日时段使用，例如只在 08:00–23:00 之间检查；时间窗口留空表示全天。
            </div>
          </FormSection>

          <FormSection title="自动化与通知" description="只有匹配成功的文件才会自动整理；低置信度、未识别与重复会按渠道上报。">
            <Field
              label="同名文件处理"
              hint="目标目录已有同名视频时：跟随影视库、自动加 (1)、跳过或覆盖。"
              className="mb-3"
            >
              <Select
                value={form.collision}
                onChange={(event) => patch({ collision: event.target.value as CollisionChoice })}
              >
                <option value="inherit">跟随影视库设置</option>
                <option value="suffix">自动加 (1)</option>
                <option value="skip">跳过，不移动</option>
                <option value="overwrite">覆盖同名文件</option>
              </Select>
            </Field>
            <div className="space-y-2.5">
              <Switch
                checked={form.auto_organize}
                onChange={(value) => patch({ auto_organize: value })}
                label="自动整理匹配成功的文件"
                hint="默认关闭。开启后会直接重命名、刮削并移动到目标目录。"
              />
              <Switch
                checked={form.replace_versions}
                onChange={(value) => patch({ replace_versions: value })}
                label="检测到更高版本时替换旧集"
                hint="需要自动整理已开启；只处理同一 TMDb + 季 + 集，普通重复只通知不替换。"
                disabled={!form.auto_organize}
              />
            </div>
            <div className="mt-3">
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
                  还没有通知渠道。可先保存订阅，再到「通知渠道」页添加 Bark、Telegram、Webhook 等。
                </div>
              )}
              <p className="mt-1.5 text-[10px] leading-relaxed text-slate-500">
                不选择表示使用所有已启用渠道；选择后只向所选渠道发送。
              </p>
            </div>
          </FormSection>
        </div>
      </Modal>

      <Drawer
        open={detailId !== null}
        onClose={() => {
          setDetailId(null);
          setRunLogs(null);
        }}
        title={detailSub ? `订阅记录 · ${detailSub.name}` : "订阅记录"}
        width="max-w-3xl"
      >
        {detailSub ? (
          <div className="space-y-4">
            <div className="rounded-2xl border border-white/[0.07] bg-white/[0.02] p-4">
              <div className="flex flex-wrap items-center gap-2">
                <Badge tone={detailSub.type === "rss" ? "violet" : "sky"}>
                  {detailSub.type === "rss" ? "RSS / 115" : "夸克分享"}
                </Badge>
                <Badge tone={detailSub.enabled ? "mint" : "slate"}>
                  {detailSub.enabled ? "已启用" : "已暂停"}
                </Badge>
                <span className="text-[11px] text-slate-500">{detailSub.library_name}</span>
              </div>
              <div className="mt-2 grid gap-x-5 gap-y-1 text-[11px] text-slate-400 sm:grid-cols-2">
                <span>计划：{scheduleLabel(detailSub.schedule)}</span>
                <span>时区：{detailSub.schedule.timezone}</span>
                <span>
                  下次：{detailSub.next_run_at ? formatDateTime(detailSub.next_run_at) : "—"}
                </span>
                <span>
                  上次：{formatTime(detailSub.last_run_at)} {detailSub.last_status ? `· ${statusLabel(detailSub.last_status)}` : ""}
                </span>
              </div>
              {detailSub.last_message ? (
                <div className="mt-2 text-[11px] leading-relaxed text-slate-500">
                  {detailSub.last_message}
                </div>
              ) : null}
              <div className="mt-3 flex gap-2">
                <Button
                  className="px-2.5 py-1.5 text-xs"
                  loading={actingId === detailSub.id}
                  disabled={Boolean(detailSub.active_run)}
                  onClick={() => runNow(detailSub)}
                >
                  <Play size={13} /> 立即运行
                </Button>
                <Button
                  className="px-2.5 py-1.5 text-xs"
                  onClick={() => openEdit(detailSub)}
                >
                  <Pencil size={13} /> 编辑
                </Button>
                <Button
                  className="px-2.5 py-1.5 text-xs"
                  onClick={() => reloadDetailRuns()}
                >
                  <RefreshCw size={13} /> 刷新记录
                </Button>
              </div>
            </div>

            <div className="flex gap-1 rounded-xl border border-white/[0.06] bg-white/[0.02] p-1">
              {[
                { key: "runs" as const, label: `运行记录 ${detailRuns?.length ?? 0}` },
                { key: "entries" as const, label: `条目 ${detailEntries?.length ?? 0}` },
              ].map((tab) => (
                <button
                  key={tab.key}
                  onClick={() => setDetailTab(tab.key)}
                  className={clsx(
                    "flex-1 rounded-lg px-3 py-1.5 text-xs transition",
                    detailTab === tab.key
                      ? "bg-white/[0.07] text-slate-100"
                      : "text-slate-500 hover:text-slate-300",
                  )}
                >
                  {tab.label}
                </button>
              ))}
            </div>

            {detailTab === "runs" ? (
              detailRuns?.length ? (
                <div className="space-y-2">
                  {detailRuns.map((run) => (
                    <div
                      key={run.id}
                      className="rounded-xl border border-white/[0.07] bg-white/[0.02] p-3"
                    >
                      <div className="flex flex-wrap items-center gap-2">
                        <StatusBadge status={run.status} />
                        <Badge tone="slate">{triggerLabel(run.trigger)}</Badge>
                        <span className="text-[11px] text-slate-500">
                          #{run.id} · {formatDateTime(run.created_at)}
                        </span>
                        {run.finished_at ? (
                          <span className="text-[10px] text-slate-600">
                            {formatTime(run.created_at)} → {formatTime(run.finished_at)}
                          </span>
                        ) : null}
                        <button
                          className="ml-auto text-[11px] text-slate-400 transition hover:text-mint-300"
                          onClick={() => loadRunLogs(run.id)}
                        >
                          {runLogs?.id === run.id ? "收起日志" : logsLoading ? "读取中…" : "查看日志"}
                        </button>
                      </div>
                      {run.status === "running" || run.status === "queued" ? (
                        <div className="mt-2">
                          <Progress value={run.progress} className="h-1" />
                          <div className="mt-1 text-[10px] text-slate-500">
                            {run.message || "执行中"} · {Math.round(run.progress)}%
                          </div>
                        </div>
                      ) : null}
                      {run.message ? (
                        <div className="mt-2 text-[11px] leading-relaxed text-slate-400">
                          {run.message}
                        </div>
                      ) : null}
                      {statsLine(run.stats) ? (
                        <div className="mt-1 text-[10px] text-slate-500">{statsLine(run.stats)}</div>
                      ) : null}
                      {run.error ? (
                        <div className="mt-1 rounded-lg border border-rose-500/25 bg-rose-500/[0.06] px-2 py-1 text-[10px] text-rose-200">
                          {run.error}
                        </div>
                      ) : null}
                      {runLogs?.id === run.id ? (
                        <div className="mt-2 max-h-64 space-y-0.5 overflow-y-auto rounded-lg border border-white/[0.06] bg-ink-950/50 p-2 font-mono text-[10px] leading-relaxed">
                          {(runLogs.logs || []).length ? (
                            runLogs.logs?.map((log) => (
                              <div
                                key={log.id}
                                className={clsx(
                                  log.level === "error"
                                    ? "text-rose-300"
                                    : log.level === "warn"
                                      ? "text-amber-300"
                                      : log.level === "debug"
                                        ? "text-slate-500"
                                        : "text-slate-400",
                                )}
                              >
                                <span className="text-slate-600">{formatTime(log.ts)}</span>{" "}
                                {log.message}
                              </div>
                            ))
                          ) : (
                            <span className="text-slate-600">没有日志</span>
                          )}
                        </div>
                      ) : null}
                    </div>
                  ))}
                </div>
              ) : (
                <div className="rounded-xl border border-dashed border-white/10 px-3 py-10 text-center text-xs text-slate-500">
                  还没有运行记录。点击「立即运行」或等待计划触发。
                </div>
              )
            ) : (
              <div className="space-y-2">
                <div className="flex justify-end">
                  <Select
                    className="w-36 py-1.5 text-xs"
                    value={entryFilter}
                    onChange={(event) => setEntryFilter(event.target.value)}
                  >
                    <option value="all">全部状态</option>
                    {Object.entries(ENTRY_STATUS_LABELS).map(([status, label]) => (
                      <option key={status} value={status}>
                        {label}
                      </option>
                    ))}
                  </Select>
                </div>
                {filteredEntries.length ? (
                  filteredEntries.map((entry) => (
                    <div
                      key={entry.id}
                      className="rounded-xl border border-white/[0.07] bg-white/[0.02] p-3"
                    >
                      <div className="flex flex-wrap items-center gap-2">
                        <span className="min-w-0 flex-1 truncate text-[11px] text-slate-200">
                          {entry.file_name}
                        </span>
                        <StatusBadge status={entry.status} />
                        {entry.season != null || entry.episode != null ? (
                          <Badge tone="slate">{seasonEpisode(entry.season, entry.episode)}</Badge>
                        ) : null}
                        {entry.version_label ? <Badge tone="violet">{entry.version_label}</Badge> : null}
                      </div>
                      <div className="mt-1 truncate text-[10px] text-slate-500">
                        {entry.target_path || entry.saved_path || entry.remote_path || "—"}
                      </div>
                      {entry.error ? (
                        <div className="mt-1 text-[10px] text-rose-300">{entry.error}</div>
                      ) : null}
                    </div>
                  ))
                ) : (
                  <div className="rounded-xl border border-dashed border-white/10 px-3 py-10 text-center text-xs text-slate-500">
                    没有符合筛选条件的条目。
                  </div>
                )}
              </div>
            )}
          </div>
        ) : (
          <div className="flex h-40 items-center justify-center text-sm text-slate-500">
            订阅已删除
          </div>
        )}
      </Drawer>

      <Modal
        open={importOpen}
        onClose={() => setImportOpen(false)}
        title="粘贴任务参数"
        width="max-w-2xl"
        footer={
          <>
            <Button onClick={() => setImportOpen(false)}>取消</Button>
            <Button variant="primary" onClick={applyImport} disabled={!importText.trim()}>
              <ClipboardPaste size={14} /> 识别并继续
            </Button>
          </>
        }
      >
        <div className="space-y-3">
          <p className="text-[11px] leading-relaxed text-slate-500">
            粘贴影序任务参数（订阅卡片上的「参数」按钮可提取，包含完整配置，可跨任务快速新建）。
            识别后会进入手动配置，补全影视库、转存目录和执行计划再保存。一次只能导入一个任务。
          </p>
          <textarea
            className="input min-h-[240px] resize-y py-2 font-mono text-[11px] leading-relaxed"
            value={importText}
            onChange={(event) => setImportText(event.target.value)}
            placeholder={'{\n  "app": "cinemint",\n  "name": "提欧奥特曼",\n  "type": "quark",\n  "config": { "share_url": "https://pan.quark.cn/s/..." }\n}'}
            spellCheck={false}
          />
          <p className="text-[10px] leading-relaxed text-slate-500">
            粘贴内容不会被上传；识别只在本页面完成，确认后仍需点击「保存」创建订阅。
          </p>
        </div>
      </Modal>

      <Modal
        open={paramsOpen}
        onClose={() => setParamsOpen(false)}
        title={paramsSub ? `任务参数 · ${paramsSub.name}` : "任务参数"}
        width="max-w-2xl"
        footer={
          <>
            <Button onClick={() => setParamsOpen(false)}>关闭</Button>
            <Button onClick={copyParams} disabled={paramsLoading || !paramsText}>
              <Copy size={14} /> 复制
            </Button>
            <Button
              variant="primary"
              onClick={createFromParams}
              disabled={paramsLoading || !paramsText}
            >
              <Plus size={14} /> 用此参数新建
            </Button>
          </>
        }
      >
        <div className="space-y-3">
          <p className="text-[11px] leading-relaxed text-slate-500">
            这是影序自己的任务参数：包含订阅名称、类型、完整配置（含提取码）、执行计划、截止日期、自动整理 / V2 替换与通知渠道。
            可以复制保存，或直接点「用此参数新建」快速创建一个相同配置的新订阅（影视库不存在时会回退到默认库，可在表单里改）。
            提取码会随参数一起导出，请按需保存。
          </p>
          <textarea
            className="input min-h-[300px] resize-y py-2 font-mono text-[11px] leading-relaxed"
            value={paramsText}
            readOnly={!paramsLoading}
            placeholder={paramsLoading ? "正在读取完整任务参数（含提取码）…" : ""}
            spellCheck={false}
          />
        </div>
      </Modal>

      <ConfirmDialog
        open={Boolean(deleteTarget)}
        title="删除订阅"
        danger
        confirmText="删除"
        message={`确定删除「${deleteTarget?.name || ""}」吗？已整理的文件不会删除，但订阅的历史条目与运行记录将无法恢复。`}
        onConfirm={remove}
        onClose={() => {
          if (!actingId) setDeleteTarget(null);
        }}
      />
    </div>
  );

}
