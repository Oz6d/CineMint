import { useEffect, useState } from "react";
import {
  ArrowLeft,
  Cloud,
  Folder,
  FolderOpen,
  HardDrive,
  Pencil,
  Plus,
  Server,
  ShieldCheck,
  Trash2,
  Wifi,
} from "lucide-react";
import clsx from "clsx";
import type { Source } from "../types";
import { useAsync } from "../lib/hooks";
import { api } from "../lib/api";
import { useToast } from "../lib/toast";
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

interface SourceForm {
  name: string;
  type: "local" | "webdav" | "smb";
  config: Record<string, any>;
}

const EMPTY_FORM: SourceForm = {
  name: "",
  type: "local",
  config: { base_path: "/media" },
};

const TYPE_META = {
  local: { label: "本地目录", icon: HardDrive, hint: "Docker 部署时请把宿主机媒体目录挂载进容器，再填写容器内路径。" },
  webdav: {
    label: "WebDAV",
    icon: Cloud,
    hint: "支持 OpenList / Alist、Nextcloud、群晖等标准 WebDAV。OpenList / Alist 的地址必须带 /dav 后缀（如 http://IP:5244/dav），否则会返回 HTTP 405。",
  },
  smb: { label: "SMB / Samba", icon: Server, hint: "直接访问局域网共享，无需在系统里手动挂载。" },
} as const;

function typeDefaults(type: SourceForm["type"]): Record<string, any> {
  if (type === "webdav") return { url: "", username: "", password: "", verify_tls: true };
  if (type === "smb") return { host: "", share: "", port: 445, domain: "", username: "", password: "" };
  return { base_path: "/media" };
}

function typeSummary(source: Source): string {
  if (source.type === "local") return String(source.config.base_path || "");
  if (source.type === "webdav") return String(source.config.url || "");
  return `\\\\${source.config.host || ""}\\${source.config.share || ""}`;
}

interface BrowseEntry {
  name: string;
  path: string;
  is_dir: boolean;
  size: number;
}

interface BrowseResult {
  path: string;
  parent: string;
  entries: BrowseEntry[];
}

