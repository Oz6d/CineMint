import { useMemo, useState } from "react";
import {
  BellRing,
  CheckCircle2,
  Pencil,
  Plus,
  Power,
  RefreshCw,
  Send,
  Trash2,
  XCircle,
} from "lucide-react";
import clsx from "clsx";
import type {
  NotificationCatalog,
  NotificationChannel,
  NotificationChannelMeta,
  NotificationField,
  NotificationLog,
} from "../types";
import { useAsync } from "../lib/hooks";
import { api } from "../lib/api";
import { useToast } from "../lib/toast";
import { formatDateTime } from "../lib/format";
import {
  Badge,
  Button,
  Card,
  ConfirmDialog,
  EmptyState,
  Field,
  Input,
  Modal,
  Select,
  Spinner,
  Switch,
} from "../components/ui";

interface ChannelForm {
  name: string;
  type: string;
  config: Record<string, any>;
  events: string[];
  enabled: boolean;
}

function defaultsFor(meta?: NotificationChannelMeta): Record<string, any> {
  const config: Record<string, any> = {};
  for (const field of meta?.fields || []) {
    config[field.key] = field.default ?? (field.type === "bool" ? false : "");
  }
  return config;
}

function eventLabel(catalog: NotificationCatalog | null, key: string): string {
  return catalog?.events.find((event) => event.key === key)?.label || key;
}

function typeLabel(catalog: NotificationCatalog | null, type: string): string {
  return catalog?.channels.find((channel) => channel.type === type)?.label || type;
}

