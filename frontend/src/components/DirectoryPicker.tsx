import { useEffect, useState } from "react";
import clsx from "clsx";
import { ArrowUp, Check, ChevronRight, Folder, FolderPlus } from "lucide-react";
import { api } from "../lib/api";
import { Button, Input, Modal, Spinner } from "./ui";

interface BrowseEntry {
  name: string;
  path: string;
  is_dir: boolean;
}

interface BrowseResult {
  path: string;
  parent: string;
  entries: BrowseEntry[];
}

/**
 * 目录选择器：让用户直接在文件源里点选文件夹，而不是手打路径。
 * 支持逐级进入、面包屑跳转、上一级，以及在当前位置新建文件夹。
 */
export function DirectoryPicker({
  sourceId,
  title,
  initialPath = "",
  onClose,
  onSelect,
}: {
  sourceId: number;
  title: string;
  initialPath?: string;
  onClose: () => void;
  onSelect: (path: string) => void;
}) {
  const [path, setPath] = useState(initialPath);
  const [listing, setListing] = useState<BrowseResult | null>(null);
  const [busy, setBusy] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const [newName, setNewName] = useState("");
  const [createBusy, setCreateBusy] = useState(false);
  const [createError, setCreateError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setBusy(true);
    setError(null);
    api
      .get<BrowseResult>(`/api/sources/${sourceId}/browse?path=${encodeURIComponent(path)}`)
      .then((result) => {
        if (!cancelled) setListing(result);
      })
      .catch((err) => {
        if (!cancelled) {
          setListing(null);
          setError(err instanceof Error ? err.message : String(err));
        }
      })
      .finally(() => {
        if (!cancelled) setBusy(false);
      });
    return () => {
      cancelled = true;
    };
  }, [sourceId, path]);

  const segments = path ? path.split("/").filter(Boolean) : [];
  const parentPath = segments.length > 0 ? segments.slice(0, -1).join("/") : "";

  const createFolder = async () => {
    const name = newName.trim().replace(/^\/+|\/+$/g, "");
    setCreateError(null);
    if (!name) return;
    if (name.includes("/")) {
      setCreateError("文件夹名称不能包含 /");
      return;
    }
    const target = path ? `${path}/${name}` : name;
    setCreateBusy(true);
    try {
      await api.post(`/api/sources/${sourceId}/mkdir`, { path: target });
      setCreating(false);
      setNewName("");
      setPath(target);
      setCreateError(null);
    } catch (err) {
      setCreateError(err instanceof Error ? err.message : "创建失败");
    } finally {
      setCreateBusy(false);
    }
  };

  return (
    <Modal
      open
      onClose={onClose}
      title={title}
      width="max-w-lg"
      footer={
        <>
          <Button onClick={onClose}>取消</Button>
          <Button
            variant="primary"
            onClick={() => {
              onSelect(path);
              onClose();
            }}
          >
            <Check size={14} /> 选择此目录
          </Button>
        </>
      }
    >
      <div className="space-y-3">
        <div className="flex items-center gap-2">
          <Button
            className="shrink-0 px-2.5 py-2"
            title="上一级"
            disabled={!path}
            onClick={() => setPath(parentPath)}
          >
            <ArrowUp size={14} />
          </Button>
          <div className="flex min-h-[38px] flex-1 flex-wrap items-center gap-0.5 rounded-xl border border-white/[0.08] bg-white/[0.02] px-2.5 py-1.5 font-mono text-xs">
            <button
              type="button"
              className="rounded px-1 py-0.5 text-slate-400 transition hover:bg-white/10 hover:text-mint-300"
              onClick={() => setPath("")}
            >
              根目录
            </button>
            {segments.map((segment, index) => {
              const target = segments.slice(0, index + 1).join("/");
              const isLast = index === segments.length - 1;
              return (
                <span key={target} className="flex items-center">
                  <ChevronRight size={11} className="text-slate-600" />
                  <button
                    type="button"
                    className={clsx(
                      "max-w-[140px] truncate rounded px-1 py-0.5 transition hover:bg-white/10 hover:text-mint-300",
                      isLast ? "text-mint-300" : "text-slate-400",
                    )}
                    onClick={() => setPath(target)}
                  >
                    {segment}
                  </button>
                </span>
              );
            })}
          </div>
        </div>

        <div
          className={clsx(
            "max-h-64 overflow-y-auto rounded-xl border border-white/[0.06] bg-white/[0.01] p-1",
            busy && listing ? "pointer-events-none opacity-50" : "",
          )}
        >
          {busy && !listing ? (
            <div className="flex items-center justify-center gap-2 py-10 text-xs text-slate-500">
              <Spinner /> 读取目录…
            </div>
          ) : error ? (
            <div className="px-3 py-8 text-center text-xs leading-relaxed text-rose-300">{error}</div>
          ) : (listing?.entries.length ?? 0) === 0 ? (
            <div className="px-3 py-8 text-center text-xs text-slate-500">
              此目录下没有子文件夹，可直接「选择此目录」或先新建文件夹
            </div>
          ) : (
            listing?.entries.map((entry) => (
              <button
                key={entry.path}
                type="button"
                className="group flex w-full items-center gap-2.5 rounded-lg px-3 py-2 text-left text-sm text-slate-200 transition hover:bg-white/[0.06]"
                onClick={() => setPath(entry.path)}
              >
                <Folder size={15} className="shrink-0 text-mint-400/80" />
                <span className="truncate">{entry.name}</span>
                <ChevronRight size={14} className="ml-auto shrink-0 text-slate-600 transition group-hover:text-slate-300" />
              </button>
            ))
          )}
        </div>

        {creating ? (
          <div className="space-y-1.5">
            <div className="flex gap-2">
              <Input
                autoFocus
                value={newName}
                placeholder="新文件夹名称"
                onChange={(event) => {
                  setNewName(event.target.value);
                  setCreateError(null);
                }}
                onKeyDown={(event) => {
                  if (event.key === "Enter") void createFolder();
                }}
              />
              <Button className="shrink-0" loading={createBusy} onClick={() => void createFolder()}>
                <FolderPlus size={14} /> 创建
              </Button>
              <Button
                className="shrink-0"
                onClick={() => {
                  setCreating(false);
                  setNewName("");
                  setCreateError(null);
                }}
              >
                取消
              </Button>
            </div>
            {createError ? (
              <p className="text-[11px] text-rose-300">{createError}</p>
            ) : (
              <p className="text-[11px] text-slate-500">
                将在当前位置（<span className="font-mono">{path ? `/${path}` : "根目录"}</span>）下创建，创建后自动进入
              </p>
            )}
          </div>
        ) : (
          <button
            type="button"
            className="flex w-full items-center justify-center gap-2 rounded-xl border border-dashed border-white/15 px-3 py-2.5 text-xs text-slate-400 transition hover:border-mint-500/40 hover:text-mint-300"
            onClick={() => setCreating(true)}
          >
            <FolderPlus size={14} /> 新建文件夹
          </button>
        )}
      </div>
    </Modal>
  );
}
