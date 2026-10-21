import { useState } from "react";
import { Link } from "react-router-dom";
import { FolderOpen, FolderSearch, HardDrive, Library, Plus, RefreshCw, Trash2 } from "lucide-react";
import type { Library as LibraryType, Source } from "../types";
import { useAsync } from "../lib/hooks";
import { api } from "../lib/api";
import { useToast } from "../lib/toast";
import { Badge, Button, Card, ConfirmDialog, EmptyState, Field, Input, Modal, Poster, Select, Spinner } from "../components/ui";
import { DirectoryPicker } from "../components/DirectoryPicker";
import { formatTime } from "../lib/format";

interface LibraryForm {
  name: string;
  source_id: number | "";
  root_path: string;
  target_root: string;
  media_type: string;
}

const EMPTY_FORM: LibraryForm = { name: "", source_id: "", root_path: "", target_root: "", media_type: "auto" };

export default function LibrariesPage() {
  const { data: libraries, loading, reload } = useAsync<LibraryType[]>("/api/libraries", [], { pollMs: 6000 });
  const { data: sources } = useAsync<Source[]>("/api/sources");
  const [dialogOpen, setDialogOpen] = useState(false);
  const [picker, setPicker] = useState<"root_path" | "target_root" | null>(null);
  const [form, setForm] = useState<LibraryForm>(EMPTY_FORM);
  const [saving, setSaving] = useState(false);
  const [scanning, setScanning] = useState<number | null>(null);
  const [confirmDelete, setConfirmDelete] = useState<LibraryType | null>(null);
  const toast = useToast();

  const openCreate = () => {
    setForm({ ...EMPTY_FORM, source_id: sources?.[0]?.id ?? "" });
    setDialogOpen(true);
  };

  const save = async () => {
    if (!form.name.trim() || form.source_id === "") {
      toast.error("请填写名称并选择文件源");
      return;
    }
    setSaving(true);
    try {
      await api.post("/api/libraries", {
        name: form.name.trim(),
        source_id: Number(form.source_id),
        root_path: form.root_path,
        target_root: form.target_root,
        media_type: form.media_type,
      });
      toast.success("影视库已创建");
      setDialogOpen(false);
      reload();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "创建失败");
    } finally {
      setSaving(false);
    }
  };

  const scan = async (library: LibraryType) => {
    setScanning(library.id);
    try {
      await api.post(`/api/libraries/${library.id}/scan`);
      toast.success(`「${library.name}」开始扫描，进度可在任务中心查看`);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "扫描失败");
    } finally {
      setScanning(null);
    }
  };

  const remove = async () => {
    if (!confirmDelete) return;
    try {
      await api.del(`/api/libraries/${confirmDelete.id}`);
      toast.success("影视库已删除（文件不会被删除）");
      setConfirmDelete(null);
      reload();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "删除失败");
    }
  };

  if (loading && !libraries) {
    return (
      <div className="flex h-64 items-center justify-center gap-2 text-sm text-slate-400">
        <Spinner /> 加载影视库…
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-7xl space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-lg font-semibold text-white">影视库</h1>
          <p className="mt-1 text-xs text-slate-500">每个影视库绑定一个文件源目录，扫描后进入识别与整理流程。</p>
        </div>
        <Button variant="primary" onClick={openCreate} disabled={!sources?.length}>
          <Plus size={15} /> 新建影视库
        </Button>
      </div>

      {!sources?.length ? (
        <Card className="border-amber-500/20 bg-amber-500/[0.05] text-xs text-amber-200">
          还没有文件源。请先到 <Link to="/sources" className="underline">文件源</Link> 页面添加本地目录 / WebDAV / SMB。
        </Card>
      ) : null}

      {(libraries || []).length === 0 ? (
        <EmptyState
          icon={<Library size={26} />}
          title="还没有影视库"
          description="创建影视库时可以选择片源目录和整理后的目标目录。建议先用一个测试目录跑一遍流程。"
        />
      ) : (
        <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
          {(libraries || []).map((library) => (
            <Card key={library.id} className="flex flex-col gap-4">
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0">
                  <Link to={`/libraries/${library.id}`} className="truncate text-sm font-semibold text-white hover:text-mint-300">
                    {library.name}
                  </Link>
                  <div className="mt-1 flex items-center gap-1.5 text-[11px] text-slate-500">
                    <HardDrive size={11} />
                    {library.source_type.toUpperCase()} · {library.source_name}
                  </div>
                  <div className="mt-0.5 truncate font-mono text-[11px] text-slate-600" title={`/${library.root_path}`}>
                    /{library.root_path || ""} → /{library.target_root || ""}
                  </div>
                </div>
                <div className="flex shrink-0 gap-1">
                  {library.is_demo ? <Badge tone="violet">演示</Badge> : null}
                  <Badge tone={library.media_type === "tv" ? "sky" : library.media_type === "movie" ? "mint" : "slate"}>
                    {library.media_type === "auto" ? "自动" : library.media_type === "tv" ? "剧集" : "电影"}
                  </Badge>
                </div>
              </div>

              <div className="flex gap-1.5">
                {library.posters.slice(0, 5).map((url, index) => (
                  <Poster key={`${url}-${index}`} url={url} rounded="rounded-lg" className="h-20 w-[54px] shrink-0" />
                ))}
                {library.posters.length === 0
                  ? Array.from({ length: 5 }).map((_, index) => (
                      <div key={index} className="poster-fallback h-20 w-[54px] shrink-0 rounded-lg opacity-60" />
                    ))
                  : null}
              </div>

              <div className="flex flex-wrap gap-1.5 text-[11px]">
                <Badge tone="mint">{library.counts.organized} 已整理</Badge>
                {library.counts.matched > 0 ? <Badge tone="sky">{library.counts.matched} 已匹配</Badge> : null}
                <Badge tone={library.counts.review ? "amber" : "slate"}>{library.counts.review} 待确认</Badge>
                <Badge>{library.counts.unmatched} 未识别</Badge>
                {library.counts.failed > 0 ? <Badge tone="rose">{library.counts.failed} 失败</Badge> : null}
              </div>

              <div className="flex items-center justify-between border-t border-white/[0.06] pt-3">
                <span className="text-[11px] text-slate-600">
                  {library.last_scan_at ? `上次扫描 ${formatTime(library.last_scan_at)}` : "尚未扫描"}
                </span>
                <div className="flex gap-1.5">
                  <Button
                    className="px-2.5 py-1.5 text-xs"
                    loading={scanning === library.id}
                    onClick={() => scan(library)}
                    title="扫描识别"
                  >
                    <RefreshCw size={13} /> 扫描
                  </Button>
                  <Button className="px-2.5 py-1.5 text-xs" onClick={() => (window.location.href = `/libraries/${library.id}`)}>
                    <FolderSearch size={13} /> 详情
                  </Button>
                  <Button variant="danger" className="px-2 py-1.5 text-xs" onClick={() => setConfirmDelete(library)}>
                    <Trash2 size={13} />
                  </Button>
                </div>
              </div>
            </Card>
          ))}
        </div>
      )}

      <Modal
        open={dialogOpen}
        onClose={() => setDialogOpen(false)}
        title="新建影视库"
        width="max-w-2xl"
        footer={
          <>
            <Button onClick={() => setDialogOpen(false)}>取消</Button>
            <Button variant="primary" loading={saving} onClick={save}>
              创建
            </Button>
          </>
        }
      >
        <div className="space-y-4">
          <Field label="名称">
            <Input
              value={form.name}
              onChange={(event) => setForm({ ...form, name: event.target.value })}
              placeholder="例如：主库 / 纪录片"
            />
          </Field>
          <Field label="文件源">
            <Select
              value={form.source_id}
              onChange={(event) => setForm({ ...form, source_id: event.target.value ? Number(event.target.value) : "" })}
            >
              <option value="">请选择文件源</option>
              {(sources || []).map((source) => (
                <option key={source.id} value={source.id}>
                  {source.name}（{source.type}）
                </option>
              ))}
            </Select>
          </Field>
          <div className="grid grid-cols-2 gap-4">
            <Field label="待整理目录（相对文件源）" hint="扫描这里的新文件；点「浏览」可直接选择，留空表示文件源根目录">
              <div className="flex gap-2">
                <Input
                  className="min-w-0 flex-1"
                  value={form.root_path}
                  onChange={(event) => setForm({ ...form, root_path: event.target.value })}
                  placeholder="inbox"
                />
                <Button
                  type="button"
                  className="shrink-0 px-3"
                  disabled={form.source_id === ""}
                  onClick={() => setPicker("root_path")}
                >
                  <FolderOpen size={14} /> 浏览
                </Button>
              </div>
            </Field>
            <Field label="整理后目录（相对文件源）" hint="整理后的文件存放位置；点「浏览」可直接选择，留空表示与待整理目录相同的根">
              <div className="flex gap-2">
                <Input
                  className="min-w-0 flex-1"
                  value={form.target_root}
                  onChange={(event) => setForm({ ...form, target_root: event.target.value })}
                  placeholder="library"
                />
                <Button
                  type="button"
                  className="shrink-0 px-3"
                  disabled={form.source_id === ""}
                  onClick={() => setPicker("target_root")}
                >
                  <FolderOpen size={14} /> 浏览
                </Button>
              </div>
            </Field>
          </div>
          <Field label="媒体类型" hint="自动模式会按文件名与季集特征判断电影或剧集">
            <Select value={form.media_type} onChange={(event) => setForm({ ...form, media_type: event.target.value })}>
              <option value="auto">自动判断</option>
              <option value="movie">电影为主</option>
              <option value="tv">剧集为主</option>
            </Select>
          </Field>
        </div>
      </Modal>

      {picker && form.source_id !== "" ? (
        <DirectoryPicker
          sourceId={Number(form.source_id)}
          title={picker === "root_path" ? "选择待整理目录" : "选择整理后目录"}
          initialPath={form[picker]}
          onSelect={(path) => setForm((prev) => ({ ...prev, [picker]: path }))}
          onClose={() => setPicker(null)}
        />
      ) : null}

      <ConfirmDialog
        open={Boolean(confirmDelete)}
        title="删除影视库"
        danger
        confirmText="删除"
        message={
          <>
            确定删除「{confirmDelete?.name}」？只会移除影视库记录与识别数据，
            <b>不会删除磁盘上的任何文件</b>。
          </>
        }
        onConfirm={remove}
        onClose={() => setConfirmDelete(null)}
      />
    </div>
  );
}
