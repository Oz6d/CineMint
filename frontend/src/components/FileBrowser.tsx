import { useEffect, useMemo, useRef, useState, type MouseEvent } from "react";
import {
  ChevronDown,
  ChevronRight,
  Eye,
  FileVideo,
  Film,
  Folder,
  FolderOpen,
  Loader2,
  PencilLine,
  TextCursorInput,
  Search,
  Tv,
  Wand2,
} from "lucide-react";
import clsx from "clsx";
import type { AttachmentPayload, Library, MediaItem } from "../types";
import { api } from "../lib/api";
import { useToast } from "../lib/toast";
import { formatSize, seasonEpisode } from "../lib/format";
import { Badge, Button, Card, EmptyState, Input, Poster, Select, Spinner, StatusBadge } from "./ui";
import { AttachmentPanel } from "./AttachmentPanel";

type FileNode = { kind: "file"; name: string; path: string; depth: number; item: MediaItem };
type FolderNode = {
  kind: "folder";
  name: string;
  path: string;
  depth: number;
  children: TreeNode[];
  fileCount: number;
};
type TreeNode = FileNode | FolderNode;
type SortMode = "name" | "title" | "status";
type NameMode = "ellipsis" | "wrap" | "scroll";

const NAME_MODE_KEY = "cineflow_file_name_mode";
const NAME_MODES: { value: NameMode; label: string; hint: string }[] = [
  { value: "ellipsis", label: "省略", hint: "超出部分省略，悬停可看完整文件名" },
  { value: "wrap", label: "换行", hint: "完整文件名换行显示" },
  { value: "scroll", label: "横滑", hint: "单行显示，拖动横向滑块查看完整文件名" },
];

function nameClass(mode: NameMode): string {
  if (mode === "wrap") return "whitespace-normal break-all leading-snug";
  if (mode === "scroll") return "overflow-x-auto whitespace-nowrap scroll-x-thin";
  return "truncate";
}

const CN_DIGITS: Record<string, number> = {
  零: 0,
  〇: 0,
  一: 1,
  二: 2,
  两: 2,
  三: 3,
  四: 4,
  五: 5,
  六: 6,
  七: 7,
  八: 8,
  九: 9,
};

function chineseNumber(text: string): number | null {
  if (/^\d+$/.test(text)) return Number(text);
  let total = 0;
  let current = 0;
  for (const char of text) {
    if (char === "十") {
      total += (current || 1) * 10;
      current = 0;
    } else if (char === "百") {
      total += (current || 1) * 100;
      current = 0;
    } else if (char in CN_DIGITS) {
      current = CN_DIGITS[char];
    } else {
      return null;
    }
  }
  return total + current;
}

function seasonRank(name: string): number | null {
  const match =
    name.match(/第\s*([零〇一二两三四五六七八九十百\d]+)\s*[季部]/) ||
    name.match(/season\s*(\d+)/i) ||
    name.match(/^S(\d{1,2})$/i);
  if (!match) return null;
  const value = chineseNumber(match[1]);
  return value != null && Number.isFinite(value) ? value : null;
}

function compareName(a: string, b: string): number {
  const ra = seasonRank(a);
  const rb = seasonRank(b);
  if (ra != null && rb != null && ra !== rb) return ra - rb;
  return a.localeCompare(b, "zh-Hans-CN", { numeric: true, sensitivity: "base" });
}

