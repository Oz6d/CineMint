import { useCallback, useEffect, useRef, useState } from "react";
import {
  Bot,
  CloudDownload,
  Database,
  KeyRound,
  Lock,
  RefreshCw,
  ShieldCheck,
  Sparkles,
  Tags,
  Trash2,
  Wand2,
  Wifi,
} from "lucide-react";
import clsx from "clsx";
import type { AiPricingSyncResult, AiUsagePayload, SettingsPayload, TagDictionary } from "../types";
import { useAsync } from "../lib/hooks";
import { api } from "../lib/api";
import { useToast } from "../lib/toast";
import { formatDateTime } from "../lib/format";
import { Badge, Button, Card, ConfirmDialog, Field, Input, Select, Spinner, Switch } from "../components/ui";

interface SettingsForm {
  tmdb_api_key: string;
  tmdb_language: string;
  tmdb_qps: number;
  auto_threshold: number;
  review_threshold: number;
  min_size_mb: number;
  max_files_per_scan: number;
  allow_iso: boolean;
  auto_scan_enabled: boolean;
  auto_scan_interval_minutes: number;
  ai_enabled: boolean;
  ai_base_url: string;
  ai_api_key: string;
  ai_model: string;
  ai_input_price: number;
  ai_cached_input_price: number;
  ai_output_price: number;
  ai_peak_multiplier: number;
  ai_offpeak_multiplier: number;
  ai_peak_windows: string;
  ai_weekend_offpeak: boolean;
  ai_cn_holidays: string;
  ai_pricing_mode: string;
  quark_cookie: string;
  quark_qps: number;
  p115_cookie: string;
  p115_qps: number;
  p115_auth_mode: "cookie" | "open";
  p115_refresh_token: string;
  bangumi_enabled: boolean;
  bangumi_api_base: string;
  mikan_api_base: string;
  bangumi_main_url: string;
  bangumi_mirror_url: string;
  mikan_main_url: string;
  mikan_mirror_url: string;
}

interface SystemInfo {
  version: string;
  python: string;
  platform: string;
  data_dir: string;
  items: number;
}

type TagField = keyof TagDictionary;
type Preset = "main" | "mirror";
type AutoSaveStatus = "idle" | "saving" | "saved" | "error";

/** 停止输入后多久自动保存（毫秒）。 */
const AUTO_SAVE_DELAY = 800;

/** 用 GET /api/settings 的返回构建表单，密钥字段始终留空。 */
function formFromSettings(settings: SettingsPayload): SettingsForm {
  return {
    tmdb_api_key: "",
    tmdb_language: settings.tmdb_language,
    tmdb_qps: Number(settings.tmdb_qps),
    auto_threshold: Number(settings.auto_threshold),
    review_threshold: Number(settings.review_threshold),
    min_size_mb: Number(settings.min_size_mb),
    max_files_per_scan: Number(settings.max_files_per_scan),
    allow_iso: Boolean(settings.allow_iso),
    auto_scan_enabled: Boolean(settings.auto_scan_enabled),
    auto_scan_interval_minutes: Number(settings.auto_scan_interval_minutes ?? 15),
    ai_enabled: Boolean(settings.ai_enabled),
    ai_base_url: settings.ai_base_url,
    ai_api_key: "",
    ai_model: settings.ai_model,
    ai_input_price: Number(settings.ai_input_price ?? 0.15),
    ai_cached_input_price: Number(settings.ai_cached_input_price ?? settings.ai_input_price ?? 0.15),
    ai_output_price: Number(settings.ai_output_price ?? 0.6),
    ai_peak_multiplier: Number(settings.ai_peak_multiplier ?? 1),
    ai_offpeak_multiplier: Number(settings.ai_offpeak_multiplier ?? 1),
    ai_peak_windows: settings.ai_peak_windows || "01:00-04:00,06:00-10:00",
    ai_weekend_offpeak: Boolean(settings.ai_weekend_offpeak),
    ai_cn_holidays: settings.ai_cn_holidays || "",
    ai_pricing_mode: settings.ai_pricing_mode || "manual",
    quark_cookie: "",
    quark_qps: Number(settings.quark_qps ?? 2),
    p115_cookie: "",
    p115_qps: Number(settings.p115_qps ?? 1),
    p115_auth_mode: settings.p115_auth_mode === "open" ? "open" : "cookie",
    p115_refresh_token: "",
    bangumi_enabled: Boolean(settings.bangumi_enabled),
    bangumi_api_base: settings.bangumi_api_base || "https://api.bgm.tv",
    mikan_api_base: settings.mikan_api_base || "https://mikanani.me",
    bangumi_main_url: settings.bangumi_main_url || "https://api.bgm.tv",
    bangumi_mirror_url: settings.bangumi_mirror_url || "https://bgm.tv",
    mikan_main_url: settings.mikan_main_url || "https://mikanani.me",
    mikan_mirror_url: settings.mikan_mirror_url || "https://mikanime.tv",
  };
}

/** 根据已保存的 API 地址推断当前选中的是主站还是镜像。 */
function presetsFromSettings(settings: SettingsPayload): { bangumi: Preset; mikan: Preset } {
  const bangumiBase = settings.bangumi_api_base || "https://api.bgm.tv";
  const mikanBase = settings.mikan_api_base || "https://mikanani.me";
  return {
    bangumi: bangumiBase === (settings.bangumi_mirror_url || "https://bgm.tv") ? "mirror" : "main",
    mikan: mikanBase === (settings.mikan_mirror_url || "https://mikanime.tv") ? "mirror" : "main",
  };
}

/** 按 PUT /api/settings 的约定构建 payload（空密钥不上传、不发送 refresh_token）。 */
function buildSettingsPayload(
  form: SettingsForm,
  bangumiPreset: Preset,
  mikanPreset: Preset,
): Record<string, unknown> {
  const values: Record<string, unknown> = { ...form };
  if (!form.tmdb_api_key) delete values.tmdb_api_key;
  if (!form.ai_api_key) delete values.ai_api_key;
  if (!form.quark_cookie) delete values.quark_cookie;
  if (!form.p115_cookie) delete values.p115_cookie;
  delete values.p115_refresh_token;
  // 只把当前输入的地址写回所选主站 / 镜像对应的字段
  if (bangumiPreset === "main") {
    values.bangumi_main_url = form.bangumi_api_base;
  } else {
    values.bangumi_mirror_url = form.bangumi_api_base;
  }
  if (mikanPreset === "main") {
    values.mikan_main_url = form.mikan_api_base;
  } else {
    values.mikan_mirror_url = form.mikan_api_base;
  }
  return values;
}

function payloadSignature(payload: Record<string, unknown>): string {
  return JSON.stringify(payload);
}

function formatTokens(value: number): string {
  return Number(value || 0).toLocaleString("zh-CN");
}

function formatCost(value: number): string {
  const amount = Number(value || 0);
  if (amount >= 1) return `$${amount.toFixed(2)}`;
  if (amount >= 0.01) return `$${amount.toFixed(4)}`;
  return `$${amount.toFixed(6)}`;
}

