import { useEffect, useMemo, useState } from "react";
import {
  AlertTriangle,
  ChevronDown,
  ChevronRight,
  ChevronUp,
  File,
  FileAudio,
  FileImage,
  FileText,
  FileVideo,
  Filter,
  Folder,
  FolderOpen,
  Home,
  Move,
  RefreshCw,
  Trash2,
} from "lucide-react";
import clsx from "clsx";
import type {
  FileDeleteResult,
  FileMatchResult,
  FileMoveResult,
  Library as LibraryType,
  LibraryFileEntry,
  LibraryFileList,
} from "../types";
import { api } from "../lib/api";
import { useToast } from "../lib/toast";
import { formatDateTime, formatSize } from "../lib/format";
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
} from "./ui";

type SortKey = "name" | "kind" | "size" | "mtime";
type SortDir = "asc" | "desc";

const KIND_LABELS: Record<string, string> = {
  folder: "文件夹",
  video: "视频",
  subtitle: "字幕",
  audio: "音轨",
  image: "图片",
  meta: "NFO",
  other: "其他",
};

const KIND_TONES: Record<string, string> = {
  folder: "text-sky-300",
  video: "text-mint-300",
  subtitle: "text-violet-300",
  audio: "text-amber-300",
  image: "text-rose-300",
  meta: "text-slate-300",
  other: "text-slate-400",
};

function kindLabel(entry: LibraryFileEntry) {
  return KIND_LABELS[entry.kind] || entry.kind || "文件";
}

function FileIcon({ entry, size = 15 }: { entry: LibraryFileEntry; size?: number }) {
  if (entry.is_dir) return <Folder size={size} className="text-sky-300" />;
  if (entry.kind === "video") return <FileVideo size={size} className="text-mint-300" />;
  if (entry.kind === "subtitle") return <FileText size={size} className="text-violet-300" />;
  if (entry.kind === "audio") return <FileAudio size={size} className="text-amber-300" />;
  if (entry.kind === "image") return <FileImage size={size} className="text-rose-300" />;
  if (entry.kind === "meta") return <FileText size={size} className="text-slate-300" />;
  return <File size={size} className="text-slate-400" />;
}

function SortHeader({
  label,
  column,
  sortKey,
  sortDir,
  onSort,
  className,
}: {
  label: string;
  column: SortKey;
  sortKey: SortKey;
  sortDir: SortDir;
  onSort: (key: SortKey) => void;
  className?: string;
}) {
  const active = sortKey === column;
  return (
    <button
      type="button"
      className={clsx(
        "inline-flex items-center gap-1 text-left text-[11px] font-medium transition hover:text-slate-200",
        active ? "text-slate-200" : "text-slate-500",
        className,
      )}
      onClick={() => onSort(column)}
    >
      {label}
      {active ? (
        sortDir === "asc" ? (
          <ChevronUp size={12} />
        ) : (
          <ChevronDown size={12} />
        )
      ) : null}
    </button>
  );
}