function relSegments(sourcePath: string, rootPath: string): string[] {
  const clean = (value: string) =>
    (value || "").replace(/\\/g, "/").replace(/\/+$/, "").replace(/^\.\//, "");
  const path = clean(sourcePath);
  const root = clean(rootPath);
  let relative = path;
  if (root && (path === root || path.startsWith(root + "/"))) {
    relative = path.slice(root.length).replace(/^\/+/, "");
  }
  return relative.split("/").filter(Boolean);
}

function statusRank(status: string): number {
  switch (status) {
    case "review":
      return 0;
    case "unmatched":
      return 1;
    case "failed":
      return 2;
    case "matched":
      return 3;
    case "organized":
      return 4;
    default:
      return 5;
  }
}

function sortTree(folder: FolderNode, mode: SortMode): void {
  folder.children.sort((left, right) => {
    if (left.kind !== right.kind) return left.kind === "folder" ? -1 : 1;
    if (left.kind === "folder" && right.kind === "folder") return compareName(left.name, right.name);
    if (left.kind === "file" && right.kind === "file") {
      if (mode === "title") {
        const la = left.item.match?.title || left.item.parsed?.title || "";
        const lb = right.item.match?.title || right.item.parsed?.title || "";
        const diff = la.localeCompare(lb, "zh-Hans-CN", { numeric: true, sensitivity: "base" });
        if (diff !== 0) return diff;
      } else if (mode === "status") {
        const diff = statusRank(left.item.status) - statusRank(right.item.status);
        if (diff !== 0) return diff;
      }
      return compareName(left.name, right.name);
    }
    return 0;
  });
  for (const child of folder.children) {
    if (child.kind === "folder") sortTree(child, mode);
  }
}

function countFiles(folder: FolderNode): number {
  let count = 0;
  for (const child of folder.children) {
    count += child.kind === "file" ? 1 : countFiles(child);
  }
  folder.fileCount = count;
  return count;
}

function buildTree(items: MediaItem[], rootPath: string, mode: SortMode): FolderNode {
  const root: FolderNode = { kind: "folder", name: "", path: "", depth: 0, children: [], fileCount: 0 };
  for (const item of items) {
    const segments = relSegments(item.source_path, rootPath);
    const parts = segments.length ? segments : [item.file_name];
    let folder = root;
    for (let index = 0; index < parts.length - 1; index += 1) {
      const name = parts[index];
      const path = parts.slice(0, index + 1).join("/");
      let next = folder.children.find(
        (child): child is FolderNode => child.kind === "folder" && child.name === name,
      );
      if (!next) {
        next = { kind: "folder", name, path, depth: index + 1, children: [], fileCount: 0 };
        folder.children.push(next);
      }
      folder = next;
    }
    folder.children.push({
      kind: "file",
      name: parts[parts.length - 1],
      path: parts.join("/"),
      depth: parts.length - 1,
      item,
    });
  }
  sortTree(root, mode);
  countFiles(root);
  return root;
}

function walkFiles(node: FolderNode, out: FileNode[] = []): FileNode[] {
  for (const child of node.children) {
    if (child.kind === "file") out.push(child);
    else walkFiles(child, out);
  }
  return out;
}

function collectFolders(node: FolderNode, out: Set<string> = new Set()): Set<string> {
  for (const child of node.children) {
    if (child.kind === "folder") {
      out.add(child.path);
      collectFolders(child, out);
    }
  }
  return out;
}

function defaultExpanded(node: FolderNode, maxDepth: number, out: Set<string> = new Set()): Set<string> {
  for (const child of node.children) {
    if (child.kind === "folder") {
      if (child.depth <= maxDepth) out.add(child.path);
      defaultExpanded(child, maxDepth, out);
    }
  }
  return out;
}

type Row = { kind: "folder"; node: FolderNode } | { kind: "file"; node: FileNode };

function rowKey(row: Row): string {
  return row.kind === "file" ? `f:${row.node.item.id}` : `d:${row.node.path}`;
}

function flattenRows(node: FolderNode, expanded: Set<string>, out: Row[] = []): Row[] {
  for (const child of node.children) {
    if (child.kind === "folder") {
      out.push({ kind: "folder", node: child });
      if (expanded.has(child.path)) flattenRows(child, expanded, out);
    } else {
      out.push({ kind: "file", node: child });
    }
  }
  return out;
}

function pad2(value: number): string {
  return String(value).padStart(2, "0");
}

function pendingEntriesContiguous(pending: Extract<PendingApply, { kind: "episodes" }>): boolean {
  return pending.entries.every(
    (entry, index) => index === 0 || entry.episode === pending.entries[index - 1].episode + 1,
  );
}

type EpisodeEntries = { item: MediaItem; episode: number }[];
type PendingApply =
  | { kind: "movie"; candidate: any; items: MediaItem[] }
  | {
      kind: "episodes";
      candidate: any;
      season: number;
      anchor: number;
      mode: "sequential" | "offset";
      entries: EpisodeEntries;
    };

export function FileBrowser({
  library,
  items,
  selected,
  onSelect,
  onOpenItem,
  onPreview,
  onRename,
  onEditTarget,
  onChanged,
}: {
  library: Library;
  items: MediaItem[];
  selected: number[];
  onSelect: (ids: number[]) => void;
  onOpenItem: (item: MediaItem) => void;
  onPreview: (ids: number[]) => void;
  onRename: (ids: number[]) => void;
  onEditTarget: (item: MediaItem) => void;
  onChanged: () => void;
}) {
  const toast = useToast();
  const [sortMode, setSortMode] = useState<SortMode>("name");
  const [nameMode, setNameMode] = useState<NameMode>(() => {
    const saved = typeof window !== "undefined" ? window.localStorage.getItem(NAME_MODE_KEY) : null;
    return saved === "wrap" || saved === "scroll" ? saved : "ellipsis";
  });
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const initialised = useRef(false);
  const anchorRef = useRef<string | null>(null);

  const [query, setQuery] = useState("");
  const [mediaFilter, setMediaFilter] = useState<"" | "movie" | "tv">("");
  const [results, setResults] = useState<any[] | null>(null);
  const [searchPage, setSearchPage] = useState(1);
  const [searchPages, setSearchPages] = useState(1);
  const [searching, setSearching] = useState(false);
  const [chosen, setChosen] = useState<any | null>(null);
  const [seasons, setSeasons] = useState<any[]>([]);
  const [season, setSeason] = useState<number | null>(null);
  const [episodes, setEpisodes] = useState<any[]>([]);
  const [detailsLoading, setDetailsLoading] = useState(false);
  const [episodesLoading, setEpisodesLoading] = useState(false);
  const [applying, setApplying] = useState(false);
  const [pending, setPending] = useState<PendingApply | null>(null);
  const [attachments, setAttachments] = useState<AttachmentPayload | null>(null);
  const [attachmentsLoading, setAttachmentsLoading] = useState(false);

  const tree = useMemo(() => buildTree(items, library.root_path, sortMode), [items, library.root_path, sortMode]);
  const allFiles = useMemo(() => walkFiles(tree), [tree]);

  useEffect(() => {
    if (initialised.current || !items.length) return;
    initialised.current = true;
    setExpanded(defaultExpanded(tree, 2));
  }, [tree, items.length]);

  const rows = useMemo(() => flattenRows(tree, expanded), [tree, expanded]);
  const selectedSet = useMemo(() => new Set(selected), [selected]);
  const folderFiles = useMemo(() => {
    const map = new Map<string, number[]>();
    const walk = (folder: FolderNode): number[] => {
      const ids: number[] = [];
      for (const child of folder.children) {
        if (child.kind === "file") ids.push(child.item.id);
        else ids.push(...walk(child));
      }
      map.set(folder.path, ids);
      return ids;
    };
    walk(tree);
    return map;
  }, [tree]);
  const folderStats = useMemo(() => {
    const stats = new Map<string, { total: number; selected: number }>();
    const walk = (folder: FolderNode): { total: number; selected: number } => {
      let total = 0;
      let selectedCount = 0;
      for (const child of folder.children) {
        if (child.kind === "file") {
          total += 1;
          if (selectedSet.has(child.item.id)) selectedCount += 1;
        } else {
          const childStats = walk(child);
          total += childStats.total;
          selectedCount += childStats.selected;
        }
      }
      stats.set(folder.path, { total, selected: selectedCount });
      return { total, selected: selectedCount };
    };
    walk(tree);
    return stats;
  }, [tree, selectedSet]);
  const selectedItems = useMemo(
    () => allFiles.map((file) => file.item).filter((item) => selectedSet.has(item.id)),
    [allFiles, selectedSet],
  );
  const selectionKey = selectedItems.map((item) => item.id).join(",");

  useEffect(() => {
    setPending(null);
  }, [selectionKey]);

  const toggleFolder = (path: string) => {
    setExpanded((current) => {
      const next = new Set(current);
      if (next.has(path)) next.delete(path);
      else next.add(path);
      return next;
    });
  };

  const rowIds = (row: Row): number[] =>
    row.kind === "file" ? [row.node.item.id] : folderFiles.get(row.node.path) || [];

  const handleRowClick = (event: MouseEvent, row: Row) => {
    const key = rowKey(row);
    const index = rows.findIndex((entry) => rowKey(entry) === key);
    const modifier = event.metaKey || event.ctrlKey;
    if (event.shiftKey && anchorRef.current) {
      const startIndex = rows.findIndex((entry) => rowKey(entry) === anchorRef.current);
      if (startIndex >= 0 && index >= 0) {
        const start = Math.min(startIndex, index);
        const end = Math.max(startIndex, index);
        const range = rows.slice(start, end + 1).flatMap(rowIds);
        onSelect(modifier ? Array.from(new Set([...selected, ...range])) : range);
        return;
      }
    }
    const ids = rowIds(row);
    if (modifier) {
      const allSelected = ids.length > 0 && ids.every((id) => selectedSet.has(id));
      if (allSelected) {
        const remove = new Set(ids);
        onSelect(selected.filter((id) => !remove.has(id)));
      } else {
        onSelect(Array.from(new Set([...selected, ...ids])));
      }
    } else {
      onSelect(ids);
    }
    anchorRef.current = key;
  };

  const runSearch = async (page = 1) => {
    const text = query.trim();
    if (!text) return;
    const target = typeof page === "number" && page > 0 ? page : 1;
    setSearching(true);
    try {
      const data = await api.post<{ results: any[]; page?: number; total_pages?: number }>(
        "/api/items/search",
        {
          query: text,
          media_type: mediaFilter,
          page: target,
        },
      );
      setResults(data.results);
      setSearchPage(data.page || target);
      setSearchPages(data.total_pages || 1);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "搜索失败");
    } finally {
      setSearching(false);
    }
  };

  const chooseCandidate = async (candidate: any) => {
    if (!selectedItems.length) return;
    if (candidate.media_type !== "tv") {
      setChosen(null);
      setSeasons([]);
      setSeason(null);
      setEpisodes([]);
      if (selectedItems.length === 1) {
        await applySingle(candidate, selectedItems[0]);
      } else {
        setPending({ kind: "movie", candidate, items: selectedItems });
      }
      return;
    }
    setChosen(candidate);
    setSeasons([]);
    setSeason(null);
    setEpisodes([]);
    setPending(null);
    setDetailsLoading(true);
    try {
      const details = await api.get<any>(`/api/items/tmdb/tv/${candidate.tmdb_id}`);
      const list = Array.isArray(details.seasons) ? details.seasons : [];
      setSeasons(list);
      const preferred = selectedItems[0]?.parsed?.season;
      const fallback = list.find((entry: any) => entry.season_number === 1) || list.find((entry: any) => entry.season_number !== 0) || list[0];
      const initial = list.find((entry: any) => entry.season_number === preferred)?.season_number ?? fallback?.season_number ?? 1;
      setSeason(Number(initial));
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "获取剧集信息失败");
    } finally {
      setDetailsLoading(false);
    }
  };

  useEffect(() => {
    if (!chosen || chosen.media_type !== "tv" || season == null) {
      setEpisodes([]);
      return;
    }
    let alive = true;
    setEpisodesLoading(true);
    api
      .get<{ episodes: any[] }>(`/api/items/tmdb/tv/${chosen.tmdb_id}/season/${season}`)
      .then((data) => {
        if (alive) setEpisodes(data.episodes || []);
      })
      .catch(() => {
        if (alive) setEpisodes([]);
      })
      .finally(() => {
        if (alive) setEpisodesLoading(false);
      });
    return () => {
      alive = false;
    };
  }, [chosen, season]);

  const applySingle = async (candidate: any, item: MediaItem, seasonNumber?: number, episodeNumber?: number) => {
    if (item.status === "organized") {
      toast.error("已整理的文件不支持直接纠错，请先恢复为待确认");
      return;
    }
    setApplying(true);
    try {
      const body: Record<string, unknown> = {
        tmdb_id: candidate.tmdb_id,
        media_type: candidate.media_type,
      };
      if (candidate.media_type === "tv") {
        body.season = seasonNumber ?? 1;
        body.episode = episodeNumber ?? 1;
      }
      await api.post(`/api/items/${item.id}/match`, body);
      const suffix =
        candidate.media_type === "tv"
          ? ` S${pad2(body.season as number)}E${pad2(body.episode as number)}`
          : "";
      toast.success(`已更新为《${candidate.title}》${suffix}`);
      onChanged();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "修改识别结果失败");
    } finally {
      setApplying(false);
    }
  };

  const buildEpisodeEntries = (mode: "sequential" | "offset", anchor: number): EpisodeEntries => {
    const firstEpisode = selectedItems[0]?.parsed?.episode ?? 1;
    const delta = anchor - firstEpisode;
    return selectedItems.map((item, index) => {
      const oldEpisode = item.parsed?.episode ?? firstEpisode + index;
      return { item, episode: mode === "sequential" ? anchor + index : oldEpisode + delta };
    });
  };

  const pickEpisode = (entry: any) => {
    if (!chosen || season == null) return;
    const episodeNumber = Number(entry.episode_number);
    if (!Number.isFinite(episodeNumber)) return;
    if (selectedItems.length === 1) {
      void applySingle(chosen, selectedItems[0], season, episodeNumber);
      return;
    }
    const mode = pending?.kind === "episodes" ? pending.mode : "sequential";
    setPending({
      kind: "episodes",
      candidate: chosen,
      season,
      anchor: episodeNumber,
      mode,
      entries: buildEpisodeEntries(mode, episodeNumber),
    });
  };

  const changePendingMode = (mode: "sequential" | "offset") => {
    if (!pending || pending.kind !== "episodes") return;
    setPending({ ...pending, mode, entries: buildEpisodeEntries(mode, pending.anchor) });
  };

  const applyPending = async () => {
    if (!pending) return;
    const targets = pending.kind === "movie" ? pending.items : pending.entries.map((entry) => entry.item);
    if (targets.some((item) => item.status === "organized")) {
      toast.error("已整理的文件不支持直接纠错，请先恢复为待确认");
      return;
    }
    setApplying(true);
    try {
      if (pending.kind === "movie") {
        await api.post("/api/items/batch-match", {
          tmdb_id: pending.candidate.tmdb_id,
          media_type: "movie",
          items: pending.items.map((item) => ({ item_id: item.id })),
        });
        toast.success(`已把 ${pending.items.length} 个文件匹配为《${pending.candidate.title}》`);
      } else {
        await api.post("/api/items/batch-match", {
          tmdb_id: pending.candidate.tmdb_id,
          media_type: "tv",
          items: pending.entries.map(({ item, episode }) => ({
            item_id: item.id,
            season: pending.season,
            episode,
          })),
        });
        toast.success(`已更新 ${pending.entries.length} 个文件的季 / 集`);
      }
      setPending(null);
      onChanged();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "批量修改失败");
    } finally {
      setApplying(false);
    }
  };

  const clearChosen = () => {
    setChosen(null);
    setSeasons([]);
    setSeason(null);
    setEpisodes([]);
    setPending(null);
  };

  const singleItem = selectedItems.length === 1 ? selectedItems[0] : null;
  const totalSize = selectedItems.reduce((sum, item) => sum + (item.size || 0), 0);

  useEffect(() => {
    if (!singleItem) {
      setAttachments(null);
      setAttachmentsLoading(false);
      return;
    }
    let alive = true;
    setAttachments(null);
    setAttachmentsLoading(true);
    api
      .get<AttachmentPayload>(`/api/items/${singleItem.id}/attachments`)
      .then((data) => {
        if (alive) setAttachments(data);
      })
      .catch((err) => {
        if (alive) {
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
        if (alive) setAttachmentsLoading(false);
      });
    return () => {
      alive = false;
    };
  }, [singleItem?.id]);

  const linkAttachment = async (path: string, targetVideoPath?: string) => {
    if (!singleItem) return;
    try {
      const data = await api.post<AttachmentPayload>(`/api/items/${singleItem.id}/attachments/link`, {
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
    if (!singleItem) return;
    try {
      const data = await api.post<AttachmentPayload>(`/api/items/${singleItem.id}/attachments/unlink`, { path });
      setAttachments(data);
      toast.success("已取消手动关联");
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "取消关联失败");
    }
  };

  const relativePath = (item: MediaItem) => {
    const segments = relSegments(item.source_path, library.root_path);
    const parts = segments.length ? segments : [item.file_name];
    return parts.length > 1 ? parts.slice(0, -1).join(" / ") : "";
  };

  return (
    <Card className="overflow-hidden p-0">
      <div className="flex min-h-[560px] flex-col md:h-[calc(100vh-300px)] md:min-h-[560px] md:flex-row">
        {/* 左侧：文件结构 */}
        <div className="flex max-h-[340px] shrink-0 flex-col border-b border-white/[0.06] md:max-h-none md:w-[300px] md:border-b-0 md:border-r xl:w-[340px]">
          <div className="border-b border-white/[0.06] px-3.5 py-3">
            <div className="flex items-center justify-between gap-2">
              <div className="text-[12px] font-medium text-slate-200">待整理目录</div>
              <div className="text-[10px] text-slate-500">{items.length} 个文件</div>
            </div>
            <div className="mt-0.5 truncate font-mono text-[10px] text-slate-500" title={library.root_path}>
              {library.root_path || "/"}
            </div>
            <div className="mt-0.5 text-[10px] text-slate-600">点文件夹整组选中 · Shift 连选 · Ctrl / ⌘ 多选</div>
            <div className="mt-2 flex flex-wrap items-center gap-x-2 gap-y-1 text-[10px] text-slate-500">
              <button className="rounded px-1 py-0.5 transition hover:bg-white/[0.06] hover:text-slate-300" onClick={() => setExpanded(collectFolders(tree))}>
                展开全部
              </button>
              <button className="rounded px-1 py-0.5 transition hover:bg-white/[0.06] hover:text-slate-300" onClick={() => setExpanded(new Set())}>
                收起全部
              </button>
              <div className="ml-auto flex items-center gap-1">
                <span>排序</span>
                <Select
                  className="h-7 w-[86px] px-1.5 py-0 text-[10px]"
                  value={sortMode}
                  onChange={(event) => setSortMode(event.target.value as SortMode)}
                >
                  <option value="name">文件名</option>
                  <option value="title">识别结果</option>
                  <option value="status">状态</option>
                </Select>
              </div>
            </div>
            <div className="mt-1.5 flex items-center gap-1.5 text-[10px] text-slate-500">
              <span>显示</span>
              <div className="flex overflow-hidden rounded-md border border-white/10 bg-white/[0.02]">
                {NAME_MODES.map((mode) => (
                  <button
                    key={mode.value}
                    type="button"
                    title={mode.hint}
                    onClick={() => {
                      setNameMode(mode.value);
                      window.localStorage.setItem(NAME_MODE_KEY, mode.value);
                    }}
                    className={clsx(
                      "px-1.5 py-0.5 transition",
                      nameMode === mode.value
                        ? "bg-mint-400/15 text-mint-200"
                        : "text-slate-500 hover:bg-white/[0.06] hover:text-slate-300",
                    )}
                  >
                    {mode.label}
                  </button>
                ))}
              </div>
              <span className="ml-auto text-slate-600">
                {nameMode === "scroll" ? "可横向拖动" : nameMode === "wrap" ? "完整换行" : "悬停看全名"}
              </span>
            </div>
          </div>
          <div className="min-h-0 flex-1 overflow-y-auto py-1">
            {!items.length ? (
              <div className="px-4 py-10 text-center text-[11px] text-slate-500">没有文件，先扫描一次吧。</div>
            ) : (
              rows.map((row) => {
                if (row.kind === "folder") {
                  const open = expanded.has(row.node.path);
                  const stats = folderStats.get(row.node.path);
                  const total = stats?.total ?? row.node.fileCount;
                  const chosenCount = stats?.selected ?? 0;
                  const allChosen = total > 0 && chosenCount === total;
                  const partial = chosenCount > 0 && !allChosen;
                  return (
                    <div
                      key={`d:${row.node.path}`}
                      role="button"
                      tabIndex={0}
                      onClick={(event) => handleRowClick(event, row)}
                      onDoubleClick={() => toggleFolder(row.node.path)}
                      onKeyDown={(event) => {
                        if (event.key === "Enter" || event.key === " ") {
                          event.preventDefault();
                          onSelect(rowIds(row));
                          anchorRef.current = rowKey(row);
                        }
                      }}
                      className={clsx(
                        "group flex w-full cursor-pointer gap-1.5 py-1.5 pr-2 text-left text-[12px] transition",
                        nameMode === "wrap" ? "items-start" : "items-center",
                        allChosen
                          ? "bg-mint-400/10 text-mint-200"
                          : partial
                            ? "bg-mint-400/[0.05] text-slate-200"
                            : "text-slate-400 hover:bg-white/[0.04] hover:text-slate-200",
                      )}
                      style={{ paddingLeft: 8 + row.node.depth * 14 }}
                      title={`${row.node.path}（点击选中文件夹内全部文件，双击展开 / 收起）`}
                    >
                      <button
                        type="button"
                        onClick={(event) => {
                          event.stopPropagation();
                          toggleFolder(row.node.path);
                        }}
                        className={clsx(
                          "shrink-0 rounded p-0.5 text-slate-600 transition hover:bg-white/[0.08] hover:text-slate-300",
                          nameMode === "wrap" && "mt-0.5",
                        )}
                        title={open ? "收起" : "展开"}
                      >
                        {open ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
                      </button>
                      {open ? (
                        <FolderOpen
                          size={13}
                          className={clsx(
                            "shrink-0",
                            nameMode === "wrap" && "mt-0.5",
                            allChosen || partial ? "text-mint-300/80" : "text-sky-300/70",
                          )}
                        />
                      ) : (
                        <Folder
                          size={13}
                          className={clsx(
                            "shrink-0",
                            nameMode === "wrap" && "mt-0.5",
                            allChosen || partial ? "text-mint-300/80" : "text-sky-300/60",
                          )}
                        />
                      )}
                      <span className={clsx("min-w-0 flex-1", nameClass(nameMode))}>{row.node.name}</span>
                      {chosenCount > 0 ? (
                        <span
                          className={clsx(
                            "shrink-0 text-[10px]",
                            nameMode === "wrap" && "mt-0.5",
                            allChosen ? "text-mint-300" : "text-mint-400/80",
                          )}
                        >
                          {chosenCount}/{total}
                        </span>
                      ) : (
                        <span className={clsx("shrink-0 text-[10px] text-slate-600", nameMode === "wrap" && "mt-0.5")}>
                          {total}
                        </span>
                      )}
                    </div>
                  );
                }
                const item = row.node.item;
                const active = selectedSet.has(item.id);
                return (
                  <div
                    key={`f:${item.id}`}
                    role="button"
                    tabIndex={0}
                    onClick={(event) => handleRowClick(event, row)}
                    onDoubleClick={() => onOpenItem(item)}
                    onKeyDown={(event) => {
                      if (event.key === "Enter" || event.key === " ") {
                        event.preventDefault();
                        onOpenItem(item);
                      }
                    }}
                    className={clsx(
                      "group flex w-full cursor-pointer gap-1.5 py-1.5 pr-2 text-left text-[12px] transition",
                      nameMode === "wrap" ? "items-start" : "items-center",
                      active ? "bg-mint-400/10 text-mint-200" : "text-slate-300 hover:bg-white/[0.04]",
                    )}
                    style={{ paddingLeft: 26 + row.node.depth * 14 }}
                    title={item.file_name}
                  >
                    <FileVideo
                      size={13}
                      className={clsx("shrink-0", nameMode === "wrap" && "mt-0.5", active ? "text-mint-300" : "text-slate-600")}
                    />
                    <span className="min-w-0 flex-1">
                      <span className={clsx("block", nameClass(nameMode))}>{row.node.name}</span>
                      {item.match && item.planned_name ? (
                        <span className={clsx("mt-0.5 block truncate text-[9px] text-slate-500", nameMode === "wrap" && "whitespace-normal")}
                          title={`整理后：${item.planned_name}`}
                        >
                          整理后：{item.planned_name}
                        </span>
                      ) : null}
                    </span>
                    {item.match ? (
                      <button
                        type="button"
                        className={clsx(
                          "shrink-0 rounded p-1 text-slate-600 opacity-70 transition hover:bg-white/[0.08] hover:text-mint-300 group-hover:opacity-100",
                          nameMode === "wrap" && "mt-0.5",
                        )}
                        title="修改整理后名称"
                        onClick={(event) => {
                          event.stopPropagation();
                          onEditTarget(item);
                        }}
                      >
                        <PencilLine size={11} />
                      </button>
                    ) : null}
                    <span className={clsx("shrink-0 font-mono text-[10px] text-slate-500", nameMode === "wrap" && "mt-0.5")}>
                      {seasonEpisode(item.parsed?.season, item.parsed?.episode)}
                    </span>
                    <span
                      className={clsx(
                        "h-1.5 w-1.5 shrink-0 rounded-full",
                        nameMode === "wrap" && "mt-1",
                        statusDot(item.status),
                      )}
                    />
                  </div>
                );
              })
            )}
          </div>
        </div>

        {/* 右侧：识别与纠错 */}
        <div className="min-h-0 flex-1 overflow-y-auto">
          {!selectedItems.length ? (
            <div className="flex h-full min-h-[420px] flex-col items-center justify-center gap-3 px-8 text-center">
              <Search size={26} className="text-slate-600" />
              <div className="text-sm text-slate-300">在左侧选择文件或文件夹</div>
              <div className="max-w-md text-[11px] leading-relaxed text-slate-500">
                右侧会显示当前识别结果。点文件夹可整组选中，按住 Shift 点击可连续选择，按住 Ctrl / ⌘ 点击可多选。
              </div>
            </div>
          ) : (
            <div className="space-y-5 p-4 lg:p-5">
              {singleItem ? (
                <div className="space-y-3">
                  <div className="flex items-start gap-3">
                    <div className="rounded-xl border border-white/[0.06] bg-white/[0.03] p-2 text-slate-400">
                      <FileVideo size={18} />
                    </div>
                    <div className="min-w-0 flex-1">
                      <div className="break-all text-[13px] font-medium text-slate-100">{singleItem.file_name}</div>
                      <div className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-1 text-[11px] text-slate-500">
                        <StatusBadge status={singleItem.status} />
                        <span>{formatSize(singleItem.size)}</span>
                        <span>{seasonEpisode(singleItem.parsed?.season, singleItem.parsed?.episode) || "正片"}</span>
                        {singleItem.parsed?.resolution ? <span>{singleItem.parsed.resolution}</span> : null}
                        {singleItem.parsed?.video ? <span>{singleItem.parsed.video}</span> : null}
                      </div>
                      {singleItem.match && singleItem.planned_name ? (
                        <div className="mt-1 flex min-w-0 items-center gap-1 text-[10px] text-slate-500">
                          <span className="shrink-0">整理后</span>
                          <span className="truncate text-mint-300/80" title={singleItem.planned_name}>
                            {singleItem.planned_name}
                          </span>
                          <button
                            type="button"
                            className="shrink-0 rounded p-0.5 text-slate-600 transition hover:bg-white/[0.08] hover:text-mint-300"
                            title="修改整理后名称"
                            onClick={() => onEditTarget(singleItem)}
                          >
                            <PencilLine size={10} />
                          </button>
                        </div>
                      ) : null}
                      {relativePath(singleItem) ? (
                        <div className="mt-1 break-all font-mono text-[10px] text-slate-600">{relativePath(singleItem)}</div>
                      ) : null}
                      {singleItem.error ? (
                        <div className="mt-1 text-[10px] text-rose-300">{singleItem.error}</div>
                      ) : null}
                    </div>
                  </div>

                  {singleItem.match ? (
                    <div className="flex gap-3 rounded-xl border border-white/[0.06] bg-white/[0.02] p-3">
                      <Poster
                        url={singleItem.match.poster_url}
                        title={singleItem.match.title}
                        rounded="rounded-lg"
                        className="h-36 w-24 shrink-0"
                      />
                      <div className="min-w-0 flex-1">
                        <div className="flex flex-wrap items-center gap-1.5">
                          <Badge tone={singleItem.match.media_type === "tv" ? "sky" : "mint"}>
                            {singleItem.match.media_type === "tv" ? <Tv size={11} /> : <Film size={11} />}
                            {singleItem.match.media_type === "tv" ? "剧集" : "电影"}
                          </Badge>
                          {singleItem.match.vote_average ? (
                            <Badge tone="amber">★ {singleItem.match.vote_average.toFixed(1)}</Badge>
                          ) : null}
                          <span className="text-[10px] text-slate-500">
                            {singleItem.manual
                              ? "手动"
                              : singleItem.match.confidence != null
                                ? `${Math.round(singleItem.match.confidence * 100)}%`
                                : ""}
                          </span>
                        </div>
                        <div className="mt-1 text-[15px] font-semibold text-white">
                          {singleItem.match.title}
                          {singleItem.match.year ? (
                            <span className="ml-1.5 text-xs font-normal text-slate-500">({singleItem.match.year})</span>
                          ) : null}
                        </div>
                        {singleItem.match.original_title && singleItem.match.original_title !== singleItem.match.title ? (
                          <div className="text-[11px] text-slate-500">{singleItem.match.original_title}</div>
                        ) : null}
                        <p className="mt-1.5 line-clamp-3 text-[11px] leading-relaxed text-slate-500">
                          {singleItem.match.overview || "暂无简介"}
                        </p>
                        <div className="mt-2 flex flex-wrap gap-1.5">
                          <Button className="px-2 py-1 text-[11px]" onClick={() => onOpenItem(singleItem)}>
                            <Eye size={12} /> 详情
                          </Button>
                          {singleItem.status !== "organized" ? (
                            <>
                              <Button className="px-2 py-1 text-[11px]" onClick={() => onPreview([singleItem.id])}>
                                <Wand2 size={12} /> 整理预览
                              </Button>
                              <Button className="px-2 py-1 text-[11px]" onClick={() => onRename([singleItem.id])}>
                                <TextCursorInput size={12} /> 仅重命名
                              </Button>
                            </>
                          ) : null}
                        </div>
                      </div>
                    </div>
                  ) : (
                    <div className="rounded-xl border border-dashed border-white/10 px-3.5 py-4 text-[11px] leading-relaxed text-slate-500">
                      还没有匹配结果。文件名识别为
                      <span className="mx-1 text-slate-300">
                        《{singleItem.parsed?.title || "未知"}》
                        {singleItem.parsed?.year ? ` (${singleItem.parsed.year})` : ""}
                      </span>
                      ，可以在下方搜索正确的作品。
                    </div>
                  )}
                  <AttachmentPanel
                    data={attachments}
                    loading={attachmentsLoading}
                    compact
                    onLink={linkAttachment}
                    onUnlink={unlinkAttachment}
                  />
                </div>
              ) : (
                <div className="space-y-2.5">
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <div className="text-[13px] text-slate-200">
                      已选 <span className="text-mint-300">{selectedItems.length}</span> 个文件
                      <span className="ml-2 text-[11px] text-slate-500">{formatSize(totalSize)}</span>
                    </div>
                    <div className="text-[10px] text-slate-500">选择剧集后会从点选的集数开始按顺序分配</div>
                  </div>
                  <div className="max-h-48 overflow-y-auto rounded-xl border border-white/[0.06]">
                    {selectedItems.map((item) => (
                      <div
                        key={item.id}
                        className="flex items-center gap-2 border-b border-white/[0.04] px-3 py-1.5 text-[11px] last:border-0"
                      >
                        <span className={clsx("h-1.5 w-1.5 shrink-0 rounded-full", statusDot(item.status))} />
                        <span className="w-14 shrink-0 font-mono text-slate-500">
                          {seasonEpisode(item.parsed?.season, item.parsed?.episode) || "—"}
                        </span>
                        <span className="min-w-0 flex-1 truncate text-slate-300" title={item.file_name}>
                          {item.file_name}
                        </span>
                        <span className="max-w-[180px] shrink-0 truncate text-[10px] text-slate-500">
                          {item.match?.title || "未匹配"}
                        </span>
                      </div>
                    ))}
                  </div>
                  <div className="text-[10px] leading-relaxed text-slate-500">
                    字幕、外挂音轨、图片和 NFO 会随对应视频一起整理；选中单个文件可查看具体附带内容。
                  </div>
                </div>
              )}

              {/* 搜索与纠错 */}
              <div className="space-y-3 border-t border-white/[0.06] pt-4">
                <div className="flex items-center justify-between gap-2">
                  <div className="text-[12px] font-medium text-slate-200">纠错 / 重新匹配</div>
                  {chosen ? (
                    <button className="text-[10px] text-slate-500 transition hover:text-slate-300" onClick={clearChosen}>
                      重新选择作品
                    </button>
                  ) : null}
                </div>

                <div className="flex flex-wrap gap-2">
                  <Input
                    className="h-9 min-w-[200px] flex-1 text-[12px]"
                    value={query}
                    placeholder="搜索正确的电影 / 剧集名称，支持中英文"
                    onChange={(event) => setQuery(event.target.value)}
                    onKeyDown={(event) => {
                      if (event.key === "Enter") void runSearch();
                    }}
                  />
                  <Select
                    className="h-9 w-24 text-[11px]"
                    value={mediaFilter}
                    onChange={(event) => setMediaFilter(event.target.value as "" | "movie" | "tv")}
                  >
                    <option value="">全部</option>
                    <option value="movie">电影</option>
                    <option value="tv">剧集</option>
                  </Select>
                  <Button className="h-9 px-3 text-[11px]" loading={searching} onClick={() => void runSearch()}>
                    <Search size={13} /> 搜索
                  </Button>
                </div>

                {!chosen && results !== null ? (
                  results.length ? (
                    <>
                      <div className="grid gap-2 sm:grid-cols-2">
                        {results.map((candidate: any) => (
                        <button
                          key={`${candidate.media_type}-${candidate.tmdb_id}`}
                          type="button"
                          onClick={() => void chooseCandidate(candidate)}
                          className="flex gap-2.5 rounded-xl border border-white/[0.06] bg-white/[0.02] p-2.5 text-left transition hover:border-mint-400/30 hover:bg-mint-400/[0.05]"
                        >
                          <Poster
                            url={candidate.poster_url}
                            title={candidate.title}
                            rounded="rounded-md"
                            className="h-20 w-14 shrink-0"
                          />
                          <div className="min-w-0 flex-1">
                            <div className="flex flex-wrap items-center gap-1.5">
                              <span className="truncate text-[12px] text-slate-100">{candidate.title}</span>
                              <Badge tone={candidate.media_type === "tv" ? "sky" : "mint"}>
                                {candidate.media_type === "tv" ? "剧集" : "电影"}
                              </Badge>
                              {candidate.year ? <span className="text-[10px] text-slate-500">{candidate.year}</span> : null}
                            </div>
                            {candidate.original_title && candidate.original_title !== candidate.title ? (
                              <div className="truncate text-[10px] text-slate-500">{candidate.original_title}</div>
                            ) : null}
                            <div className="mt-1 line-clamp-2 text-[10px] leading-relaxed text-slate-500">
                              {candidate.overview || "暂无简介"}
                            </div>
                          </div>
                        </button>
                      ))}
                      </div>
                      {searchPages > 1 ? (
                        <div className="flex items-center justify-between pt-1 text-[11px] text-slate-400">
                          <Button
                            className="px-2.5 py-1.5 text-[11px]"
                            disabled={searching || searchPage <= 1}
                            onClick={() => void runSearch(searchPage - 1)}
                          >
                            上一页
                          </Button>
                          <span>
                            第 {searchPage} / {searchPages} 页
                          </span>
                          <Button
                            className="px-2.5 py-1.5 text-[11px]"
                            disabled={searching || searchPage >= searchPages}
                            onClick={() => void runSearch(searchPage + 1)}
                          >
                            下一页
                          </Button>
                        </div>
                      ) : null}
                    </>
                  ) : (
                    <div className="text-[11px] text-slate-500">没有找到结果，换个关键词或切换类型再试。</div>
                  )
                ) : null}

                {chosen ? (
                  <div className="space-y-3 rounded-xl border border-mint-500/30 bg-mint-500/[0.05] p-3">
                    <div className="flex items-center gap-3">
                      <Poster url={chosen.poster_url} title={chosen.title} rounded="rounded-lg" className="h-16 w-11 shrink-0" />
                      <div className="min-w-0 flex-1">
                        <div className="flex flex-wrap items-center gap-1.5">
                          <span className="truncate text-[13px] text-slate-100">{chosen.title}</span>
                          <Badge tone="sky">剧集</Badge>
                          {chosen.year ? <span className="text-[10px] text-slate-500">{chosen.year}</span> : null}
                        </div>
                        <div className="mt-0.5 line-clamp-1 text-[10px] text-slate-500">
                          {chosen.original_title && chosen.original_title !== chosen.title ? `${chosen.original_title} · ` : ""}
                          {chosen.overview || "暂无简介"}
                        </div>
                      </div>
                    </div>

                    {detailsLoading ? (
                      <div className="flex items-center gap-2 text-[11px] text-slate-500">
                        <Spinner size={13} /> 正在读取季列表…
                      </div>
                    ) : (
                      <>
                        <div className="flex flex-wrap gap-1.5">
                          {(seasons.length ? seasons : season != null ? [{ season_number: season }] : []).map((entry: any) => (
                            <button
                              key={entry.season_number}
                              type="button"
                              onClick={() => setSeason(Number(entry.season_number))}
                              className={clsx(
                                "rounded-lg border px-2 py-1 text-[11px] transition",
                                season === entry.season_number
                                  ? "border-mint-400/40 bg-mint-400/10 text-mint-200"
                                  : "border-white/[0.08] text-slate-400 hover:text-slate-200",
                              )}
                            >
                              {entry.season_number === 0 ? "特别篇" : `第 ${entry.season_number} 季`}
                              {entry.episode_count ? <span className="ml-1 text-[9px] text-slate-500">{entry.episode_count} 集</span> : null}
                            </button>
                          ))}
                        </div>

                        {episodesLoading ? (
                          <div className="flex items-center gap-2 text-[11px] text-slate-500">
                            <Loader2 size={13} className="animate-spin" /> 正在读取集列表…
                          </div>
                        ) : (
                          <div className="grid max-h-64 grid-cols-2 gap-1.5 overflow-y-auto pr-1 sm:grid-cols-3">
                            {episodes.map((entry: any) => {
                              const active =
                                pending?.kind === "episodes" &&
                                pending.season === season &&
                                pending.entries.some((row) => row.episode === Number(entry.episode_number));
                              return (
                                <button
                                  key={entry.episode_number}
                                  type="button"
                                  disabled={applying}
                                  onClick={() => pickEpisode(entry)}
                                  className={clsx(
                                    "flex flex-col items-start gap-0.5 rounded-lg border px-2 py-1.5 text-left transition disabled:opacity-50",
                                    active
                                      ? "border-mint-400/50 bg-mint-400/10"
                                      : "border-white/[0.07] bg-white/[0.02] hover:border-mint-400/30 hover:bg-mint-400/[0.06]",
                                  )}
                                >
                                  <span className="font-mono text-[10px] text-mint-300">E{pad2(Number(entry.episode_number))}</span>
                                  <span className="line-clamp-1 text-[10px] text-slate-400">{entry.name || "—"}</span>
                                </button>
                              );
                            })}
                            {!episodes.length ? (
                              <div className="col-span-full py-2 text-[11px] text-slate-500">这一季没有读取到集信息。</div>
                            ) : null}
                          </div>
                        )}
                        {selectedItems.length === 1 ? (
                          <div className="text-[10px] text-slate-500">点击集数即可直接修改这个文件的识别结果。</div>
                        ) : (
                          <div className="text-[10px] text-slate-500">
                            点击集数后，会从该集开始依次分配给已选的 {selectedItems.length} 个文件。
                          </div>
                        )}
                      </>
                    )}
                  </div>
                ) : null}

                {pending ? (
                  <div className="space-y-2 rounded-xl border border-sky-500/30 bg-sky-500/[0.06] px-3 py-2.5">
                    {pending.kind === "movie" ? (
                      <div className="text-[11px] text-slate-300">
                        将 {pending.items.length} 个文件匹配为《{pending.candidate.title}》
                      </div>
                    ) : (
                      <>
                        <div className="flex flex-wrap items-center gap-2 text-[11px] text-slate-300">
                          <span>
                            {pending.mode === "sequential" ? "将依次分配" : "将按偏移分配"}：
                            <span className="ml-1 font-mono text-mint-200">
                              {pendingEntriesContiguous(pending)
                                ? `S${pad2(pending.season)}E${pad2(pending.entries[0]?.episode ?? 1)}–E${pad2(
                                    pending.entries[pending.entries.length - 1]?.episode ?? 1,
                                  )}`
                                : `第 ${pending.season} 季 · 共 ${pending.entries.length} 集`}
                            </span>
                            <span className="ml-1 text-slate-500">（{pending.entries.length} 个文件）</span>
                          </span>
                          <div className="ml-auto flex items-center gap-1">
                            {(
                              [
                                ["sequential", "依次排列"],
                                ["offset", "按偏移"],
                              ] as const
                            ).map(([value, label]) => (
                              <button
                                key={value}
                                type="button"
                                onClick={() => changePendingMode(value)}
                                className={clsx(
                                  "rounded border px-1.5 py-0.5 text-[10px] transition",
                                  pending.mode === value
                                    ? "border-sky-400/40 bg-sky-400/10 text-sky-200"
                                    : "border-white/10 text-slate-400 hover:text-slate-200",
                                )}
                              >
                                {label}
                              </button>
                            ))}
                          </div>
                        </div>
                        <div className="max-h-24 space-y-0.5 overflow-y-auto font-mono text-[10px] text-slate-500">
                          {pending.entries.map((row) => (
                            <div key={row.item.id} className="flex gap-2">
                              <span className="w-16 shrink-0 text-mint-300">
                                S{pad2(pending.season)}E{pad2(row.episode)}
                              </span>
                              <span className="truncate">{row.item.file_name}</span>
                            </div>
                          ))}
                        </div>
                      </>
                    )}
                    {pending.kind === "movie" ? (
                      <div className="text-[10px] text-slate-500">
                        {pending.items
                          .slice(0, 5)
                          .map((item) => item.file_name)
                          .join("、")}
                        {pending.items.length > 5 ? ` 等 ${pending.items.length} 个文件` : ""}
                      </div>
                    ) : null}
                    <div className="flex justify-end gap-1.5">
                      <Button className="px-2 py-1 text-[11px]" onClick={() => setPending(null)}>
                        取消
                      </Button>
                      <Button variant="primary" className="px-2.5 py-1 text-[11px]" loading={applying} onClick={applyPending}>
                        应用
                      </Button>
                    </div>
                  </div>
                ) : null}

                {!chosen && results === null ? (
                  <div className="text-[10px] leading-relaxed text-slate-500">
                    搜索后点选作品卡片：电影直接应用，剧集会展开季 / 集，再点选集数即可修改识别结果。
                  </div>
                ) : null}
              </div>
            </div>
          )}
        </div>
      </div>
    </Card>
  );
}

function statusDot(status: string): string {
  switch (status) {
    case "matched":
      return "bg-mint-400";
    case "review":
      return "bg-amber-400";
    case "unmatched":
    case "failed":
      return "bg-rose-400";
    case "organized":
      return "bg-sky-400";
    case "ignored":
      return "bg-slate-600";
    default:
      return "bg-slate-500";
  }
}