export default function NotificationsPage() {
  const { data: catalog, loading: catalogLoading } = useAsync<NotificationCatalog>(
    "/api/notifications/catalog",
  );
  const {
    data: channelPayload,
    loading: channelsLoading,
    reload: reloadChannels,
  } = useAsync<{ channels: NotificationChannel[] }>("/api/notifications");
  const [logChannel, setLogChannel] = useState("0");
  const logQuery = `/api/notifications/logs?limit=100${
    logChannel !== "0" ? `&channel_id=${logChannel}` : ""
  }`;
  const { data: logPayload, reload: reloadLogs } = useAsync<{ logs: NotificationLog[] }>(
    logQuery,
    [logChannel],
    { pollMs: 10000 },
  );
  const toast = useToast();

  const [formOpen, setFormOpen] = useState(false);
  const [editing, setEditing] = useState<NotificationChannel | null>(null);
  const [form, setForm] = useState<ChannelForm>({
    name: "",
    type: "webhook",
    config: {},
    events: [],
    enabled: true,
  });
  const [saving, setSaving] = useState(false);
  const [actingId, setActingId] = useState<number | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<NotificationChannel | null>(null);

  const channels = channelPayload?.channels || [];
  const metas = catalog?.channels || [];
  const meta = useMemo(
    () => metas.find((channel) => channel.type === form.type),
    [metas, form.type],
  );

  const openCreate = () => {
    const first = metas[0];
    const type = first?.type || "webhook";
    setEditing(null);
    setForm({
      name: first?.label || "",
      type,
      config: defaultsFor(first),
      events: [],
      enabled: true,
    });
    setFormOpen(true);
  };

  const openEdit = (channel: NotificationChannel) => {
    const channelMeta = metas.find((item) => item.type === channel.type);
    setEditing(channel);
    setForm({
      name: channel.name,
      type: channel.type,
      config: { ...defaultsFor(channelMeta), ...channel.config },
      events: [...(channel.events || [])],
      enabled: channel.enabled,
    });
    setFormOpen(true);
  };

  const patch = (values: Partial<ChannelForm>) =>
    setForm((current) => ({ ...current, ...values }));

  const changeType = (type: string) => {
    if (editing) return;
    const next = metas.find((item) => item.type === type);
    setForm({
      name: next?.label || "",
      type,
      config: defaultsFor(next),
      events: form.events,
      enabled: form.enabled,
    });
  };

  const setConfig = (key: string, value: any) =>
    setForm((current) => ({
      ...current,
      config: { ...current.config, [key]: value },
    }));

  const toggleEvent = (key: string) => {
    setForm((current) => ({
      ...current,
      events: current.events.includes(key)
        ? current.events.filter((item) => item !== key)
        : [...current.events, key],
    }));
  };

  const save = async () => {
    if (!form.type) {
      toast.error("请选择通知渠道类型");
      return;
    }
    setSaving(true);
    try {
      const payload = {
        name: form.name.trim() || meta?.label || form.type,
        type: form.type,
        config: form.config,
        events: form.events,
        enabled: form.enabled,
      };
      if (editing) {
        await api.put(`/api/notifications/${editing.id}`, payload);
        toast.success("通知渠道已更新");
      } else {
        await api.post("/api/notifications", payload);
        toast.success("通知渠道已添加");
      }
      setFormOpen(false);
      reloadChannels();
      reloadLogs();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "保存失败");
    } finally {
      setSaving(false);
    }
  };

  const testChannel = async (channel: NotificationChannel) => {
    setActingId(channel.id);
    try {
      await api.post(`/api/notifications/${channel.id}/test`);
      toast.success(`「${channel.name}」测试消息已发送`);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "测试失败");
    } finally {
      setActingId(null);
      reloadChannels();
      reloadLogs();
    }
  };

  const toggleEnabled = async (channel: NotificationChannel) => {
    setActingId(channel.id);
    try {
      await api.put(`/api/notifications/${channel.id}`, { enabled: !channel.enabled });
      toast.success(channel.enabled ? "渠道已停用" : "渠道已启用");
      reloadChannels();
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
      await api.del(`/api/notifications/${deleteTarget.id}`);
      toast.success("通知渠道已删除");
      setDeleteTarget(null);
      reloadChannels();
      reloadLogs();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "删除失败");
    } finally {
      setActingId(null);
    }
  };

  const logs = logPayload?.logs || [];

  const renderField = (field: NotificationField) => {
    const value = form.config[field.key];
    const isSecretSet = form.config[field.key] === "" && editing?.secret_fields_set.includes(field.key);
    if (field.type === "bool") {
      return (
        <div key={field.key} className="sm:col-span-2">
          <Switch
            checked={Boolean(value)}
            onChange={(checked) => setConfig(field.key, checked)}
            label={field.label}
            hint={field.help || undefined}
          />
        </div>
      );
    }
    const label = `${field.label}${field.required ? " *" : ""}`;
    if (field.type === "select") {
      return (
        <Field key={field.key} label={label} hint={field.help || undefined}>
          <Select
            value={String(value ?? "")}
            onChange={(event) => setConfig(field.key, event.target.value)}
          >
            {(field.options || []).map((option) => (
              <option key={option} value={option}>
                {option || "（默认）"}
              </option>
            ))}
          </Select>
        </Field>
      );
    }
    if (field.type === "textarea") {
      return (
        <Field
          key={field.key}
          label={label}
          hint={field.help || undefined}
          className="sm:col-span-2"
        >
          <textarea
            className="input min-h-[84px] resize-y py-2 text-xs leading-relaxed"
            value={String(value ?? "")}
            placeholder={isSecretSet ? "已设置，留空保持不变" : field.placeholder}
            onChange={(event) => setConfig(field.key, event.target.value)}
          />
        </Field>
      );
    }
    return (
      <Field
        key={field.key}
        label={label}
        hint={isSecretSet ? "已保存，留空保持不变。" : field.help || undefined}
      >
        <Input
          type={field.secret ? "password" : field.type === "int" ? "number" : "text"}
          value={String(value ?? "")}
          placeholder={isSecretSet ? "已设置，留空保持不变" : field.placeholder}
          autoComplete={field.secret ? "new-password" : undefined}
          onChange={(event) =>
            setConfig(
              field.key,
              field.type === "int" ? Number(event.target.value) : event.target.value,
            )
          }
        />
      </Field>
    );
  };

  return (
    <div className="mx-auto max-w-5xl space-y-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-lg font-semibold text-white">通知渠道</h1>
          <p className="mt-1 text-xs text-slate-500">
            订阅每次运行会合并成一条推送：标题只写任务名；正文先显示转存成功 / 失败 / 整理 / 未识别 / 待确认计数，再列分类文件明细，最后是时间。同类文件不会重复发多条；勾选事件用于筛选你关心的运行，未勾选时接收全部。
          </p>
        </div>
        <div className="flex gap-2">
          <Button onClick={() => { reloadChannels(); reloadLogs(); }}>
            <RefreshCw size={15} /> 刷新
          </Button>
          <Button variant="primary" onClick={openCreate} disabled={!metas.length}>
            <Plus size={15} /> 添加渠道
          </Button>
        </div>
      </div>

      <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
        {[
          ["渠道总数", channels.length],
          ["已启用", channels.filter((channel) => channel.enabled).length],
          ["支持类型", metas.length],
          ["最近投递", logs.length],
        ].map(([label, value]) => (
          <div
            key={label}
            className="rounded-xl border border-white/[0.06] bg-white/[0.02] px-3 py-2.5"
          >
            <div className="text-[10px] text-slate-500">{label}</div>
            <div className="mt-0.5 font-mono text-lg text-slate-100">{value}</div>
          </div>
        ))}
      </div>

      {catalogLoading && !catalog ? (
        <div className="flex h-48 items-center justify-center gap-2 text-sm text-slate-400">
          <Spinner /> 加载通知渠道目录…
        </div>
      ) : channelsLoading && !channelPayload ? (
        <div className="flex h-40 items-center justify-center gap-2 text-sm text-slate-400">
          <Spinner /> 加载通知渠道…
        </div>
      ) : channels.length ? (
        <div className="space-y-3">
          {channels.map((channel) => (
            <Card key={channel.id} className="space-y-2.5 p-4">
              <div className="flex flex-wrap items-start justify-between gap-2">
                <div className="flex min-w-0 items-start gap-3">
                  <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl border border-white/10 bg-white/[0.04] text-mint-300">
                    <BellRing size={16} />
                  </div>
                  <div className="min-w-0">
                    <div className="flex flex-wrap items-center gap-1.5">
                      <span className="truncate text-sm font-semibold text-white">
                        {channel.name}
                      </span>
                      <Badge tone="slate">
                        {typeLabel(catalog, channel.type)}
                      </Badge>
                      <Badge tone={channel.enabled ? "mint" : "slate"}>
                        {channel.enabled ? "已启用" : "已停用"}
                      </Badge>
                      {channel.last_test_ok === true ? <Badge tone="mint">测试通过</Badge> : null}
                      {channel.last_test_ok === false ? <Badge tone="rose">测试失败</Badge> : null}
                    </div>
                    <div className="mt-1 flex flex-wrap gap-1">
                      {channel.events.length ? (
                        channel.events.map((key) => (
                          <Badge key={key} tone="violet">
                            {eventLabel(catalog, key)}
                          </Badge>
                        ))
                      ) : (
                        <Badge tone="sky">全部事件</Badge>
                      )}
                    </div>
                    {channel.last_error ? (
                      <div className="mt-1.5 rounded-lg border border-rose-500/25 bg-rose-500/[0.06] px-2 py-1 text-[10px] leading-relaxed text-rose-200">
                        {channel.last_error}
                      </div>
                    ) : null}
                    {channel.last_test_at ? (
                      <div className="mt-1 text-[10px] text-slate-600">
                        上次测试：{formatDateTime(channel.last_test_at)}
                      </div>
                    ) : null}
                  </div>
                </div>
                <div className="flex flex-wrap items-center gap-1.5">
                  <Button
                    className="px-2.5 py-1.5 text-xs"
                    loading={actingId === channel.id}
                    onClick={() => testChannel(channel)}
                  >
                    <Send size={12} /> 测试
                  </Button>
                  <Button
                    className="px-2 py-1.5 text-xs"
                    onClick={() => toggleEnabled(channel)}
                    title={channel.enabled ? "停用渠道" : "启用渠道"}
                  >
                    <Power size={12} /> {channel.enabled ? "停用" : "启用"}
                  </Button>
                  <Button
                    className="px-2 py-1.5 text-xs"
                    onClick={() => openEdit(channel)}
                  >
                    <Pencil size={12} /> 编辑
                  </Button>
                  <Button
                    className="px-2 py-1.5 text-xs"
                    onClick={() => setDeleteTarget(channel)}
                  >
                    <Trash2 size={12} />
                  </Button>
                </div>
              </div>
            </Card>
          ))}
        </div>
      ) : (
        <EmptyState
          icon={<BellRing size={28} />}
          title="还没有通知渠道"
          description="添加 Bark、Telegram、企业微信、飞书、钉钉、自定义 Webhook 等渠道后，订阅事件会实时推送。"
          action={
            <Button variant="primary" onClick={openCreate} disabled={!metas.length}>
              <Plus size={14} /> 添加第一个渠道
            </Button>
          }
        />
      )}

      <Card className="p-4 sm:p-5">
        <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
          <div>
            <h2 className="text-sm font-semibold text-slate-100">投递记录</h2>
            <p className="mt-0.5 text-[10px] text-slate-500">保留最近 1000 条记录，这里显示最新 100 条。</p>
          </div>
          <div className="flex items-center gap-2">
            <Select
              className="w-44 py-1.5 text-xs"
              value={logChannel}
              onChange={(event) => setLogChannel(event.target.value)}
            >
              <option value="0">全部渠道</option>
              {channels.map((channel) => (
                <option key={channel.id} value={channel.id}>
                  {channel.name}
                </option>
              ))}
            </Select>
            <Button className="px-2.5 py-1.5 text-xs" onClick={reloadLogs}>
              <RefreshCw size={12} /> 刷新
            </Button>
          </div>
        </div>
        {logs.length ? (
          <div className="max-h-[420px] space-y-1 overflow-y-auto">
            {logs.map((log) => (
              <div
                key={log.id}
                className="flex flex-wrap items-center gap-x-2 gap-y-0.5 rounded-lg border border-white/[0.04] bg-white/[0.02] px-2.5 py-1.5 text-[11px]"
              >
                {log.ok ? (
                  <CheckCircle2 size={12} className="shrink-0 text-mint-400" />
                ) : (
                  <XCircle size={12} className="shrink-0 text-rose-400" />
                )}
                <span className="font-medium text-slate-300">
                  {eventLabel(catalog, log.event)}
                </span>
                <span className="min-w-0 flex-1 truncate text-slate-400">{log.title}</span>
                <span className="text-slate-500">
                  {log.channel_name || (log.channel_type ? typeLabel(catalog, log.channel_type) : "测试")}
                </span>
                {!log.ok && log.error ? (
                  <span className="w-full truncate text-[10px] text-rose-300">{log.error}</span>
                ) : null}
                <span className="text-[10px] text-slate-600">{formatDateTime(log.created_at)}</span>
              </div>
            ))}
          </div>
        ) : (
          <div className="rounded-xl border border-dashed border-white/10 px-3 py-8 text-center text-[11px] text-slate-500">
            还没有投递记录。添加渠道后点「测试」，或等待订阅运行。
          </div>
        )}
      </Card>

      <Modal
        open={formOpen}
        onClose={() => {
          if (!saving) setFormOpen(false);
        }}
        title={editing ? `编辑渠道 · ${editing.name}` : "添加通知渠道"}
        width="max-w-2xl"
        footer={
          <>
            <span className="mr-auto text-[10px] text-slate-500">
              保存后可在列表里发送测试消息。
            </span>
            <Button onClick={() => setFormOpen(false)} disabled={saving}>
              取消
            </Button>
            <Button variant="primary" onClick={save} loading={saving}>
              保存
            </Button>
          </>
        }
      >
        <div className="space-y-4">
          <section className="rounded-2xl border border-white/[0.07] bg-white/[0.02] p-4">
            <div className="grid gap-3 sm:grid-cols-2">
              <Field label="渠道类型" hint={editing ? "渠道类型创建后不可修改。" : meta?.description}>
                <Select
                  value={form.type}
                  disabled={Boolean(editing)}
                  onChange={(event) => changeType(event.target.value)}
                >
                  {metas.map((channel) => (
                    <option key={channel.type} value={channel.type}>
                      {channel.label}
                    </option>
                  ))}
                </Select>
              </Field>
              <Field label="渠道名称">
                <Input
                  value={form.name}
                  maxLength={80}
                  onChange={(event) => patch({ name: event.target.value })}
                  placeholder={meta?.label || "例如：家庭群通知"}
                />
              </Field>
            </div>
            <div className="mt-3">
              <Switch
                checked={form.enabled}
                onChange={(value) => patch({ enabled: value })}
                label="启用这个渠道"
                hint="停用后不会参与任何事件推送，但仍保留配置"
              />
            </div>
          </section>

          <section className="rounded-2xl border border-white/[0.07] bg-white/[0.02] p-4">
            <h4 className="text-xs font-semibold text-slate-100">渠道配置</h4>
            <p className="mt-1 text-[11px] leading-relaxed text-slate-500">
              密钥字段会加密保存在本地数据库；编辑时留空表示保持原值。
            </p>
            <div className="mt-3 grid gap-3 sm:grid-cols-2">
              {(meta?.fields || []).map(renderField)}
            </div>
          </section>

          <section className="rounded-2xl border border-white/[0.07] bg-white/[0.02] p-4">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <div>
                <h4 className="text-xs font-semibold text-slate-100">接收哪些事件</h4>
                <p className="mt-1 text-[11px] text-slate-500">
                  不选择表示接收全部事件；选择后只推送所选类型。
                </p>
              </div>
              <Button
                className="px-2 py-1 text-[10px]"
                onClick={() => patch({ events: [] })}
                disabled={!form.events.length}
              >
                全选
              </Button>
            </div>
            <div className="mt-2.5 flex flex-wrap gap-1.5">
              {(catalog?.events || []).map((event) => {
                const selected = form.events.includes(event.key);
                return (
                  <button
                    key={event.key}
                    type="button"
                    onClick={() => toggleEvent(event.key)}
                    className={clsx(
                      "rounded-lg border px-2.5 py-1.5 text-[11px] transition",
                      selected
                        ? "border-mint-500/40 bg-mint-500/10 text-mint-200"
                        : "border-white/10 bg-white/[0.02] text-slate-400 hover:text-slate-200",
                    )}
                  >
                    {event.label}
                  </button>
                );
              })}
            </div>
          </section>
        </div>
      </Modal>

      <ConfirmDialog
        open={Boolean(deleteTarget)}
        title="删除通知渠道"
        danger
        confirmText="删除"
        message={`确定删除「${deleteTarget?.name || ""}」吗？历史投递记录会保留，但该渠道将不再接收通知。`}
        onConfirm={remove}
        onClose={() => {
          if (!actingId) setDeleteTarget(null);
        }}
      />
    </div>
  );
}
