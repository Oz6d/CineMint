import { useEffect, useState } from "react";
import {
  AlertTriangle,
  Check,
  ChevronRight,
  FolderOpen,
  FolderPlus,
} from "lucide-react";
import { api } from "../lib/api";
import { useAsync } from "../lib/hooks";
import { useToast } from "../lib/toast";
import type { CloudTree } from "../types";
import { Button, Input, Modal, Spinner } from "./ui";

interface FolderNode {
  fid: string;
  path: string;
  name: string;
  driveRoot?: boolean;
}

interface CloudFolderPickerProps {
  open: boolean;
  kind: "quark" | "p115";
  libraryId: number;
  root?: string;
  initialFid?: string;
  initialPath?: string;
  onClose: () => void;
  onSelect: (value: { fid: string; path: string; libraryPath: string }) => void;
}

const KIND_LABEL: Record<"quark" | "p115", string> = {
  quark: "夸克网盘",
  p115: "115 网盘",
};

function trimPath(value: string): string {
  return (value || "").replace(/^\/+|\/+$/g, "");
}

export default function CloudFolderPicker({
  open,
  kind,
  libraryId,
  root = "",
  initialFid = "",
  initialPath = "",
  onClose,
  onSelect,
}: CloudFolderPickerProps) {
  const toast = useToast();
  const [trail, setTrail] = useState<FolderNode[]>([{ fid: "", path: "", name: "" }]);
  const [newFolder, setNewFolder] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!open) return;
    const path = trimPath(initialPath);
    setTrail([
      {
        fid: path ? initialFid : "",
        path,
        name: path ? path.split("/").pop() || "" : "",
      },
    ]);
    setNewFolder("");
  }, [open, kind, libraryId, root, initialFid, initialPath]);

  const node = trail[trail.length - 1];
  const query =
    open && libraryId
      ? `/api/subscriptions/${kind}/tree?library_id=${libraryId}&fid=${encodeURIComponent(
          node.fid,
        )}&path=${encodeURIComponent(node.path)}&cloud_root=${encodeURIComponent(root)}${
          node.driveRoot ? "&from_root=true" : ""
        }`
      : null;
  const { data, loading, error } = useAsync<CloudTree>(query, [
    open,
    libraryId,
    kind,
    root,
    node.fid,
    node.path,
  ]);

  const currentPath = data?.path ?? node.path;
  const parts = currentPath ? currentPath.split("/") : [];
  const canUp = trail.length > 1 || Boolean(currentPath);
  const inside = Boolean(data?.inside_library);

  const goUp = () => {
    if (trail.length > 1) {
      setTrail(trail.slice(0, -1));
      return;
    }
    if (!currentPath) {
      if (!node.driveRoot) {
        setTrail([{ fid: "", path: "", name: "", driveRoot: true }]);
      }
      return;
    }
    const parent = parts.slice(0, -1).join("/");
    if (!parent && !node.driveRoot) {
      setTrail([{ fid: "", path: "", name: "", driveRoot: true }]);
    } else {
      setTrail([{ fid: "", path: parent, name: "" }]);
    }
  };

  const jumpTo = (path: string, name = "") => {
    setTrail([{ fid: "", path, name }]);
  };

  const jumpToDriveRoot = () => {
    setTrail([{ fid: "", path: "", name: "", driveRoot: true }]);
  };

  const openEntry = (entry: CloudTree["entries"][number]) => {
    setTrail([...trail, { fid: entry.fid, path: entry.path, name: entry.name }]);
  };

  const createFolder = async () => {
    const name = newFolder.trim();
    if (!libraryId) {
      toast.error("请先选择影视库");
      return;
    }
    if (!name) {
      toast.error("请填写新目录名称");
      return;
    }
    setBusy(true);
    try {
      const created = await api.post<{ name: string; fid: string; path: string }>(
        `/api/subscriptions/${kind}/tree`,
        {
          library_id: libraryId,
          fid: data?.fid || node.fid,
          path: data?.path ?? node.path,
          name,
          cloud_root: root,
        },
      );
      toast.success("目录已创建");
      setNewFolder("");
      setTrail([...trail, { fid: created.fid, path: created.path, name: created.name }]);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "创建目录失败");
    } finally {
      setBusy(false);
    }
  };

  const select = () => {
    if (!data || !data.inside_library) return;
    onSelect({
      fid: data.fid,
      path: data.path,
      libraryPath: data.library_path ?? "",
    });
  };

  return (
    <Modal
      open={open}
      onClose={onClose}
      title={`选择${KIND_LABEL[kind]}目录`}
      width="max-w-2xl"
      top={
        <div className="flex flex-wrap items-center gap-1.5 rounded-xl border border-white/[0.07] bg-ink-950/40 px-2.5 py-2 text-[11px] text-slate-400">
          <button
            type="button"
            onClick={jumpToDriveRoot}
            className="rounded px-1 py-0.5 transition hover:bg-white/[0.06] hover:text-slate-200"
          >
            网盘根目录
          </button>
          {parts.map((part, index) => (
            <span key={`${part}-${index}`} className="flex items-center gap-1.5">
              <ChevronRight size={11} className="text-slate-600" />
              <button
                type="button"
                onClick={() => jumpTo(parts.slice(0, index + 1).join("/"), part)}
                className="rounded px-1 py-0.5 transition hover:bg-white/[0.06] hover:text-slate-200"
              >
                {part}
              </button>
            </span>
          ))}
          <Button
            type="button"
            className="ml-auto px-2 py-1 text-[10px]"
            onClick={goUp}
            disabled={!canUp}
          >
            上一级
          </Button>
        </div>
      }
      footer={
        <>
          <Button
            type="button"
            variant="primary"
            onClick={select}
            disabled={!data || !inside}
          >
            <Check size={14} /> 使用当前目录
          </Button>
        </>
      }
    >
      <div className="space-y-3">
        {data && !inside ? (
          <div className="flex items-start gap-2 rounded-xl border border-amber-500/25 bg-amber-500/[0.06] px-3 py-2.5 text-[11px] leading-relaxed text-amber-200">
            <AlertTriangle size={14} className="mt-0.5 shrink-0" />
            <span>
              当前目录不在影视库内。请选择影视库根目录
              {data.cloud_root ? `（${data.cloud_root}）` : ""} 或其子目录，否则无法扫描整理。
            </span>
          </div>
        ) : (
          <div className="text-[11px] text-slate-500">
            {data?.library_path
              ? `影视库内路径：${data.library_path}`
              : data
                ? "当前目录就是影视库根目录"
                : "读取网盘目录中…"}
          </div>
        )}

        <div className="max-h-[42vh] min-h-[180px] space-y-0.5 overflow-y-auto rounded-xl border border-white/[0.06] bg-ink-950/40 p-1.5">
          {loading && !data ? (
            <div className="flex items-center justify-center gap-2 py-10 text-[11px] text-slate-500">
              <Spinner size={13} /> 读取目录…
            </div>
          ) : error ? (
            <div className="px-2 py-8 text-center text-[11px] text-rose-300">{error}</div>
          ) : data?.entries.length ? (
            data.entries.map((entry) => (
              <button
                key={entry.fid}
                type="button"
                onClick={() => openEntry(entry)}
                className="flex w-full items-center gap-2 rounded-md px-2.5 py-2 text-left text-xs text-slate-300 transition hover:bg-white/[0.05]"
              >
                <FolderOpen size={13} className="text-slate-500" />
                <span className="min-w-0 flex-1 truncate">{entry.name}</span>
                <ChevronRight size={12} className="text-slate-600" />
              </button>
            ))
          ) : (
            <div className="px-2 py-8 text-center text-[11px] text-slate-600">
              没有子目录
            </div>
          )}
        </div>

        <div className="flex gap-1.5">
          <Input
            className="py-1.5 text-[11px]"
            value={newFolder}
            onChange={(event) => setNewFolder(event.target.value)}
            placeholder="在当前目录新建子目录"
            onKeyDown={(event) => {
              if (event.key === "Enter") {
                event.preventDefault();
                void createFolder();
              }
            }}
          />
          <Button
            type="button"
            className="shrink-0 px-2.5 py-1.5 text-[10px]"
            loading={busy}
            onClick={createFolder}
            disabled={!libraryId}
          >
            <FolderPlus size={12} /> 新建
          </Button>
        </div>
        <p className="text-[10px] leading-relaxed text-slate-500">
          这里浏览的是网盘真实目录；选择后订阅会直接转存到该目录，并从对应的 OpenList
          路径扫描整理。
        </p>
      </div>
    </Modal>
  );
}