export function LibraryFiles({
  library,
  onChanged,
}: {
  library: LibraryType;
  onChanged?: () => void;
}) {
  const toast = useToast();
  const [path, setPath] = useState("");
  const [data, setData] = useState<LibraryFileList | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [reloadKey, setReloadKey] = useState(0);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [sortKey, setSortKey] = useState<SortKey>("name");
  const [sortDir, setSortDir] = useState<SortDir>("asc");

  const [deleteOpen, setDeleteOpen] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [moveOpen, setMoveOpen] = useState(false);
  const [moveDestination, setMoveDestination] = useState("");
  const [moving, setMoving] = useState(false);

  const [ruleOpen, setRuleOpen] = useState(false);
  const [rulePattern, setRulePattern] = useState(".tc");
  const [ruleMode, setRuleMode] = useState<"contains" | "regex">("contains");
  const [ruleRecursive, setRuleRecursive] = useState(false);
  const [ruleResult, setRuleResult] = useState<FileMatchResult | null>(null);
  const [ruleLoading, setRuleLoading] = useState(false);
  const [ruleConfirm, setRuleConfirm] = useState(false);
  const [ruleDeleting, setRuleDeleting] = useState(false);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError("");
    const query = path ? `?path=${encodeURIComponent(path)}` : "";
    api
      .get<LibraryFileList>(`/api/libraries/${library.id}/files${query}`)
      .then((result) => {
        if (!cancelled) setData(result);
      })
      .catch((err) => {
        if (!cancelled) setError(err instanceof Error ? err.message : "读取目录失败");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [library.id, path, reloadKey]);

  useEffect(() => {
    setSelected(new Set());
  }, [path]);

  const entries = data?.entries || [];
  const sortedEntries = useMemo(() => {
    const value = (entry: LibraryFileEntry) => {
      if (sortKey === "kind") return kindLabel(entry);
      if (sortKey === "size") return entry.size;
      if (sortKey === "mtime") return new Date(entry.modified_at || 0).getTime();
      return entry.name;
    };
    return [...entries].sort((a, b) => {
      if (a.is_dir !== b.is_dir) return a.is_dir ? -1 : 1;
      const left = value(a);
      const right = value(b);
      let result = 0;
      if (typeof left === "number" && typeof right === "number") {
        result = left - right;
      } else {
        result = String(left).localeCompare(String(right), "zh-CN", {
          numeric: true,
          sensitivity: "base",
        });
      }
      return sortDir === "asc" ? result : -result;
    });
  }, [entries, sortKey, sortDir]);

  const selectedEntries = entries.filter((entry) => selected.has(entry.path));
  const allSelected = entries.length > 0 && entries.every((entry) => selected.has(entry.path));
  const selectedSize = selectedEntries.reduce((total, entry) => total + (entry.is_dir ? 0 : entry.size), 0);
  const totalSize = data?.counts.size || 0;

  const navigate = (next: string) => {
    setData(null);
    setLoading(true);
    setPath(next);
  };

  const toggleSelected = (entryPath: string) => {
    setSelected((current) => {
      const next = new Set(current);
      if (next.has(entryPath)) next.delete(entryPath);
      else next.add(entryPath);
      return next;
    });
  };

  const toggleAll = () => {
    setSelected(
      allSelected ? new Set() : new Set(entries.map((entry) => entry.path)),
    );
  };

  const refresh = () => setReloadKey((value) => value + 1);

  const afterChange = () => {
    refresh();
    onChanged?.();
  };

  const openMove = () => {
    setMoveDestination("");
    setMoveOpen(true);
  };

  const confirmDeleteSelected = async () => {
    if (deleting || !selected.size) return;
    setDeleting(true);
    try {
      const result = await api.post<FileDeleteResult>(
        `/api/libraries/${library.id}/files/delete`,
        { paths: [...selected] },
      );
      if (result.failed.length) {
        toast.error(`有 ${result.failed.length} 项删除失败：${result.failed[0].error}`);
      } else {
        toast.success(`已删除 ${result.deleted.length} 项文件 / 文件夹`);
      }
      setDeleteOpen(false);
      setSelected(new Set());
      afterChange();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "删除失败");
    } finally {
      setDeleting(false);
    }
  };

  const confirmMove = async () => {
    if (moving || !selected.size) return;
    setMoving(true);
    try {
      const result = await api.post<FileMoveResult>(
        `/api/libraries/${library.id}/files/move`,
        { paths: [...selected], destination: moveDestination },
      );
      if (result.failed.length) {
        toast.error(`有 ${result.failed.length} 项移动失败：${result.failed[0].error}`);
      } else if (result.moved.length) {
        toast.success(`已移动 ${result.moved.length} 项到 ${result.destination || "根目录"}`);
      } else {
        toast.push("没有需要移动的文件");
      }
      setMoveOpen(false);
      setSelected(new Set());
      afterChange();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "移动失败");
    } finally {
      setMoving(false);
    }
  };

  const runRuleMatch = async () => {
    if (ruleLoading) return;
    if (!rulePattern.trim()) {
      toast.error("请输入要匹配的内容");
      return;
    }
    setRuleLoading(true);
    setRuleResult(null);
    try {
      const result = await api.post<FileMatchResult>(
        `/api/libraries/${library.id}/files/match`,
        {
          path,
          pattern: rulePattern,
          mode: ruleMode,
          recursive: ruleRecursive,
        },
      );
      setRuleResult(result);
      if (!result.total) toast.push("没有找到匹配的文件");
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "匹配失败");
    } finally {
      setRuleLoading(false);
    }
  };

  const confirmRuleDelete = async () => {
    if (ruleDeleting || !ruleResult || !ruleResult.matches.length) return;
    setRuleDeleting(true);
    try {
      const result = await api.post<FileDeleteResult>(
        `/api/libraries/${library.id}/files/delete`,
        { paths: ruleResult.matches.map((entry) => entry.path) },
      );
      if (result.failed.length) {
        toast.error(`有 ${result.failed.length} 个文件删除失败：${result.failed[0].error}`);
      } else {
        toast.success(`已按规则删除 ${result.deleted.length} 个文件`);
      }
      setRuleConfirm(false);
      setRuleOpen(false);
      setRuleResult(null);
      setSelected(new Set());
      afterChange();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "删除失败");
    } finally {
      setRuleDeleting(false);
    }
  };

  const handleSort = (key: SortKey) => {
    if (sortKey === key) {
      setSortDir((current) => (current === "asc" ? "desc" : "asc"));
    } else {
      setSortKey(key);
      setSortDir("asc");
    }
  };

  const parts = path ? path.split("/") : [];
  const ruleCanDelete = Boolean(ruleResult && ruleResult.matches.length && !ruleResult.truncated);

  return (
    <Card className="overflow-hidden p-0">
      <div className="border-b border-white/[0.06] px-4 py-3.5">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="flex min-w-0 items-center gap-2">
            <Badge tone="sky">所有文件</Badge>
            <span className="text-[11px] text-slate-500">
              {data?.counts.files || 0} 个文件 · {data?.counts.dirs || 0} 个文件夹 · {formatSize(totalSize)}
            </span>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <Button className="px-2.5 py-1.5 text-xs" onClick={refresh} disabled={loading}>
              <RefreshCw size={13} className={loading ? "animate-spin" : ""} /> 刷新
            </Button>
            <Button className="px-2.5 py-1.5 text-xs" onClick={() => setRuleOpen(true)}>
              <Filter size={13} /> 按规则清理
            </Button>
          </div>
        </div>

        <div className="mt-3 flex min-w-0 flex-wrap items-center gap-1 text-[11px] text-slate-500">
          <button
            className="inline-flex items-center gap-1 rounded px-1.5 py-1 transition hover:bg-white/[0.06] hover:text-slate-300"
            onClick={() => navigate("")}
          >
            <Home size={12} /> {library.root_path || "/"}
          </button>
          {parts.map((part, index) => {
            const target = parts.slice(0, index + 1).join("/");
            return (
              <span key={target} className="inline-flex items-center gap-1">
                <ChevronRight size={11} className="text-slate-700" />
                <button
                  className="max-w-[220px] truncate rounded px-1.5 py-1 transition hover:bg-white/[0.06] hover:text-slate-300"
                  onClick={() => navigate(target)}
                  title={target}
                >
                  {part}
                </button>
              </span>
            );
          })}
        </div>

        <div className="mt-3 flex flex-wrap items-center gap-2 text-[11px] text-slate-400">
          <button
            className="inline-flex items-center gap-1.5 rounded-lg border border-white/10 px-2.5 py-1.5 transition hover:bg-white/[0.05]"
            onClick={toggleAll}
            disabled={!entries.length}
          >
            <span
              className={clsx(
                "grid h-3.5 w-3.5 place-items-center rounded border",
                allSelected ? "border-mint-400 bg-mint-500/80 text-ink-950" : "border-white/20",
              )}
            >
              {allSelected ? <span className="text-[9px] font-bold">✓</span> : null}
            </span>
            {allSelected ? "取消全选" : "全选当前目录"}
          </button>
          {selected.size ? (
            <>
              <span className="text-slate-500">
                已选 {selected.size} 项{selectedSize ? ` · ${formatSize(selectedSize)}` : ""}
              </span>
              <Button
                className="px-2.5 py-1.5 text-xs"
                onClick={() => setSelected(new Set())}
              >
                清空
              </Button>
              <Button
                className="px-2.5 py-1.5 text-xs"
                onClick={openMove}
                disabled={moving}
              >
                <Move size={13} /> 移动选中
              </Button>
              <Button
                variant="danger"
                className="px-2.5 py-1.5 text-xs"
                onClick={() => setDeleteOpen(true)}
                disabled={deleting}
              >
                <Trash2 size={13} /> 删除选中
              </Button>
            </>
          ) : (
            <span className="text-slate-600">勾选文件或文件夹后可批量移动 / 删除</span>
          )}
        </div>
      </div>

      {loading && !data ? (
        <div className="flex min-h-[320px] items-center justify-center gap-2 text-xs text-slate-500">
          <Spinner /> 正在读取目录…
        </div>
      ) : error ? (
        <div className="flex min-h-[320px] flex-col items-center justify-center gap-3 px-5 text-center">
          <AlertTriangle size={22} className="text-rose-300" />
          <div className="text-sm text-slate-300">读取目录失败</div>
          <div className="max-w-xl text-xs leading-relaxed text-slate-500">{error}</div>
          <Button onClick={refresh}>重试</Button>
        </div>
      ) : !sortedEntries.length ? (
        <div className="p-5">
          <EmptyState
            icon={<FolderOpen size={28} />}
            title="目录为空"
            description="这里没有文件。可以返回上一级，或检查文件源是否已经同步。"
            action={<Button onClick={() => navigate(data?.parent || "")}>返回上一级</Button>}
          />
        </div>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full min-w-[780px] border-collapse text-left">
            <thead>
              <tr className="border-b border-white/[0.06] bg-white/[0.015]">
                <th className="w-9 px-4 py-2.5">
                  <input
                    type="checkbox"
                    aria-label="全选当前目录"
                    checked={allSelected}
                    onChange={toggleAll}
                    className="h-3.5 w-3.5 accent-mint-400"
                  />
                </th>
                <th className="px-2 py-2.5">
                  <SortHeader
                    label="名称"
                    column="name"
                    sortKey={sortKey}
                    sortDir={sortDir}
                    onSort={handleSort}
                  />
                </th>
                <th className="w-20 px-2 py-2.5">
                  <SortHeader
                    label="类型"
                    column="kind"
                    sortKey={sortKey}
                    sortDir={sortDir}
                    onSort={handleSort}
                  />
                </th>
                <th className="w-24 px-2 py-2.5 text-right">
                  <SortHeader
                    label="大小"
                    column="size"
                    sortKey={sortKey}
                    sortDir={sortDir}
                    onSort={handleSort}
                    className="ml-auto justify-end"
                  />
                </th>
                <th className="w-36 px-2 py-2.5">
                  <SortHeader
                    label="修改时间"
                    column="mtime"
                    sortKey={sortKey}
                    sortDir={sortDir}
                    onSort={handleSort}
                  />
                </th>
              </tr>
            </thead>
            <tbody>
              {sortedEntries.map((entry) => {
                const checked = selected.has(entry.path);
                return (
                  <tr
                    key={entry.path}
                    className={clsx(
                      "border-b border-white/[0.035] transition last:border-0",
                      checked ? "bg-mint-500/[0.055]" : "hover:bg-white/[0.025]",
                    )}
                  >
                    <td className="px-4 py-2.5 align-middle">
                      <input
                        type="checkbox"
                        aria-label={`选择 ${entry.name}`}
                        checked={checked}
                        onChange={() => toggleSelected(entry.path)}
                        className="h-3.5 w-3.5 accent-mint-400"
                      />
                    </td>
                    <td className="px-2 py-2.5">
                      <div className="flex min-w-0 items-center gap-2">
                        <span className="shrink-0">
                          <FileIcon entry={entry} />
                        </span>
                        {entry.is_dir ? (
                          <button
                            className="min-w-0 truncate text-left text-xs text-slate-200 transition hover:text-mint-300"
                            onClick={() => navigate(entry.path)}
                            title={entry.path}
                          >
                            {entry.name}
                          </button>
                        ) : (
                          <span className="min-w-0 truncate text-xs text-slate-300" title={entry.path}>
                            {entry.name}
                          </span>
                        )}
                      </div>
                    </td>
                    <td className="px-2 py-2.5">
                      <span className={clsx("text-[11px]", KIND_TONES[entry.kind] || "text-slate-400")}>
                        {kindLabel(entry)}
                      </span>
                    </td>
                    <td className="px-2 py-2.5 text-right font-mono text-[11px] text-slate-400">
                      {entry.is_dir ? "—" : formatSize(entry.size)}
                    </td>
                    <td className="px-2 py-2.5 text-[11px] text-slate-500">
                      {formatDateTime(entry.modified_at)}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      <div className="border-t border-white/[0.06] px-4 py-2.5 text-[10px] leading-relaxed text-slate-600">
        所有文件视图直接读取待整理目录，包含视频、字幕、NFO、海报和其他文件。删除 / 移动会同步影序的条目记录，但无法撤销。
      </div>

      <Modal
        open={deleteOpen}
        onClose={() => {
          if (!deleting) setDeleteOpen(false);
        }}
        title="删除选中的文件 / 文件夹"
        width="max-w-lg"
        footer={
          <>
            <Button onClick={() => setDeleteOpen(false)} disabled={deleting}>
              取消
            </Button>
            <Button variant="danger" loading={deleting} onClick={confirmDeleteSelected}>
              确认删除
            </Button>
          </>
        }
      >
        <div className="space-y-3 text-sm text-slate-300">
          <div className="flex items-start gap-2.5 rounded-xl border border-rose-500/20 bg-rose-500/[0.06] px-3.5 py-3">
            <AlertTriangle size={18} className="mt-0.5 shrink-0 text-rose-300" />
            <div>
              确定删除选中的 {selectedEntries.length} 项？文件夹会连同内部全部文件一起删除，操作无法撤销。
            </div>
          </div>
          <div className="max-h-40 overflow-y-auto rounded-xl border border-white/[0.06] bg-white/[0.02] p-2">
            {selectedEntries.slice(0, 12).map((entry) => (
              <div key={entry.path} className="flex items-center gap-2 px-1.5 py-1 text-[11px]">
                <FileIcon entry={entry} size={13} />
                <span className="min-w-0 flex-1 truncate text-slate-400" title={entry.path}>
                  {entry.path}
                </span>
                <span className="shrink-0 text-slate-600">{entry.is_dir ? "文件夹" : formatSize(entry.size)}</span>
              </div>
            ))}
            {selectedEntries.length > 12 ? (
              <div className="px-1.5 py-1 text-[10px] text-slate-600">
                还有 {selectedEntries.length - 12} 项…
              </div>
            ) : null}
          </div>
        </div>
      </Modal>

      <Modal
        open={moveOpen}
        onClose={() => {
          if (!moving) setMoveOpen(false);
        }}
        title="移动选中的文件 / 文件夹"
        width="max-w-lg"
        footer={
          <>
            <Button onClick={() => setMoveOpen(false)} disabled={moving}>
              取消
            </Button>
            <Button variant="primary" loading={moving} onClick={confirmMove}>
              开始移动
            </Button>
          </>
        }
      >
        <div className="space-y-4">
          <div className="rounded-xl border border-white/[0.06] bg-white/[0.02] px-3.5 py-2.5 text-[11px] text-slate-400">
            已选 {selected.size} 项。目标路径相对待整理根目录，例如 `归档/待删除`；不存在会自动创建。
          </div>
          <Field label="目标目录" hint="留空表示待整理根目录；不能移动到当前影视库之外。">
            <Input
              value={moveDestination}
              onChange={(event) => setMoveDestination(event.target.value)}
              placeholder="例如：归档 / 2026"
            />
          </Field>
          <div className="flex flex-wrap gap-2">
            <Button className="px-2.5 py-1.5 text-xs" onClick={() => setMoveDestination("")}>
              根目录
            </Button>
            <Button className="px-2.5 py-1.5 text-xs" onClick={() => setMoveDestination(path)}>
              当前目录
            </Button>
          </div>
        </div>
      </Modal>

      <Modal
        open={ruleOpen}
        onClose={() => {
          if (!ruleDeleting) setRuleOpen(false);
        }}
        title="按规则清理文件"
        width="max-w-3xl"
        footer={
          <>
            <Button onClick={() => setRuleOpen(false)} disabled={ruleDeleting}>
              关闭
            </Button>
            <Button onClick={runRuleMatch} loading={ruleLoading}>
              预览匹配
            </Button>
            <Button
              variant="danger"
              disabled={!ruleCanDelete}
              onClick={() => setRuleConfirm(true)}
            >
              删除匹配文件{ruleResult?.matches.length ? `（${ruleResult.matches.length}）` : ""}
            </Button>
          </>
        }
      >
        <div className="space-y-4">
          <div className="grid gap-3 sm:grid-cols-2">
            <Field label="匹配方式">
              <Select
                value={ruleMode}
                onChange={(event) => {
                  setRuleMode(event.target.value as "contains" | "regex");
                  setRuleResult(null);
                }}
              >
                <option value="contains">包含文本（普通匹配）</option>
                <option value="regex">正则表达式</option>
              </Select>
            </Field>
            <Field label="匹配内容" hint="只匹配文件名，不区分大小写">
              <Input
                value={rulePattern}
                onChange={(event) => {
                  setRulePattern(event.target.value);
                  setRuleResult(null);
                }}
                placeholder={ruleMode === "regex" ? String.raw`例如：\.tc$` : "例如：.tc"}
              />
            </Field>
          </div>
          <div className="grid gap-3 sm:grid-cols-2">
            <Field label="范围" hint="包含子目录时会跳过隐藏目录和回收站目录">
              <Select
                value={ruleRecursive ? "recursive" : "current"}
                onChange={(event) => {
                  setRuleRecursive(event.target.value === "recursive");
                  setRuleResult(null);
                }}
              >
                <option value="current">当前目录</option>
                <option value="recursive">当前目录 + 子目录</option>
              </Select>
            </Field>
            <div className="rounded-xl border border-white/[0.06] bg-white/[0.02] px-3.5 py-2.5 text-[11px] text-slate-500">
              当前路径：<span className="font-mono text-slate-400">{path || library.root_path || "/"}</span>
            </div>
          </div>

          {ruleResult ? (
            <div className="space-y-2">
              <div className="flex flex-wrap items-center gap-2 text-[11px]">
                <Badge tone={ruleResult.total ? "rose" : "slate"}>
                  匹配 {ruleResult.total} 个文件
                </Badge>
                <span className="text-slate-500">已扫描 {ruleResult.scanned} 项</span>
                {ruleResult.truncated ? (
                  <span className="text-amber-300">
                    结果超过上限，本次不执行删除，请缩小匹配范围
                  </span>
                ) : null}
              </div>
              {ruleResult.warnings.length ? (
                <div className="rounded-lg border border-amber-500/20 bg-amber-500/[0.06] px-3 py-2 text-[10px] leading-relaxed text-amber-200/80">
                  {ruleResult.warnings.slice(0, 3).map((warning) => (
                    <div key={warning}>{warning}</div>
                  ))}
                </div>
              ) : null}
              {ruleResult.matches.length ? (
                <div className="max-h-64 overflow-y-auto rounded-xl border border-white/[0.06] bg-white/[0.02] p-2">
                  {ruleResult.matches.slice(0, 100).map((entry) => (
                    <div key={entry.path} className="flex items-center gap-2 px-1.5 py-1 text-[11px]">
                      <FileIcon entry={entry} size={13} />
                      <span className="min-w-0 flex-1 truncate text-slate-400" title={entry.path}>
                        {entry.path}
                      </span>
                      <span className="shrink-0 font-mono text-slate-600">{formatSize(entry.size)}</span>
                    </div>
                  ))}
                  {ruleResult.matches.length > 100 ? (
                    <div className="px-1.5 py-1 text-[10px] text-slate-600">
                      仅显示前 100 个；删除时会处理全部 {ruleResult.matches.length} 个匹配文件。
                    </div>
                  ) : null}
                </div>
              ) : (
                <div className="rounded-xl border border-dashed border-white/10 px-4 py-8 text-center text-xs text-slate-500">
                  没有匹配的文件
                </div>
              )}
            </div>
          ) : (
            <div className="rounded-xl border border-dashed border-white/10 px-4 py-8 text-center text-xs text-slate-500">
              先输入匹配内容，再点「预览匹配」查看会删除哪些文件。
            </div>
          )}
        </div>
      </Modal>

      <ConfirmDialog
        open={ruleConfirm}
        title="确认按规则删除"
        confirmText={ruleDeleting ? "删除中…" : "确认删除"}
        danger
        onClose={() => {
          if (!ruleDeleting) setRuleConfirm(false);
        }}
        onConfirm={confirmRuleDelete}
        message={
          <div>
            将从文件源删除 {ruleResult?.matches.length || 0} 个匹配文件，无法撤销。匹配内容：
            <span className="mx-1 font-mono text-rose-200">{ruleResult?.pattern}</span>
          </div>
        }
      />
    </Card>
  );
}