export default function SourcesPage() {
  const { data: sources, loading, reload } = useAsync<Source[]>("/api/sources");
  const [formOpen, setFormOpen] = useState(false);
  const [editing, setEditing] = useState<Source | null>(null);
  const [form, setForm] = useState<SourceForm>(EMPTY_FORM);
  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState<{ ok: boolean; message: string } | null>(null);
  const [browseSource, setBrowseSource] = useState<Source | null>(null);
  const [browsePath, setBrowsePath] = useState("");
  const [confirmDelete, setConfirmDelete] = useState<Source | null>(null);
  const toast = useToast();

  const browseQuery = browseSource
    ? `/api/sources/${browseSource.id}/browse?path=${encodeURIComponent(browsePath)}`
    : null;
  const { data: browseData, loading: browseLoading } = useAsync<BrowseResult>(browseQuery, [browsePath, browseSource?.id]);

  useEffect(() => {
    if (browseSource) setBrowsePath("");
  }, [browseSource?.id]);

  const openCreate = () => {
    setEditing(null);
    setForm({ ...EMPTY_FORM, config: typeDefaults("local") });
    setTestResult(null);
    setFormOpen(true);
  };

  const openEdit = (source: Source) => {
    setEditing(source);
    setForm({
      name: source.name,
      type: source.type,
      config: { ...typeDefaults(source.type), ...source.config, password: "" },
    });
    setTestResult(null);
    setFormOpen(true);
  };

  const changeType = (type: SourceForm["type"]) => {
    setForm({ ...form, type, config: typeDefaults(type) });
    setTestResult(null);
  };

  const setConfig = (key: string, value: any) => {
    setForm((current) => ({ ...current, config: { ...current.config, [key]: value } }));
  };

  const save = async () => {
    if (!form.name.trim()) {
      toast.error("请填写文件源名称");
      return;
    }
    setSaving(true);
    try {
      const payload = { name: form.name.trim(), type: form.type, config: form.config };
      if (editing) {
        await api.put(`/api/sources/${editing.id}`, payload);
        toast.success("文件源已更新");
      } else {
        await api.post("/api/sources", payload);
        toast.success("文件源已添加");
      }
      setFormOpen(false);
      reload();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "保存失败");
    } finally {
      setSaving(false);
    }
  };

  const test = async () => {
    setTesting(true);
    setTestResult(null);
    try {
      let result: { ok?: boolean; entries?: number; normalized_url?: string };
      if (editing && !form.config.password) {
        result = await api.post(`/api/sources/${editing.id}/test`);
      } else {
        result = await api.post("/api/sources/test", { type: form.type, config: form.config });
      }
      if (result.normalized_url) {
        setConfig("url", result.normalized_url);
        setTestResult({
          ok: true,
          message: `连接成功，已自动补全为 ${result.normalized_url}（OpenList / Alist 默认 /dav 路径），根目录下有 ${result.entries ?? 0} 个条目`,
        });
      } else {
        setTestResult({ ok: true, message: `连接成功，根目录下有 ${result.entries ?? 0} 个条目` });
      }
    } catch (err) {
      setTestResult({ ok: false, message: err instanceof Error ? err.message : "连接失败" });
    } finally {
      setTesting(false);
    }
  };

  const remove = async () => {
    if (!confirmDelete) return;
    try {
      await api.del(`/api/sources/${confirmDelete.id}`);
      toast.success("文件源已删除");
      setConfirmDelete(null);
      reload();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "删除失败");
    }
  };

  if (loading && !sources) {
    return (
      <div className="flex h-64 items-center justify-center gap-2 text-sm text-slate-400">
        <Spinner /> 加载文件源…
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-7xl space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-lg font-semibold text-white">文件源</h1>
          <p className="mt-1 text-xs text-slate-500">
            配置待整理文件所在的位置。支持本地挂载目录、WebDAV 与 SMB 共享，密码会加密存储。
          </p>
        </div>
        <Button variant="primary" onClick={openCreate}>
          <Plus size={15} /> 添加文件源
        </Button>
      </div>

      {(sources || []).length === 0 ? (
        <EmptyState
          icon={<FolderOpen size={26} />}
          title="还没有文件源"
          description="本地目录是最简单的方式：把电影 / 剧集目录挂载进容器，然后填写容器内路径。"
          action={
            <Button variant="primary" className="mt-2" onClick={openCreate}>
              <Plus size={15} /> 添加第一个文件源
            </Button>
          }
        />
      ) : (
        <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
          {(sources || []).map((source) => {
            const meta = TYPE_META[source.type] || TYPE_META.local;
            const Icon = meta.icon;
            return (
              <Card key={source.id} className="flex flex-col gap-3.5">
                <div className="flex items-start justify-between gap-3">
                  <div className="flex min-w-0 items-center gap-3">
                    <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl border border-white/10 bg-white/[0.04]">
                      <Icon size={18} className="text-mint-400" />
                    </div>
                    <div className="min-w-0">
                      <div className="truncate text-sm font-medium text-white">{source.name}</div>
                      <div className="mt-0.5 flex items-center gap-1.5 text-[11px] text-slate-500">
                        <Badge tone={source.type === "local" ? "mint" : source.type === "webdav" ? "sky" : "violet"}>
                          {meta.label}
                        </Badge>
                        {source.has_password ? (
                          <span className="inline-flex items-center gap-0.5">
                            <ShieldCheck size={11} /> 已加密凭据
                          </span>
                        ) : null}
                      </div>
                    </div>
                  </div>
                </div>

                <div className="truncate rounded-lg border border-white/[0.06] bg-ink-950/50 px-2.5 py-2 font-mono text-[11px] text-slate-400" title={typeSummary(source)}>
                  {typeSummary(source)}
                </div>

                <div className="text-[11px] text-slate-500">
                  {source.libraries && source.libraries.length > 0
                    ? `被 ${source.libraries.length} 个影视库使用：${source.libraries.map((entry) => entry.name).join("、")}`
                    : "尚未关联影视库"}
                </div>

                <div className="mt-auto flex items-center justify-between border-t border-white/[0.06] pt-3">
                  <Button
                    className="px-2.5 py-1.5 text-xs"
                    onClick={() => setBrowseSource(source)}
                    title="浏览目录"
                  >
                    <FolderOpen size={13} /> 浏览
                  </Button>
                  <div className="flex gap-1.5">
                    <Button className="px-2.5 py-1.5 text-xs" onClick={() => openEdit(source)}>
                      <Pencil size={13} /> 编辑
                    </Button>
                    <Button variant="danger" className="px-2 py-1.5 text-xs" onClick={() => setConfirmDelete(source)}>
                      <Trash2 size={13} />
                    </Button>
                  </div>
                </div>
              </Card>
            );
          })}
        </div>
      )}

      {/* 添加 / 编辑 */}
      <Modal
        open={formOpen}
        onClose={() => setFormOpen(false)}
        title={editing ? "编辑文件源" : "添加文件源"}
        width="max-w-xl"
        footer={
          <>
            <Button onClick={test} loading={testing}>
              <Wifi size={15} /> 测试连接
            </Button>
            <div className="flex-1" />
            <Button onClick={() => setFormOpen(false)}>取消</Button>
            <Button variant="primary" loading={saving} onClick={save}>
              保存
            </Button>
          </>
        }
      >
        <div className="space-y-4">
          <div>
            <label className="label">类型</label>
            <div className="grid grid-cols-3 gap-2">
              {(Object.keys(TYPE_META) as SourceForm["type"][]).map((type) => {
                const meta = TYPE_META[type];
                const Icon = meta.icon;
                return (
                  <button
                    key={type}
                    type="button"
                    onClick={() => changeType(type)}
                    className={clsx(
                      "flex flex-col items-center gap-1.5 rounded-xl border px-2 py-3 text-xs transition",
                      form.type === type
                        ? "border-mint-500/50 bg-mint-500/[0.09] text-mint-300"
                        : "border-white/10 bg-white/[0.02] text-slate-400 hover:border-white/20",
                    )}
                  >
                    <Icon size={18} />
                    {meta.label}
                  </button>
                );
              })}
            </div>
            <p className="mt-2 text-[11px] leading-relaxed text-slate-500">{TYPE_META[form.type].hint}</p>
          </div>

          <Field label="名称">
            <Input
              value={form.name}
              onChange={(event) => setForm({ ...form, name: event.target.value })}
              placeholder="例如：NAS 电影 / 移动硬盘"
            />
          </Field>

          {form.type === "local" ? (
            <Field label="容器内路径" hint="填写 Docker 容器内的挂载路径，例如 /media；不是宿主机路径。">
              <Input
                value={form.config.base_path || ""}
                onChange={(event) => setConfig("base_path", event.target.value)}
                placeholder="/media"
              />
            </Field>
          ) : null}

          {form.type === "webdav" ? (
            <>
              <Field
                label="WebDAV 地址"
                hint="OpenList / Alist 填 http://IP:端口/dav；Nextcloud 填 https://域名/remote.php/dav/files/用户名/"
              >
                <Input
                  value={form.config.url || ""}
                  onChange={(event) => setConfig("url", event.target.value)}
                  placeholder="http://192.168.1.10:5244/dav"
                />
              </Field>
              {/^https?:\/\/[^/]+\/?$/i.test(String(form.config.url || "").trim()) ? (
                <p className="-mt-1.5 text-[11px] leading-relaxed text-amber-300/90">
                  地址看起来缺少 WebDAV 路径：OpenList / Alist 需要写成 http://IP:端口/dav，点「测试连接」会自动尝试补全。
                </p>
              ) : null}
              <div className="grid grid-cols-2 gap-4">
                <Field label="用户名">
                  <Input
                    value={form.config.username || ""}
                    onChange={(event) => setConfig("username", event.target.value)}
                    autoComplete="off"
                  />
                </Field>
                <Field label="密码" hint="留空保持不变">
                  <Input
                    type="password"
                    value={form.config.password || ""}
                    onChange={(event) => setConfig("password", event.target.value)}
                    autoComplete="new-password"
                  />
                </Field>
              </div>
              <Switch
                checked={form.config.verify_tls !== false}
                onChange={(value) => setConfig("verify_tls", value)}
                label="校验 HTTPS 证书"
                hint="自签名证书的 NAS 可以关闭"
              />
            </>
          ) : null}

          {form.type === "smb" ? (
            <>
              <div className="grid grid-cols-2 gap-4">
                <Field label="主机">
                  <Input
                    value={form.config.host || ""}
                    onChange={(event) => setConfig("host", event.target.value)}
                    placeholder="192.168.1.10"
                  />
                </Field>
                <Field label="共享名">
                  <Input
                    value={form.config.share || ""}
                    onChange={(event) => setConfig("share", event.target.value)}
                    placeholder="media"
                  />
                </Field>
              </div>
              <div className="grid grid-cols-3 gap-4">
                <Field label="端口">
                  <Input
                    type="number"
                    value={form.config.port ?? 445}
                    onChange={(event) => setConfig("port", Number(event.target.value))}
                  />
                </Field>
                <Field label="域" hint="可选">
                  <Input
                    value={form.config.domain || ""}
                    onChange={(event) => setConfig("domain", event.target.value)}
                    placeholder="WORKGROUP"
                  />
                </Field>
                <Field label="用户名">
                  <Input
                    value={form.config.username || ""}
                    onChange={(event) => setConfig("username", event.target.value)}
                    autoComplete="off"
                  />
                </Field>
              </div>
              <Field label="密码" hint="留空保持不变">
                <Input
                  type="password"
                  value={form.config.password || ""}
                  onChange={(event) => setConfig("password", event.target.value)}
                  autoComplete="new-password"
                />
              </Field>
            </>
          ) : null}

          {testResult ? (
            <div
              className={clsx(
                "rounded-xl border px-3.5 py-2.5 text-xs",
                testResult.ok
                  ? "border-mint-500/30 bg-mint-500/[0.08] text-mint-200"
                  : "border-rose-500/30 bg-rose-500/[0.08] text-rose-200",
              )}
            >
              {testResult.message}
            </div>
          ) : null}
        </div>
      </Modal>

      {/* 目录浏览 */}
      <Modal
        open={Boolean(browseSource)}
        onClose={() => setBrowseSource(null)}
        title={`浏览：${browseSource?.name || ""}`}
        width="max-w-2xl"
      >
        <div className="space-y-3">
          <div className="flex items-center gap-2">
            <Button
              className="px-2.5 py-1.5 text-xs"
              disabled={!browsePath}
              onClick={() => setBrowsePath(browsePath.includes("/") ? browsePath.split("/").slice(0, -1).join("/") : "")}
            >
              <ArrowLeft size={13} /> 上一级
            </Button>
            <div className="flex-1 truncate rounded-lg border border-white/[0.06] bg-ink-950/50 px-2.5 py-2 font-mono text-[11px] text-slate-400">
              /{browsePath}
            </div>
          </div>
          {browseLoading ? (
            <div className="flex h-40 items-center justify-center gap-2 text-xs text-slate-400">
              <Spinner /> 读取目录…
            </div>
          ) : (
            <div className="max-h-[420px] space-y-1 overflow-y-auto">
              {(browseData?.entries || []).length === 0 ? (
                <div className="py-10 text-center text-xs text-slate-500">这个目录下没有子文件夹</div>
              ) : (
                (browseData?.entries || []).map((entry) => (
                  <button
                    key={entry.path}
                    className="flex w-full items-center gap-2.5 rounded-lg px-3 py-2 text-left text-xs text-slate-300 transition hover:bg-white/[0.05]"
                    onClick={() => setBrowsePath(entry.path)}
                  >
                    <Folder size={15} className="text-sky-300/80" />
                    <span className="truncate">{entry.name}</span>
                  </button>
                ))
              )}
            </div>
          )}
          <p className="text-[11px] leading-relaxed text-slate-500">
            目录浏览用于确认连接与路径。创建影视库时填写的「待整理目录」是相对于文件源根目录的路径。
          </p>
        </div>
      </Modal>

      <ConfirmDialog
        open={Boolean(confirmDelete)}
        title="删除文件源"
        danger
        confirmText="删除"
        message={
          <>
            确定删除「{confirmDelete?.name}」？使用该文件源的影视库与识别记录也会一并删除，
            <b>磁盘上的文件不会被删除</b>。
          </>
        }
        onConfirm={remove}
        onClose={() => setConfirmDelete(null)}
      />
    </div>
  );
}