function formatCheckTime(value: number): string {
  if (!value) return "";
  return new Date(value * 1000).toLocaleString("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

const COST_SOURCE_LABEL: Record<string, string> = {
  provider: "平台账单",
  manual: "手动价",
  "manual-fallback": "手动兜底",
  openrouter: "OpenRouter",
  litellm: "LiteLLM",
};

const TAG_FIELDS: Array<{
  key: TagField;
  label: string;
  hint: string;
  placeholder: string;
}> = [
  {
    key: "edition",
    label: "影视版本",
    hint: "导演剪辑版、加长版、IMAX、V2 等",
    placeholder: "如 导演剪辑版",
  },
  {
    key: "release_notes",
    label: "发布标签",
    hint: "PROPER、REPACK、DIY、国配等",
    placeholder: "如 PROPER",
  },
  {
    key: "group",
    label: "发布组",
    hint: "EVO、CHD、MyGroup 等片尾组名",
    placeholder: "如 EVO",
  },
];

const EMPTY_TAG_DICTIONARY: TagDictionary = {
  edition: [],
  release_notes: [],
  group: [],
};

function Section({
  icon,
  title,
  description,
  children,
  action,
}: {
  icon: React.ReactNode;
  title: string;
  description?: string;
  children: React.ReactNode;
  action?: React.ReactNode;
}) {
  return (
    <Card>
      <div className="mb-4 flex flex-wrap items-start justify-between gap-3">
        <div className="flex items-start gap-3">
          <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl border border-white/10 bg-white/[0.04] text-mint-400">
            {icon}
          </div>
          <div>
            <h2 className="text-sm font-semibold text-slate-100">{title}</h2>
            {description ? <p className="mt-1 text-[11px] leading-relaxed text-slate-500">{description}</p> : null}
          </div>
        </div>
        {action}
      </div>
      {children}
    </Card>
  );
}

export default function SettingsPage() {
  const { data: settings, loading, reload, setData: setSettings } = useAsync<SettingsPayload>("/api/settings");
  const { data: system } = useAsync<SystemInfo>("/api/settings/system");
  const { data: me } = useAsync<{ password_is_default: boolean; demo: boolean }>("/api/auth/me");
  const {
    data: tagDictionary,
    loading: tagsLoading,
    setData: setTagDictionary,
  } = useAsync<TagDictionary>("/api/settings/tags");
  const { data: aiUsage, reload: reloadAiUsage } = useAsync<AiUsagePayload>(
    "/api/settings/ai-usage?days=30",
  );
  const toast = useToast();

  const [form, setForm] = useState<SettingsForm | null>(null);
  const [autoSaveStatus, setAutoSaveStatus] = useState<AutoSaveStatus>("idle");
  const [testingTmdb, setTestingTmdb] = useState(false);
  const [testingAi, setTestingAi] = useState(false);
  const [testingQuark, setTestingQuark] = useState(false);
  const [testingP115, setTestingP115] = useState(false);
  const [p115TokenBusy, setP115TokenBusy] = useState(false);
  const [syncingPricing, setSyncingPricing] = useState(false);
  const [syncingHolidays, setSyncingHolidays] = useState(false);
  const [pricingNote, setPricingNote] = useState("");
  const holidaySyncRef = useRef(false);
  const [tmdbResult, setTmdbResult] = useState<{ ok: boolean; message: string } | null>(null);
  const [aiResult, setAiResult] = useState<{ ok: boolean; message: string } | null>(null);
  const [cloudResult, setCloudResult] = useState<{
    kind: "quark" | "p115";
    ok: boolean;
    message: string;
  } | null>(null);
  const [passwords, setPasswords] = useState({ current: "", next: "", confirm: "" });
  const [changingPassword, setChangingPassword] = useState(false);
  const [confirmDemoClear, setConfirmDemoClear] = useState(false);
  const [confirmClearUsage, setConfirmClearUsage] = useState(false);
  const [clearingUsage, setClearingUsage] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [tagDrafts, setTagDrafts] = useState<Record<TagField, string>>({
    edition: "",
    release_notes: "",
    group: "",
  });
  const [tagBusy, setTagBusy] = useState<TagField | null>(null);
  const [bangumiPreset, setBangumiPreset] = useState<Preset>("main");
  const [mikanPreset, setMikanPreset] = useState<Preset>("main");

  const formRef = useRef<SettingsForm | null>(null);
  const presetsRef = useRef<{ bangumi: Preset; mikan: Preset }>({ bangumi: "main", mikan: "main" });
  /** 上一次从服务端同步过的 settings 对象，避免表单变化时重复重建。 */
  const lastSyncedSettingsRef = useRef<SettingsPayload | null>(null);
  /** 最近一次成功保存（或从服务端同步）的 payload 签名，用来判断是否还有未保存改动。 */
  const lastSavedSignatureRef = useRef<string | null>(null);
  /** 服务端认可的表单内容快照（含密钥输入框），用来判断表单是否有任何未同步的手工编辑。 */
  const savedFormTextRef = useRef<string | null>(null);
  const saveInFlightRef = useRef(false);
  const queuedSaveRef = useRef(false);
  const saveTimerRef = useRef<number | null>(null);
  const saveErrorToastRef = useRef(false);
  const flushSaveRef = useRef<() => Promise<void>>(async () => {});

  const tags = tagDictionary || EMPTY_TAG_DICTIONARY;

  // settings → 表单同步：没有未保存改动时才用服务端数据重建表单，避免覆盖用户正在输入的内容。
  useEffect(() => {
    if (!settings) return;
    if (lastSyncedSettingsRef.current === settings) return;
    lastSyncedSettingsRef.current = settings;
    if (form && JSON.stringify(form) !== savedFormTextRef.current) return;
    const nextForm = formFromSettings(settings);
    const nextPresets = presetsFromSettings(settings);
    setForm(nextForm);
    setBangumiPreset(nextPresets.bangumi);
    setMikanPreset(nextPresets.mikan);
    lastSavedSignatureRef.current = payloadSignature(
      buildSettingsPayload(nextForm, nextPresets.bangumi, nextPresets.mikan),
    );
    savedFormTextRef.current = JSON.stringify(nextForm);
  }, [settings, form, bangumiPreset, mikanPreset]);

  useEffect(() => {
    formRef.current = form;
    presetsRef.current = { bangumi: bangumiPreset, mikan: mikanPreset };
  }, [form, bangumiPreset, mikanPreset]);

  const scheduleAutoSave = useCallback(() => {
    setAutoSaveStatus("saving");
    if (saveTimerRef.current !== null) {
      window.clearTimeout(saveTimerRef.current);
    }
    saveTimerRef.current = window.setTimeout(() => {
      saveTimerRef.current = null;
      void flushSaveRef.current();
    }, AUTO_SAVE_DELAY);
  }, []);

  const flushSave = useCallback(async () => {
    if (saveInFlightRef.current) {
      // 已有请求在途：记录下来，等它结束后再保存最新内容。
      queuedSaveRef.current = true;
      return;
    }
    const currentForm = formRef.current;
    if (!currentForm) return;
    const presets = presetsRef.current;
    const payload = buildSettingsPayload(currentForm, presets.bangumi, presets.mikan);
    const signature = payloadSignature(payload);
    if (signature === lastSavedSignatureRef.current) {
      setAutoSaveStatus("saved");
      return;
    }
    queuedSaveRef.current = false;
    saveInFlightRef.current = true;
    const sentFormText = JSON.stringify(currentForm);
    let succeeded = false;
    let appliedResponse = false;
    try {
      const saved = await api.put<SettingsPayload>("/api/settings", { values: payload });
      succeeded = true;
      lastSavedSignatureRef.current = signature;
      saveErrorToastRef.current = false;
      // 刷新缓存里的设置，让「已配置 Key」「已授权」等徽标立即更新。
      setSettings(saved);
      const latestForm = formRef.current;
      const latestSignature =
        latestForm !== null
          ? payloadSignature(
              buildSettingsPayload(latestForm, presetsRef.current.bangumi, presetsRef.current.mikan),
            )
          : null;
      // 整个表单（含不会提交的密钥 / refresh_token 输入）都没有变化时，才用服务端结果重建表单。
      if (
        latestForm !== null &&
        latestSignature === signature &&
        JSON.stringify(latestForm) === sentFormText
      ) {
        // 请求期间没有新编辑：用服务端结果重建表单并清空密钥输入框。
        const nextForm = formFromSettings(saved);
        const nextPresets = presetsFromSettings(saved);
        setForm(nextForm);
        setBangumiPreset(nextPresets.bangumi);
        setMikanPreset(nextPresets.mikan);
        lastSavedSignatureRef.current = payloadSignature(
          buildSettingsPayload(nextForm, nextPresets.bangumi, nextPresets.mikan),
        );
        savedFormTextRef.current = JSON.stringify(nextForm);
        appliedResponse = true;
      }
      setAutoSaveStatus("saved");
    } catch (err) {
      setAutoSaveStatus("error");
      if (!saveErrorToastRef.current) {
        saveErrorToastRef.current = true;
        const detail = err instanceof Error ? err.message : "";
        toast.error(detail ? `自动保存失败：${detail}` : "自动保存失败，修改后会重试");
      }
    } finally {
      saveInFlightRef.current = false;
      const latestForm = formRef.current;
      const latestSignature =
        latestForm !== null
          ? payloadSignature(
              buildSettingsPayload(latestForm, presetsRef.current.bangumi, presetsRef.current.mikan),
            )
          : null;
      const hasNewerEdits =
        !appliedResponse && latestSignature !== null && latestSignature !== lastSavedSignatureRef.current;
      // 成功但期间又有新编辑：继续保存；失败时只在期间确实有编辑的情况下重试一次，避免循环轰炸。
      if (succeeded ? hasNewerEdits : queuedSaveRef.current) {
        scheduleAutoSave();
      }
      queuedSaveRef.current = false;
    }
  }, [scheduleAutoSave, setSettings, toast]);

  useEffect(() => {
    flushSaveRef.current = flushSave;
  }, [flushSave]);

  // 表单 / 主站镜像选择变化后，防抖触发自动保存；签名相同则跳过，避免重复 PUT。
  useEffect(() => {
    if (!form) return;
    const signature = payloadSignature(buildSettingsPayload(form, bangumiPreset, mikanPreset));
    if (lastSavedSignatureRef.current === null) {
      lastSavedSignatureRef.current = signature;
      savedFormTextRef.current = JSON.stringify(form);
      return;
    }
    if (signature === lastSavedSignatureRef.current) {
      // 改回已保存的内容时，状态行也要跟着恢复。
      setAutoSaveStatus((current) => (current === "saving" || current === "error" ? "saved" : current));
      return;
    }
    scheduleAutoSave();
  }, [form, bangumiPreset, mikanPreset, scheduleAutoSave]);

  useEffect(() => {
    return () => {
      if (saveTimerRef.current !== null) {
        // 离开页面时把还在防抖里的改动立即提交，避免丢掉最后一次编辑。
        window.clearTimeout(saveTimerRef.current);
        saveTimerRef.current = null;
        void flushSaveRef.current();
      }
    };
  }, []);

  const patch = (values: Partial<SettingsForm>) => setForm((current) => (current ? { ...current, ...values } : current));

  const saveTagDictionary = async (field: TagField, values: string[], message: string) => {
    setTagBusy(field);
    try {
      const saved = await api.put<TagDictionary>("/api/settings/tags", { ...tags, [field]: values });
      setTagDictionary(saved);
      toast.success(message);
      return true;
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "词库保存失败");
      return false;
    } finally {
      setTagBusy(null);
    }
  };

  const addTag = async (field: TagField) => {
    const value = tagDrafts[field].trim();
    if (!value) return;
    if (tags[field].some((item) => item.toLowerCase() === value.toLowerCase())) {
      toast.push("这个词条已经在自定义词库里了");
      return;
    }
    const saved = await saveTagDictionary(field, [...tags[field], value], `已添加「${value}」`);
    if (saved) {
      setTagDrafts((current) => ({ ...current, [field]: "" }));
    }
  };

  const removeTag = async (field: TagField, value: string) => {
    await saveTagDictionary(
      field,
      tags[field].filter((item) => item !== value),
      `已移除「${value}」`,
    );
  };

  const testTmdb = async () => {
    if (!form) return;
    setTestingTmdb(true);
    setTmdbResult(null);
    try {
      const result = await api.post<{ sample?: string }>("/api/settings/test-tmdb", {
        api_key: form.tmdb_api_key || "",
      });
      setTmdbResult({ ok: true, message: `连接成功${result.sample ? `，搜索示例：《${result.sample}》` : ""}` });
    } catch (err) {
      setTmdbResult({ ok: false, message: err instanceof Error ? err.message : "连接失败" });
    } finally {
      setTestingTmdb(false);
    }
  };

  const testAi = async () => {
    if (!form) return;
    setTestingAi(true);
    setAiResult(null);
    try {
      await api.post("/api/settings/test-ai", {
        base_url: form.ai_base_url,
        model: form.ai_model,
        api_key: form.ai_api_key || "",
      });
      setAiResult({ ok: true, message: "AI 接口连接成功" });
    } catch (err) {
      setAiResult({ ok: false, message: err instanceof Error ? err.message : "连接失败" });
    } finally {
      setTestingAi(false);
    }
  };

  const testCloud = async (kind: "quark" | "p115") => {
    if (!form) return;
    const setLoading = kind === "quark" ? setTestingQuark : setTestingP115;
    setLoading(true);
    setCloudResult(null);
    try {
      const result = await api.post<{ nickname?: string; user_name?: string }>(
        `/api/settings/test-${kind === "quark" ? "quark" : "115"}`,
        { cookie: kind === "quark" ? form.quark_cookie : form.p115_cookie },
      );
      const nickname = result?.nickname || result?.user_name || "";
      setCloudResult({
        kind,
        ok: true,
        message: `连接成功${nickname ? `，账号：${nickname}` : ""}`,
      });
    } catch (err) {
      setCloudResult({
        kind,
        ok: false,
        message: err instanceof Error ? err.message : "连接失败",
      });
    } finally {
      setLoading(false);
    }
  };

  const saveP115Token = async () => {
    if (!form) return;
    const token = form.p115_refresh_token.trim();
    if (!token) {
      toast.push("请先粘贴 refresh_token");
      return;
    }
    setP115TokenBusy(true);
    setCloudResult(null);
    try {
      const result = await api.post<{ nickname?: string }>("/api/settings/115-open/token", {
        refresh_token: token,
      });
      patch({ p115_refresh_token: "", p115_auth_mode: "open" });
      setCloudResult({
        kind: "p115",
        ok: true,
        message: `授权成功${result?.nickname ? `，账号：${result.nickname}` : ""}`,
      });
      reload();
    } catch (err) {
      setCloudResult({
        kind: "p115",
        ok: false,
        message: err instanceof Error ? err.message : "授权失败",
      });
    } finally {
      setP115TokenBusy(false);
    }
  };

  const refreshP115Token = async () => {
    setP115TokenBusy(true);
    setCloudResult(null);
    try {
      await api.post("/api/settings/115-open/refresh", {});
      setCloudResult({ kind: "p115", ok: true, message: "授权已续期" });
      reload();
    } catch (err) {
      setCloudResult({
        kind: "p115",
        ok: false,
        message: err instanceof Error ? err.message : "续期失败",
      });
    } finally {
      setP115TokenBusy(false);
    }
  };

  const revokeP115Token = async () => {
    if (!window.confirm("确定解除 115 开放平台授权？解除后需重新粘贴 refresh_token。")) return;
    setP115TokenBusy(true);
    setCloudResult(null);
    try {
      await api.del("/api/settings/115-open/token");
      patch({ p115_auth_mode: "cookie" });
      setCloudResult({ kind: "p115", ok: true, message: "已解除授权" });
      reload();
    } catch (err) {
      setCloudResult({
        kind: "p115",
        ok: false,
        message: err instanceof Error ? err.message : "解除授权失败",
      });
    } finally {
      setP115TokenBusy(false);
    }
  };

  const applyDeepSeekPreset = () => {
    patch({
      ai_pricing_mode: "manual",
      ai_input_price: 0.15,
      ai_cached_input_price: 0.003,
      ai_output_price: 0.6,
      ai_peak_multiplier: 2,
      ai_offpeak_multiplier: 1,
      ai_peak_windows: "01:00-04:00,06:00-10:00",
      ai_weekend_offpeak: true,
    });
    setPricingNote("");
    toast.push("已填入 DeepSeek Flash 参考价，稍后自动保存");
  };

  const syncAiPricing = async () => {
    if (!form) return;
    if (!["openrouter", "litellm"].includes(form.ai_pricing_mode)) {
      toast.push("请先把计费方式切换为 OpenRouter 或 LiteLLM");
      return;
    }
    setSyncingPricing(true);
    setPricingNote("");
    try {
      const result = await api.post<AiPricingSyncResult>("/api/settings/ai-pricing/sync", {
        source: form.ai_pricing_mode,
        model: form.ai_model,
      });
      patch({
        ai_input_price: result.input,
        ai_cached_input_price: result.cached,
        ai_output_price: result.output,
        ai_peak_multiplier: result.peak_multiplier,
        ai_offpeak_multiplier: result.offpeak_multiplier,
        ai_peak_windows: result.peak_windows || form.ai_peak_windows,
        ai_weekend_offpeak: result.weekend_offpeak,
      });
      setPricingNote(`${result.note} · ${result.model}`);
      toast.success("已填入平台价格，稍后自动保存");
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "价格同步失败");
    } finally {
      setSyncingPricing(false);
    }
  };

  const syncHolidays = async (silent = false) => {
    setSyncingHolidays(true);
    try {
      const result = await api.post<{
        holidays: string;
        years: number[];
        updated_at: string;
        source: string;
        warnings: string[];
      }>("/api/settings/ai-holidays/sync", {});
      patch({ ai_cn_holidays: result.holidays });
      if (!silent) {
        toast.success(`已同步 ${result.years.join("、")} 年法定节假日`);
      }
      reload();
    } catch (err) {
      if (!silent) {
        toast.error(err instanceof Error ? err.message : "节假日同步失败");
      }
    } finally {
      setSyncingHolidays(false);
    }
  };

  useEffect(() => {
    if (!settings || !form || holidaySyncRef.current) return;
    if (!settings.ai_enabled) return;
    const updated = Date.parse(settings.ai_cn_holidays_updated_at || "");
    const stale = !Number.isFinite(updated) || Date.now() - updated > 7 * 24 * 60 * 60 * 1000;
    if (!stale) return;
    holidaySyncRef.current = true;
    void syncHolidays(true);
    // 只在设置页打开时自动检查一次，避免重复请求。
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [settings, form]);

  const clearAiUsage = async () => {
    setClearingUsage(true);
    try {
      await api.del("/api/settings/ai-usage");
      toast.success("AI 用量统计已清空");
      setConfirmClearUsage(false);
      reloadAiUsage();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "清空失败");
    } finally {
      setClearingUsage(false);
    }
  };

  const changePassword = async () => {
    if (!passwords.current || !passwords.next) {
      toast.error("请填写当前密码和新密码");
      return;
    }
    if (passwords.next.length < 6) {
      toast.error("新密码至少 6 位");
      return;
    }
    if (passwords.next !== passwords.confirm) {
      toast.error("两次输入的新密码不一致");
      return;
    }
    setChangingPassword(true);
    try {
      await api.post("/api/auth/password", { current: passwords.current, new: passwords.next });
      toast.success("密码已修改，请使用新密码登录");
      setPasswords({ current: "", next: "", confirm: "" });
      reload();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "修改失败");
    } finally {
      setChangingPassword(false);
    }
  };

  const clearCache = async () => {
    setBusy("cache");
    try {
      await api.del("/api/settings/cache");
      toast.success("TMDb 缓存已清空");
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "清理失败");
    } finally {
      setBusy(null);
    }
  };

  const seedDemo = async () => {
    setBusy("demo");
    try {
      await api.post("/api/demo/seed");
      toast.success("演示数据已载入");
      reload();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "生成失败");
    } finally {
      setBusy(null);
    }
  };

  const clearDemo = async () => {
    setBusy("demo");
    try {
      await api.del("/api/demo");
      toast.success("演示数据已清除");
      setConfirmDemoClear(false);
      reload();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "清除失败");
    } finally {
      setBusy(null);
    }
  };

  const autoSaveLabel =
    autoSaveStatus === "saving"
      ? "正在自动保存…"
      : autoSaveStatus === "saved"
        ? "已自动保存"
        : autoSaveStatus === "error"
          ? "自动保存失败，修改后会重试"
          : "修改后自动保存";

  if (loading && !settings) {
    return (
      <div className="flex h-64 items-center justify-center gap-2 text-sm text-slate-400">
        <Spinner /> 加载设置…
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-5xl space-y-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-lg font-semibold text-white">设置</h1>
          <p className="mt-1 text-xs text-slate-500">配置 TMDb、识别阈值、AI 兜底、云盘账号与安全选项。</p>
        </div>
        <div
          role="status"
          aria-live="polite"
          title="修改后约 1 秒自动保存，无需手动提交"
          className={clsx(
            "flex items-center gap-2 rounded-full border px-3 py-1.5 text-[11px]",
            autoSaveStatus === "error"
              ? "border-rose-500/30 bg-rose-500/[0.06] text-rose-200"
              : autoSaveStatus === "saved"
                ? "border-mint-500/25 bg-mint-500/[0.06] text-mint-200"
                : "border-white/10 bg-white/[0.03] text-slate-400",
          )}
        >
          <span
            className={clsx(
              "h-1.5 w-1.5 rounded-full",
              autoSaveStatus === "saving"
                ? "animate-pulse bg-sky-400"
                : autoSaveStatus === "saved"
                  ? "bg-mint-400"
                  : autoSaveStatus === "error"
                    ? "bg-rose-400"
                    : "bg-slate-500",
            )}
          />
          {autoSaveLabel}
        </div>
      </div>

      {me?.password_is_default ? (
        <div className="rounded-2xl border border-amber-500/25 bg-amber-500/[0.06] px-4 py-3 text-xs leading-relaxed text-amber-200">
          当前仍在使用默认密码。「设置 → 安全」里修改后，部署会更安全。
        </div>
      ) : null}

      {form ? (
        <>
          {/* 安全 */}
          <div className="glass card-hover flex flex-wrap items-center gap-x-6 gap-y-4 p-5">
            <div className="flex min-w-0 items-start gap-3">
              <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl border border-white/10 bg-white/[0.04] text-mint-400">
                <Lock size={17} />
              </div>
              <div>
                <h2 className="text-sm font-semibold text-slate-100">安全</h2>
                <p className="mt-1 text-[11px] leading-relaxed text-slate-500">
                  修改登录密码，避免他人访问你的影视库和任务。
                </p>
              </div>
            </div>
            <div className="grid min-w-[300px] flex-1 gap-3 sm:grid-cols-[repeat(3,minmax(0,1fr))_auto] sm:items-end">
              <Field label="当前密码">
                <Input
                  type="password"
                  value={passwords.current}
                  onChange={(event) => setPasswords({ ...passwords, current: event.target.value })}
                  autoComplete="current-password"
                />
              </Field>
              <Field label="新密码">
                <Input
                  type="password"
                  value={passwords.next}
                  onChange={(event) => setPasswords({ ...passwords, next: event.target.value })}
                  autoComplete="new-password"
                />
              </Field>
              <Field label="确认新密码">
                <Input
                  type="password"
                  value={passwords.confirm}
                  onChange={(event) => setPasswords({ ...passwords, confirm: event.target.value })}
                  autoComplete="new-password"
                />
              </Field>
              <Button className="h-[38px] shrink-0 px-3" onClick={changePassword} loading={changingPassword}>
                <KeyRound size={15} /> 修改密码
              </Button>
            </div>
          </div>

          {/* TMDb */}
          <Section
            icon={<Database size={17} />}
            title="TMDb 元数据"
            description="识别作品、获取标题 / 简介 / 海报 / 季集信息。需要免费的 TMDb API Key。"
            action={
              <div className="flex items-center gap-2">
                {settings?.tmdb_api_key_set ? <Badge tone="mint">已配置 Key</Badge> : <Badge tone="amber">未配置</Badge>}
                <Button onClick={testTmdb} loading={testingTmdb} className="px-2.5 py-1.5 text-xs">
                  <Wifi size={13} /> 测试
                </Button>
              </div>
            }
          >
            <div className="space-y-4">
              <Field
                label="API Key"
                hint={
                  settings?.tmdb_api_key_set
                    ? "已保存 Key，留空表示保持不变。支持 v3 API Key 或 v4 Read Access Token。"
                    : "在 themoviedb.org 免费注册后创建 API Key。支持 v3 Key 或 v4 Token。"
                }
              >
                <Input
                  type="password"
                  value={form.tmdb_api_key}
                  onChange={(event) => patch({ tmdb_api_key: event.target.value })}
                  placeholder={settings?.tmdb_api_key_set ? "••••••••（留空保持不变）" : "粘贴 TMDb API Key"}
                  autoComplete="new-password"
                />
              </Field>
              <div className="grid gap-4 sm:grid-cols-2">
                <Field label="元数据语言">
                  <Select
                    className="h-[38px]"
                    value={form.tmdb_language}
                    onChange={(event) => patch({ tmdb_language: event.target.value })}
                  >
                    <option value="zh-CN">简体中文</option>
                    <option value="zh-TW">繁体中文</option>
                    <option value="zh-HK">粤语 / 香港</option>
                    <option value="en-US">English</option>
                    <option value="ja-JP">日本語</option>
                  </Select>
                </Field>
                <Field label="请求频率上限（次/秒）" hint="避免触发 TMDb 限流，一般 4 即可。">
                  <Input
                    className="h-[38px]"
                    type="number"
                    step="0.5"
                    min="0.5"
                    max="20"
                    value={form.tmdb_qps}
                    onChange={(event) => patch({ tmdb_qps: Number(event.target.value) })}
                  />
                </Field>
              </div>
              {tmdbResult ? (
                <div
                  className={clsx(
                    "rounded-xl border px-3.5 py-2.5 text-xs",
                    tmdbResult.ok
                      ? "border-mint-500/30 bg-mint-500/[0.08] text-mint-200"
                      : "border-rose-500/30 bg-rose-500/[0.08] text-rose-200",
                  )}
                >
                  {tmdbResult.message}
                </div>
              ) : null}
            </div>
          </Section>

          {/* 识别 */}
          <Section
            icon={<Wand2 size={17} />}
            title="识别与扫描"
            description="匹配分数高于自动阈值时直接采用；介于两个阈值之间进入人工确认。"
          >
            <div className="grid gap-4 sm:grid-cols-2">
              <Field label="自动匹配阈值" hint="0.72 表示 72% 以上自动通过。">
                <Input
                  type="number"
                  step="0.01"
                  min="0.05"
                  max="0.99"
                  value={form.auto_threshold}
                  onChange={(event) => patch({ auto_threshold: Number(event.target.value) })}
                />
              </Field>
              <Field label="人工确认阈值" hint="低于此分数视为未识别。">
                <Input
                  type="number"
                  step="0.01"
                  min="0.05"
                  max="0.99"
                  value={form.review_threshold}
                  onChange={(event) => patch({ review_threshold: Number(event.target.value) })}
                />
              </Field>
              <Field label="最小文件体积（MB）" hint="小于该值的内容会被忽略，避免花絮 / 样本干扰。">
                <Input
                  type="number"
                  min="0"
                  value={form.min_size_mb}
                  onChange={(event) => patch({ min_size_mb: Number(event.target.value) })}
                />
              </Field>
              <Field label="单次扫描文件上限">
                <Input
                  type="number"
                  min="1"
                  max="20000"
                  value={form.max_files_per_scan}
                  onChange={(event) => patch({ max_files_per_scan: Number(event.target.value) })}
                />
              </Field>
            </div>
            <div className="mt-4">
              <Switch
                checked={form.allow_iso}
                onChange={(value) => patch({ allow_iso: value })}
                label="识别 ISO 原盘文件"
                hint="ISO 体积较大且无法直接读取元数据，一般家庭媒体库可以关闭"
              />
            </div>
            <div className="mt-4 grid gap-4 sm:grid-cols-2">
              <Switch
                checked={form.auto_scan_enabled}
                onChange={(value) => patch({ auto_scan_enabled: value })}
                label="自动扫描待整理目录"
                hint={`每 ${form.auto_scan_interval_minutes} 分钟检查一次网盘 / 本地目录，有变化才真正扫描；演示库不参与`}
              />
              <Field
                label="自动检查间隔（分钟）"
                hint="5–1440 分钟；开启后的第一次检查会立即扫描一次，之后只在目录有变化时扫描。"
              >
                <Input
                  type="number"
                  min="5"
                  max="1440"
                  disabled={!form.auto_scan_enabled}
                  value={form.auto_scan_interval_minutes}
                  onChange={(event) =>
                    patch({ auto_scan_interval_minutes: Number(event.target.value) })
                  }
                />
              </Field>
            </div>
          </Section>

          {/* 附加信息词库 */}
          <Section
            icon={<Tags size={17} />}
            title="附加信息词库"
            description="从文件名中识别影视版本、发布标签和发布组。内置词库始终生效，这里可以追加自己的词条。"
          >
            <div className="mb-3 rounded-xl border border-sky-500/20 bg-sky-500/[0.05] px-3.5 py-2.5 text-[11px] leading-relaxed text-slate-400">
              按「附加信息表」的规则：<span className="text-slate-200">影视版本、发布标签、发布组</span>
              可以扩展；分辨率、视频质量、来源、视频编码、音频信息属于行业固定标准，不在这里修改。
              添加或删除会立即保存，下一次扫描 / 重新识别时生效。
            </div>
            <div className="grid gap-3 lg:grid-cols-3">
              {TAG_FIELDS.map((field) => {
                const values = tags[field.key] || [];
                const busy = tagBusy !== null || (tagsLoading && !tagDictionary);
                return (
                  <div
                    key={field.key}
                    className="flex flex-col rounded-xl border border-white/[0.07] bg-white/[0.02] p-3"
                  >
                    <div className="flex items-center justify-between gap-2">
                      <span className="text-xs font-medium text-slate-200">{field.label}</span>
                      <Badge tone={values.length ? "mint" : "slate"}>{values.length} 条自定义</Badge>
                    </div>
                    <p className="mt-1 text-[10px] leading-relaxed text-slate-500">{field.hint}</p>
                    <div className="mt-2 flex min-h-[34px] flex-wrap content-start gap-1.5">
                      {tagsLoading && !tagDictionary ? (
                        <span className="text-[10px] text-slate-500">加载词库…</span>
                      ) : values.length ? (
                        values.map((value) => (
                          <span
                            key={value}
                            className="inline-flex items-center gap-1 rounded-md border border-mint-400/25 bg-mint-400/[0.08] px-1.5 py-0.5 text-[11px] text-mint-200"
                          >
                            {value}
                            <button
                              type="button"
                              title={`移除 ${value}`}
                              disabled={busy}
                              onClick={() => removeTag(field.key, value)}
                              className="text-mint-300/60 transition hover:text-rose-300 disabled:opacity-40"
                            >
                              ×
                            </button>
                          </span>
                        ))
                      ) : (
                        <span className="text-[10px] text-slate-600">暂无自定义词条，内置词库仍然生效</span>
                      )}
                    </div>
                    <div className="mt-auto flex gap-1.5 pt-3">
                      <Input
                        value={tagDrafts[field.key]}
                        placeholder={field.placeholder}
                        className="text-[11px]"
                        maxLength={64}
                        disabled={busy}
                        onChange={(event) =>
                          setTagDrafts((current) => ({ ...current, [field.key]: event.target.value }))
                        }
                        onKeyDown={(event) => {
                          if (event.key === "Enter") {
                            event.preventDefault();
                            addTag(field.key);
                          }
                        }}
                      />
                      <Button
                        className="shrink-0 px-2.5 py-1.5 text-xs"
                        loading={tagBusy === field.key}
                        disabled={busy}
                        onClick={() => addTag(field.key)}
                      >
                        添加
                      </Button>
                    </div>
                  </div>
                );
              })}
            </div>
          </Section>

          {/* AI */}
          <Section
            icon={<Bot size={17} />}
            title="AI 解析兜底（可选）"
            description="当规则解析失败时，用 OpenAI 兼容接口从文件名中提取标题、年份、季集。"
            action={
              <div className="flex items-center gap-2">
                {settings?.ai_api_key_set ? <Badge tone="mint">已配置 Key</Badge> : null}
                <Button
                  onClick={testAi}
                  loading={testingAi}
                  className="px-2.5 py-1.5 text-xs"
                >
                  <Wifi size={13} /> 测试
                </Button>
              </div>
            }
          >
            <div className="space-y-4">
              <Switch
                checked={form.ai_enabled}
                onChange={(value) => patch({ ai_enabled: value })}
                label="启用 AI 文件名解析"
                hint="只发送文件名，不发送文件内容；结果仍会与 TMDb 交叉验证"
              />
              <div className="grid gap-4 sm:grid-cols-2">
                <Field label="接口地址">
                  <Input
                    value={form.ai_base_url}
                    onChange={(event) => patch({ ai_base_url: event.target.value })}
                    placeholder="https://api.deepseek.com/v1"
                  />
                </Field>
                <Field label="模型">
                  <Input
                    value={form.ai_model}
                    onChange={(event) => patch({ ai_model: event.target.value })}
                    placeholder="deepseek-flash"
                  />
                </Field>
              </div>
              <Field label="API Key" hint="留空保持不变。会加密存储在本地数据库中。">
                <Input
                  type="password"
                  value={form.ai_api_key}
                  onChange={(event) => patch({ ai_api_key: event.target.value })}
                  placeholder={settings?.ai_api_key_set ? "••••••••（留空保持不变）" : "sk-…"}
                  autoComplete="new-password"
                />
              </Field>
              <Field
                label="计费方式"
                hint={
                  form.ai_pricing_mode === "provider"
                    ? "优先使用服务商响应里的 cost；没有返回时回退到下面的三档价估算。"
                    : form.ai_pricing_mode === "openrouter"
                      ? "用 OpenRouter /api/v1/models 同步价格；峰谷规则不适用。"
                      : form.ai_pricing_mode === "litellm"
                        ? "用 LiteLLM 公开价格库同步；峰谷规则不适用。"
                        : "使用下面的三档单价和峰谷规则估算。"
                }
              >
                <Select
                  value={form.ai_pricing_mode}
                  onChange={(event) => {
                    patch({ ai_pricing_mode: event.target.value });
                    setPricingNote("");
                  }}
                >
                  <option value="manual">手动 / 自定义单价</option>
                  <option value="provider">平台返回费用优先</option>
                  <option value="openrouter">OpenRouter 自动同步</option>
                  <option value="litellm">LiteLLM 价格库同步</option>
                </Select>
              </Field>
              <div className="flex flex-wrap items-center justify-between gap-2">
                <div className="text-xs font-medium text-slate-200">计费单价（USD / 1M tokens）</div>
                <div className="flex flex-wrap gap-2">
                  {["openrouter", "litellm"].includes(form.ai_pricing_mode) ? (
                    <Button
                      className="px-2.5 py-1.5 text-xs"
                      onClick={syncAiPricing}
                      loading={syncingPricing}
                    >
                      <RefreshCw size={12} /> 同步平台价格
                    </Button>
                  ) : null}
                  <Button
                    className="px-2.5 py-1.5 text-xs"
                    onClick={applyDeepSeekPreset}
                  >
                    <Sparkles size={12} /> 填入 DeepSeek 参考价
                  </Button>
                </div>
              </div>
              {pricingNote ? (
                <div className="rounded-lg border border-sky-500/20 bg-sky-500/[0.06] px-3 py-2 text-[10px] text-sky-200">
                  {pricingNote}
                </div>
              ) : null}
              <div className="grid gap-4 sm:grid-cols-3">
                <Field
                  label="输入价（缓存未命中）"
                  hint="普通输入 token 单价；只用于估算，不影响调用。"
                >
                  <Input
                    type="number"
                    min="0"
                    step="0.001"
                    value={form.ai_input_price}
                    onChange={(event) => patch({ ai_input_price: Number(event.target.value) })}
                  />
                </Field>
                <Field
                  label="输入价（缓存命中）"
                  hint="DeepSeek Flash 参考价 0.003；未单独配置时按普通输入价计算。"
                >
                  <Input
                    type="number"
                    min="0"
                    step="0.001"
                    value={form.ai_cached_input_price}
                    onChange={(event) =>
                      patch({ ai_cached_input_price: Number(event.target.value) })
                    }
                  />
                </Field>
                <Field
                  label="输出价"
                  hint="completion / reasoning token 的单价。"
                >
                  <Input
                    type="number"
                    min="0"
                    step="0.01"
                    value={form.ai_output_price}
                    onChange={(event) => patch({ ai_output_price: Number(event.target.value) })}
                  />
                </Field>
              </div>
              <div className="grid gap-4 sm:grid-cols-3">
                <Field label="峰时倍率" hint="DeepSeek 默认高峰 ×2，谷时 ×1。">
                  <Input
                    type="number"
                    min="0"
                    step="0.1"
                    value={form.ai_peak_multiplier}
                    onChange={(event) =>
                      patch({ ai_peak_multiplier: Number(event.target.value) })
                    }
                  />
                </Field>
                <Field label="谷时倍率" hint="没有峰谷计费的模型保持 1。">
                  <Input
                    type="number"
                    min="0"
                    step="0.1"
                    value={form.ai_offpeak_multiplier}
                    onChange={(event) =>
                      patch({ ai_offpeak_multiplier: Number(event.target.value) })
                    }
                  />
                </Field>
                <Field
                  label="峰时窗口（UTC）"
                  hint="逗号分隔，例如 DeepSeek：01:00-04:00,06:00-10:00"
                >
                  <Input
                    value={form.ai_peak_windows}
                    onChange={(event) => patch({ ai_peak_windows: event.target.value })}
                    placeholder="01:00-04:00,06:00-10:00"
                  />
                </Field>
              </div>
              <Switch
                checked={form.ai_weekend_offpeak}
                onChange={(value) => patch({ ai_weekend_offpeak: value })}
                label="周末全天按谷时计算"
                hint="DeepSeek 周末按谷时；中国法定节假日按下方日期列表判断。"
              />
              <div className="space-y-2">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <label className="label">中国法定节假日（YYYY-MM-DD）</label>
                  <Button
                    className="px-2.5 py-1.5 text-xs"
                    onClick={() => syncHolidays(false)}
                    loading={syncingHolidays}
                  >
                    <RefreshCw size={12} /> 同步节假日
                  </Button>
                </div>
                <textarea
                  className="input min-h-[72px] resize-y py-2 text-xs leading-relaxed"
                  value={form.ai_cn_holidays}
                  onChange={(event) => patch({ ai_cn_holidays: event.target.value })}
                  placeholder="2026-10-01,2026-10-02,..."
                />
                <p className="text-[11px] leading-relaxed text-slate-500">
                  从 holiday-cn 公开数据自动同步；打开设置页时超过 7 天会自动检查一次。也可手动修改。
                  {settings?.ai_cn_holidays_updated_at
                    ? ` 上次同步：${formatDateTime(settings.ai_cn_holidays_updated_at)}`
                    : " 尚未同步。"}
                </p>
              </div>
              {aiResult ? (
                <div
                  className={clsx(
                    "rounded-xl border px-3.5 py-2.5 text-xs",
                    aiResult.ok
                      ? "border-mint-500/30 bg-mint-500/[0.08] text-mint-200"
                      : "border-rose-500/30 bg-rose-500/[0.08] text-rose-200",
                  )}
                >
                  {aiResult.message}
                </div>
              ) : null}

              <div className="rounded-xl border border-white/[0.06] bg-white/[0.02] p-3.5">
                <div className="flex flex-wrap items-start justify-between gap-2">
                  <div>
                    <div className="flex items-center gap-1.5 text-xs font-medium text-slate-200">
                      <Sparkles size={13} className="text-mint-300" /> AI 用量与费用
                      <span className="text-slate-500">近 30 天</span>
                    </div>
                    <div className="mt-1 text-[10px] text-slate-500">
                      按上面的三档单价和峰谷倍率估算；历史记录从当前版本开始统计。
                    </div>
                  </div>
                  <div className="flex gap-2">
                    <Button
                      className="px-2.5 py-1.5 text-xs"
                      onClick={reloadAiUsage}
                    >
                      <RefreshCw size={12} /> 刷新
                    </Button>
                    <Button
                      className="px-2.5 py-1.5 text-xs"
                      onClick={() => setConfirmClearUsage(true)}
                      disabled={!aiUsage?.all_time.calls}
                    >
                      <Trash2 size={12} /> 清空
                    </Button>
                  </div>
                </div>

                <div className="mt-3 grid grid-cols-2 gap-2 sm:grid-cols-5">
                  {[
                    ["调用次数", formatTokens(aiUsage?.period.calls ?? 0)],
                    ["输入 tokens", formatTokens(aiUsage?.period.prompt_tokens ?? 0)],
                    ["缓存命中", formatTokens(aiUsage?.period.cached_tokens ?? 0)],
                    ["输出 tokens", formatTokens(aiUsage?.period.completion_tokens ?? 0)],
                    ["估算费用", formatCost(aiUsage?.period.cost ?? 0)],
                  ].map(([label, value]) => (
                    <div
                      key={label}
                      className="rounded-lg border border-white/[0.05] bg-white/[0.02] px-2.5 py-2"
                    >
                      <div className="text-[10px] text-slate-500">{label}</div>
                      <div className="mt-0.5 font-mono text-sm text-slate-200">{value}</div>
                    </div>
                  ))}
                </div>

                {aiUsage?.by_model.length ? (
                  <div className="mt-3 space-y-1">
                    <div className="text-[10px] text-slate-500">按模型</div>
                    {aiUsage.by_model.slice(0, 5).map((item) => (
                      <div
                        key={item.model || "unknown"}
                        className="flex flex-wrap items-center justify-between gap-2 rounded-md bg-white/[0.02] px-2 py-1 text-[10px]"
                      >
                        <span className="min-w-0 truncate font-mono text-slate-300">
                          {item.model || "未知模型"}
                        </span>
                        <span className="text-slate-500">
                          {formatTokens(item.calls)} 次 · {formatTokens(item.total_tokens)} tokens · 缓存{" "}
                          {formatTokens(item.cached_tokens)} ·{" "}
                          <span className="text-mint-300">{formatCost(item.cost)}</span>
                        </span>
                      </div>
                    ))}
                  </div>
                ) : null}

                {aiUsage?.recent.length ? (
                  <div className="mt-3">
                    <div className="mb-1 text-[10px] text-slate-500">最近调用</div>
                    <div className="max-h-56 space-y-1 overflow-y-auto pr-0.5">
                      {aiUsage.recent.slice(0, 10).map((record) => (
                        <div
                          key={record.id}
                          className="flex flex-wrap items-center gap-x-2 gap-y-0.5 rounded-md bg-white/[0.02] px-2 py-1 text-[10px]"
                        >
                          <span className="min-w-0 flex-1 truncate text-slate-300">
                            {record.file_name || record.context || "AI 调用"}
                          </span>
                          <span className="font-mono text-slate-500">{record.model || "—"}</span>
                          <span className="text-slate-500">
                            {formatTokens(record.total_tokens)} tokens
                          </span>
                          {record.cached_tokens ? (
                            <span className="text-slate-500">
                              缓存 {formatTokens(record.cached_tokens)}
                            </span>
                          ) : null}
                          {record.peak ? (
                            <span className="rounded bg-rose-400/10 px-1 py-px text-rose-300">
                              峰时 ×{record.multiplier}
                            </span>
                          ) : null}
                          <span className="text-mint-300">{formatCost(record.cost)}</span>
                          <span
                            className={clsx(
                              "rounded px-1 py-px",
                              record.cost_source === "provider"
                                ? "bg-sky-400/10 text-sky-300"
                                : record.cost_source === "openrouter" || record.cost_source === "litellm"
                                  ? "bg-violet-400/10 text-violet-300"
                                  : "bg-white/[0.05] text-slate-400",
                            )}
                          >
                            {COST_SOURCE_LABEL[record.cost_source] || record.cost_source || "手动价"}
                          </span>
                          {record.estimated ? (
                            <span className="rounded bg-amber-400/10 px-1 py-px text-amber-300">
                              估算
                            </span>
                          ) : null}
                          <span className="text-slate-600">{formatDateTime(record.created_at)}</span>
                        </div>
                      ))}
                    </div>
                  </div>
                ) : (
                  <div className="mt-3 rounded-lg border border-dashed border-white/10 px-3 py-4 text-center text-[10px] text-slate-500">
                    还没有 AI 调用记录。启用 AI 兜底并重新扫描后，这里会显示 token 与费用。
                  </div>
                )}

                <div className="mt-2 text-[10px] leading-relaxed text-slate-600">
                  费用优先使用平台返回的 cost；否则按「缓存命中 / 未命中 / 输出」三档单价和峰谷倍率估算。OpenRouter / LiteLLM 使用同步价格，不叠加峰谷。服务商未返回 usage 时会按字符数粗略计算并标记「估算」。
                </div>
              </div>
            </div>
          </Section>

          {/* 云盘与追更 */}
          <Section
            icon={<CloudDownload size={17} />}
            title="云盘与追更服务"
            description="夸克订阅与 RSS → 115 转存需要对应网盘登录 Cookie；Cookie 会加密保存在本地数据库。"
          >
            <div className="grid gap-4 lg:grid-cols-2">
              <div className="space-y-3 rounded-xl border border-white/[0.07] bg-white/[0.02] p-3.5">
                <div className="flex items-center justify-between gap-2">
                  <div>
                    <div className="text-xs font-medium text-slate-200">夸克网盘</div>
                    <div className="mt-0.5 text-[10px] text-slate-500">
                      用于读取分享、转存到待整理目录
                    </div>
                  </div>
                  {settings?.quark_cookie_set ? (
                    <Badge tone="mint">已配置</Badge>
                  ) : (
                    <Badge tone="amber">未配置</Badge>
                  )}
                </div>
                <Field
                  label="Cookie"
                  hint="浏览器登录 pan.quark.cn 后，从开发者工具复制完整 Cookie。留空保持不变。"
                >
                  <textarea
                    className="input min-h-[84px] resize-y py-2 font-mono text-[10px] leading-relaxed"
                    value={form.quark_cookie}
                    onChange={(event) => patch({ quark_cookie: event.target.value })}
                    placeholder={
                      settings?.quark_cookie_set
                        ? "已配置，留空保持不变"
                        : "粘贴夸克网盘 Cookie"
                    }
                    autoComplete="off"
                  />
                </Field>
                <div className="flex items-end gap-2">
                  <Field label="限速 QPS" className="w-28">
                    <Input
                      type="number"
                      min="0.2"
                      max="20"
                      step="0.1"
                      value={form.quark_qps}
                      onChange={(event) => patch({ quark_qps: Number(event.target.value) })}
                    />
                  </Field>
                  <Button
                    className="mb-0.5 px-2.5 py-1.5 text-xs"
                    loading={testingQuark}
                    disabled={!settings?.quark_cookie_set && !form.quark_cookie}
                    onClick={() => testCloud("quark")}
                  >
                    <Wifi size={13} /> 测试连接
                  </Button>
                </div>
              </div>

              <div className="space-y-3 rounded-xl border border-white/[0.07] bg-white/[0.02] p-3.5">
                <div className="flex items-center justify-between gap-2">
                  <div className="min-w-0">
                    <div className="text-xs font-medium text-slate-200">115 网盘</div>
                    <div className="mt-0.5 text-[10px] text-slate-500">
                      用于 RSS 磁力 / 种子 / 电驴云添加
                    </div>
                  </div>
                  <div className="flex shrink-0 items-center gap-1.5">
                    <button
                      type="button"
                      onClick={() => patch({ p115_auth_mode: "cookie" })}
                      className={clsx(
                        "rounded-md border px-2 py-1 text-[11px] transition-colors",
                        form.p115_auth_mode === "cookie"
                          ? "border-cyan-500/50 bg-cyan-500/20 text-cyan-200"
                          : "border-slate-700 bg-slate-800/50 text-slate-400 hover:border-slate-600 hover:text-slate-300",
                      )}
                    >
                      Cookie
                    </button>
                    <button
                      type="button"
                      onClick={() => patch({ p115_auth_mode: "open" })}
                      className={clsx(
                        "rounded-md border px-2 py-1 text-[11px] transition-colors",
                        form.p115_auth_mode === "open"
                          ? "border-cyan-500/50 bg-cyan-500/20 text-cyan-200"
                          : "border-slate-700 bg-slate-800/50 text-slate-400 hover:border-slate-600 hover:text-slate-300",
                      )}
                    >
                      开放平台
                    </button>
                    {form.p115_auth_mode === "open" ? (
                      settings?.p115_open_authorized && !settings?.p115_open_needs_reauth ? (
                        <Badge tone="mint">已授权</Badge>
                      ) : (
                        <Badge tone="amber">
                          {settings?.p115_open_needs_reauth ? "需重新授权" : "未授权"}
                        </Badge>
                      )
                    ) : settings?.p115_cookie_set ? (
                      <Badge tone="mint">已配置</Badge>
                    ) : (
                      <Badge tone="amber">未配置</Badge>
                    )}
                  </div>
                </div>

                {form.p115_auth_mode === "cookie" ? (
                  <Field
                    label="Cookie"
                    hint="浏览器登录 115.com 后复制完整 Cookie。需要账号具备云添加权限。留空保持不变。"
                  >
                    <textarea
                      className="input min-h-[84px] resize-y py-2 font-mono text-[10px] leading-relaxed"
                      value={form.p115_cookie}
                      onChange={(event) => patch({ p115_cookie: event.target.value })}
                      placeholder={
                        settings?.p115_cookie_set
                          ? "已配置，留空保持不变"
                          : "粘贴 115 网盘 Cookie"
                      }
                      autoComplete="off"
                    />
                  </Field>
                ) : (
                  <div className="space-y-2.5">
                    <div
                      className={clsx(
                        "rounded-lg border px-2.5 py-2 text-[11px] leading-relaxed",
                        settings?.p115_open_authorized && !settings?.p115_open_needs_reauth
                          ? "border-mint-500/25 bg-mint-500/[0.06] text-mint-200"
                          : "border-amber-500/25 bg-amber-500/[0.06] text-amber-200",
                      )}
                    >
                      {settings?.p115_open_authorized ? (
                        <>
                          {settings.p115_open_nickname ? `账号：${settings.p115_open_nickname}` : "已授权"}
                          {settings.p115_open_needs_reauth
                            ? " · 授权已失效，请重新粘贴 refresh_token"
                            : settings.p115_open_expires_at
                              ? ` · access_token 有效至 ${formatCheckTime(settings.p115_open_expires_at)}`
                              : ""}
                        </>
                      ) : (
                        "尚未授权。粘贴开放平台 refresh_token 后即可自动续期。"
                      )}
                    </div>
                    <Field
                      label="refresh_token"
                      hint="115 开放平台下发的一次性长期令牌，保存后会立即校验并加密存储。留空不会改动已有授权。"
                    >
                      <textarea
                        className="input min-h-[72px] resize-y py-2 font-mono text-[10px] leading-relaxed"
                        value={form.p115_refresh_token}
                        onChange={(event) => patch({ p115_refresh_token: event.target.value })}
                        placeholder={
                          settings?.p115_open_authorized
                            ? "已授权，留空保持不变"
                            : "粘贴 refresh_token"
                        }
                        autoComplete="off"
                      />
                    </Field>
                    <div className="flex flex-wrap gap-1.5">
                      <Button
                        className="px-2.5 py-1.5 text-xs"
                        loading={p115TokenBusy}
                        onClick={saveP115Token}
                      >
                        保存并验证
                      </Button>
                      {settings?.p115_open_authorized ? (
                        <>
                          <Button
                            className="px-2.5 py-1.5 text-xs"
                            loading={p115TokenBusy}
                            onClick={refreshP115Token}
                          >
                            立即续期
                          </Button>
                          <Button
                            className="px-2.5 py-1.5 text-xs"
                            disabled={p115TokenBusy}
                            onClick={revokeP115Token}
                          >
                            解除授权
                          </Button>
                        </>
                      ) : null}
                    </div>
                  </div>
                )}

                <div className="flex items-end gap-2">
                  <Field label="限速 QPS" className="w-28">
                    <Input
                      type="number"
                      min="0.2"
                      max="10"
                      step="0.1"
                      value={form.p115_qps}
                      onChange={(event) => patch({ p115_qps: Number(event.target.value) })}
                    />
                  </Field>
                  <Button
                    className="mb-0.5 px-2.5 py-1.5 text-xs"
                    loading={testingP115}
                    disabled={
                      form.p115_auth_mode === "open"
                        ? !settings?.p115_open_authorized
                        : !settings?.p115_cookie_set && !form.p115_cookie
                    }
                    onClick={() => testCloud("p115")}
                  >
                    <Wifi size={13} /> 测试连接
                  </Button>
                </div>
              </div>
            </div>

            {cloudResult ? (
              <div
                className={clsx(
                  "mt-3 rounded-xl border px-3.5 py-2.5 text-xs",
                  cloudResult.ok
                    ? "border-mint-500/30 bg-mint-500/[0.08] text-mint-200"
                    : "border-rose-500/30 bg-rose-500/[0.08] text-rose-200",
                )}
              >
                {cloudResult.kind === "quark" ? "夸克" : "115"} · {cloudResult.message}
              </div>
            ) : null}

            <div className="mt-4 space-y-4">
              <Switch
                checked={form.bangumi_enabled}
                onChange={(value) => patch({ bangumi_enabled: value })}
                label="启用 Bangumi"
                hint="放送日历默认使用蜜柑计划数据。开启后会额外从 Bangumi 获取放送表作为补充。"
              />

              <Field
                label="Bangumi API 地址"
                hint="追更与放送日历使用。默认 https://api.bgm.tv；网络受限时可改成可访问的镜像地址。"
              >
                <div className="space-y-2">
                  <div className="flex flex-wrap gap-1.5">
                    <button
                      type="button"
                      onClick={() => {
                        setBangumiPreset("main");
                        patch({ bangumi_api_base: form?.bangumi_main_url || "https://api.bgm.tv" });
                      }}
                      className={clsx(
                        "rounded-md border px-2 py-1 text-[11px] transition-colors",
                        bangumiPreset === "main"
                          ? "border-cyan-500/50 bg-cyan-500/20 text-cyan-200"
                          : "border-slate-700 bg-slate-800/50 text-slate-400 hover:border-slate-600 hover:text-slate-300",
                      )}
                    >
                      Bangumi
                    </button>
                    <button
                      type="button"
                      onClick={() => {
                        setBangumiPreset("mirror");
                        patch({ bangumi_api_base: form?.bangumi_mirror_url || "https://bgm.tv" });
                      }}
                      className={clsx(
                        "rounded-md border px-2 py-1 text-[11px] transition-colors",
                        bangumiPreset === "mirror"
                          ? "border-cyan-500/50 bg-cyan-500/20 text-cyan-200"
                          : "border-slate-700 bg-slate-800/50 text-slate-400 hover:border-slate-600 hover:text-slate-300",
                      )}
                    >
                      Bangumi 镜像
                    </button>
                  </div>
                  <Input
                    value={form.bangumi_api_base}
                    onChange={(event) => patch({ bangumi_api_base: event.target.value })}
                    placeholder="https://api.bgm.tv"
                  />
                </div>
              </Field>

              <Field
                label="蜜柑计划 API 地址"
                hint="RSS 追更与放送日历使用。默认 https://mikanani.me（主站）；网络受限时可改成镜像或其他地址。"
              >
                <div className="space-y-2">
                  <div className="flex flex-wrap gap-1.5">
                    <button
                      type="button"
                      onClick={() => {
                        setMikanPreset("main");
                        patch({ mikan_api_base: form?.mikan_main_url || "https://mikanani.me" });
                      }}
                      className={clsx(
                        "rounded-md border px-2 py-1 text-[11px] transition-colors",
                        mikanPreset === "main"
                          ? "border-cyan-500/50 bg-cyan-500/20 text-cyan-200"
                          : "border-slate-700 bg-slate-800/50 text-slate-400 hover:border-slate-600 hover:text-slate-300",
                      )}
                    >
                      蜜柑计划
                    </button>
                    <button
                      type="button"
                      onClick={() => {
                        setMikanPreset("mirror");
                        patch({ mikan_api_base: form?.mikan_mirror_url || "https://mikanime.tv" });
                      }}
                      className={clsx(
                        "rounded-md border px-2 py-1 text-[11px] transition-colors",
                        mikanPreset === "mirror"
                          ? "border-cyan-500/50 bg-cyan-500/20 text-cyan-200"
                          : "border-slate-700 bg-slate-800/50 text-slate-400 hover:border-slate-600 hover:text-slate-300",
                      )}
                    >
                      蜜柑计划镜像
                    </button>
                  </div>
                  <Input
                    value={form.mikan_api_base}
                    onChange={(event) => patch({ mikan_api_base: event.target.value })}
                    placeholder="https://mikanani.me"
                  />
                </div>
              </Field>
            </div>
            <p className="mt-3 text-[10px] leading-relaxed text-slate-500">
              Cookie 仅用于本地请求夸克 / 115 接口，不会上传到其他服务。订阅调度与整理仍在服务端执行，
              请只整理你拥有合法访问权的文件。
            </p>
          </Section>

          {/* 数据 */}
          <Section
            icon={<ShieldCheck size={17} />}
            title="数据与演示"
            description="演示数据会在本地生成占位视频文件与示例识别结果，不会访问网络。"
          >
            <div className="flex flex-wrap gap-2">
              <Button onClick={clearCache} loading={busy === "cache"}>
                <RefreshCw size={15} /> 清空 TMDb 缓存
              </Button>
              <Button onClick={seedDemo} loading={busy === "demo"}>
                <Sparkles size={15} /> {me?.demo ? "重新生成演示数据" : "载入演示数据"}
              </Button>
              {me?.demo ? (
                <Button variant="danger" onClick={() => setConfirmDemoClear(true)}>
                  <Trash2 size={15} /> 清除演示数据
                </Button>
              ) : null}
            </div>
          </Section>

          {/* 关于 */}
          <Section icon={<Database size={17} />} title="关于影序 CineMint">
            <div className="space-y-2 text-[11px] leading-relaxed text-slate-400">
              <div className="flex flex-wrap gap-x-5 gap-y-1">
                <span>版本 {system?.version || settings?.version || "—"}</span>
                <span>Python {system?.python || "—"}</span>
                <span>已管理 {system?.items ?? "—"} 个文件</span>
              </div>
              <div className="font-mono text-slate-500">数据目录：{system?.data_dir || "—"}</div>
              <p>
                元数据与海报来自 TMDb，本产品使用 TMDb API 但未经 TMDb 认可或认证。本工具只做文件重命名与元数据整理，
                不提供、不下载任何影视内容。
              </p>
              <p className="text-slate-500">
                请只整理你拥有合法访问权的媒体文件，并遵守所在地区的版权法规。
              </p>
            </div>
          </Section>
        </>
      ) : null}

      <ConfirmDialog
        open={confirmDemoClear}
        title="清除演示数据"
        danger
        confirmText="清除"
        message="将删除演示影视库、占位文件与相关任务记录。真实影视库不受影响。"
        onConfirm={clearDemo}
        onClose={() => setConfirmDemoClear(false)}
      />

      <ConfirmDialog
        open={confirmClearUsage}
        title="清空 AI 用量统计"
        danger
        confirmText={clearingUsage ? "清空中…" : "清空"}
        message="将删除所有 AI 调用记录和费用统计，不会影响 AI 配置或影视库。此操作无法撤销。"
        onConfirm={clearAiUsage}
        onClose={() => {
          if (!clearingUsage) setConfirmClearUsage(false);
        }}
      />
    </div>
  );
}
