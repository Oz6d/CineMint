import { useEffect, useMemo, useRef, useState } from "react";
import {
  AlertTriangle,
  Check,
  ChevronRight,
  File,
  FolderOpen,
} from "lucide-react";
import { ApiError, api } from "../lib/api";
import { formatSize } from "../lib/format";
import type { QuarkShareTree } from "../types";
import { Button, Modal, Spinner } from "./ui";

interface QuarkSharePickerProps {
  open: boolean;
  shareUrl: string;
  passcode: string;
  subscriptionId?: number;
  initialPath?: string;
  initialFid?: string;
  onClose: () => void;
  onSelect: (path: string, fid: string) => void;
}

function trimPath(value: string): string {
  return (value || "").replace(/^\/+|\/+$/g, "").replace(/\/{2,}/g, "/");
}

export default function QuarkSharePicker({
  open,
  shareUrl,
  passcode,
  subscriptionId,
  initialPath = "",
  initialFid = "",
  onClose,
  onSelect,
}: QuarkSharePickerProps) {
  const trimmedUrl = shareUrl.trim();
  /** 当前浏览的分享内相对路径；null 表示本次打开还没完成初始化 */
  const [activePath, setActivePath] = useState<string | null>(null);
  const [startFid, setStartFid] = useState("");
  const [data, setData] = useState<QuarkShareTree | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const seqRef = useRef(0);

  // 打开或外部初始路径变化时，回到分享内的目标目录
  useEffect(() => {
    if (!open) {
      setActivePath(null);
      setData(null);
      setError(null);
      setLoading(false);
      return;
    }
    setActivePath(trimPath(initialPath));
    setStartFid((initialFid || "").trim());
    setData(null);
    setError(null);
  }, [open, initialPath, initialFid]);

  useEffect(() => {
    if (!open) return;
    if (activePath === null) return;
    if (!trimmedUrl) {
      setData(null);
      setError(null);
      setLoading(false);
      return;
    }
    const seq = ++seqRef.current;
    const useFid = !activePath && Boolean(startFid);
    setLoading(true);
    setError(null);
    setData(null);
    api
      .post<QuarkShareTree>("/api/subscriptions/quark/share/tree", {
        share_url: trimmedUrl,
        passcode: passcode || "",
        path: useFid ? "" : activePath,
        fid: useFid ? startFid : "",
        subscription_id: subscriptionId ?? null,
      })
      .then((result) => {
        if (seqRef.current !== seq) return;
        setData(result);
        if (useFid) {
          setStartFid("");
          if (result.path) setActivePath(trimPath(result.path));
        }
      })
      .catch((err) => {
        if (seqRef.current !== seq) return;
        if (err instanceof ApiError && err.status === 401) {
          window.dispatchEvent(new CustomEvent("cinemint:unauthorized"));
        }
        setError(err instanceof Error ? err.message : "读取分享目录失败");
      })
      .finally(() => {
        if (seqRef.current !== seq) return;
        setLoading(false);
      });
  }, [open, activePath, startFid, trimmedUrl, passcode, subscriptionId]);

  const currentPath = activePath ?? "";
  const parts = useMemo(() => currentPath.split("/").filter(Boolean), [currentPath]);
  const entries = useMemo(() => {
    const list = data?.entries ? [...data.entries] : [];
    // 文件夹排在文件前面，组内保持接口返回顺序
    return list.sort((a, b) => Number(b.is_dir) - Number(a.is_dir));
  }, [data]);
  const dirCount =
    data?.dirs ?? entries.filter((entry) => entry.is_dir).length;
  const fileCount =
    data?.files ?? entries.filter((entry) => !entry.is_dir).length;
  const busy = loading || activePath === null;

  const navigate = (path: string) => {
    setStartFid("");
    setActivePath(trimPath(path));
  };

  const goUp = () => {
    if (!currentPath) return;
    navigate(parts.slice(0, -1).join("/"));
  };

  const select = () => {
    if (!data) return;
    onSelect(data.path || currentPath, data.fid || "");
    onClose();
  };

  return (
    <Modal
      open={open}
      onClose={onClose}
      title="浏览分享文件"
      width="max-w-2xl"
      top={
        <div className="flex flex-wrap items-center gap-1.5 rounded-xl border border-white/[0.07] bg-ink-950/40 px-2.5 py-2 text-[11px] text-slate-400">
          <button
            type="button"
            onClick={() => navigate("")}
            className="rounded px-1 py-0.5 transition hover:bg-white/[0.06] hover:text-slate-200"
          >
            分享根目录
          </button>
          {parts.map((part, index) => (
            <span key={`${part}-${index}`} className="flex items-center gap-1.5">
              <ChevronRight size={11} className="text-slate-600" />
              <button
                type="button"
                onClick={() => navigate(parts.slice(0, index + 1).join("/"))}
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
            disabled={!currentPath}
          >
            上一级
          </Button>
        </div>
      }
      footer={
        <>
          <Button type="button" onClick={onClose}>
            取消
          </Button>
          <Button
            type="button"
            variant="primary"
            onClick={select}
            disabled={!data || busy || Boolean(error)}
          >
            <Check size={14} /> 使用当前目录
          </Button>
        </>
      }
    >
      <div className="space-y-3">
        {!trimmedUrl ? (
          <div className="flex items-start gap-2 rounded-xl border border-amber-500/25 bg-amber-500/[0.06] px-3 py-2.5 text-[11px] leading-relaxed text-amber-200">
            <AlertTriangle size={14} className="mt-0.5 shrink-0" />
            <span>请先填写分享链接</span>
          </div>
        ) : null}

        <div className="flex items-center justify-between gap-3 text-[11px] text-slate-500">
          <span className="truncate">
            当前目录：{currentPath || "分享根目录"}
          </span>
          {data ? (
            <span className="shrink-0">
              本页 {dirCount} 个文件夹 · {fileCount} 个文件
            </span>
          ) : null}
        </div>

        <div className="max-h-[42vh] min-h-[180px] space-y-0.5 overflow-y-auto rounded-xl border border-white/[0.06] bg-ink-950/40 p-1.5">
          {!trimmedUrl ? (
            <div className="px-2 py-8 text-center text-[11px] text-slate-600">
              填写分享链接后即可浏览分享内的文件
            </div>
          ) : busy ? (
            <div className="flex items-center justify-center gap-2 py-10 text-[11px] text-slate-500">
              <Spinner size={13} /> 读取分享目录…
            </div>
          ) : error ? (
            <div className="px-2 py-8 text-center text-[11px] leading-relaxed text-rose-300">
              {error}
            </div>
          ) : entries.length ? (
            entries.map((entry) =>
              entry.is_dir ? (
                <button
                  key={entry.fid || entry.path}
                  type="button"
                  onClick={() => navigate(entry.path)}
                  className="flex w-full items-center gap-2 rounded-md px-2.5 py-2 text-left text-xs text-slate-300 transition hover:bg-white/[0.05]"
                >
                  <FolderOpen size={13} className="shrink-0 text-slate-500" />
                  <span className="min-w-0 flex-1 truncate">{entry.name}</span>
                  <ChevronRight size={12} className="shrink-0 text-slate-600" />
                </button>
              ) : (
                <div
                  key={entry.fid || entry.path}
                  className="flex w-full items-center gap-2 rounded-md px-2.5 py-2 text-xs text-slate-400"
                >
                  <File size={13} className="shrink-0 text-slate-600" />
                  <span className="min-w-0 flex-1 truncate">{entry.name}</span>
                  <span className="shrink-0 text-[10px] text-slate-500">
                    {formatSize(entry.size)}
                  </span>
                </div>
              ),
            )
          ) : (
            <div className="px-2 py-8 text-center text-[11px] text-slate-600">
              这个目录里没有文件或文件夹
            </div>
          )}
        </div>

        {data?.truncated ? (
          <div className="flex items-start gap-2 rounded-xl border border-amber-500/25 bg-amber-500/[0.06] px-3 py-2 text-[11px] leading-relaxed text-amber-200">
            <AlertTriangle size={13} className="mt-0.5 shrink-0" />
            <span>该目录条目过多，这里只显示了前一部分；可以进入子目录继续浏览。</span>
          </div>
        ) : null}

        {trimmedUrl ? (
          <div className="rounded-xl border border-white/[0.06] bg-ink-950/40 px-3 py-2 text-[11px] leading-relaxed text-slate-400">
            将只转存：
            <span className="text-slate-200">{currentPath || "分享根目录"}</span>
            （含子目录）
          </div>
        ) : null}

        <p className="text-[10px] leading-relaxed text-slate-500">
          这里浏览的是分享里的真实文件树；文件夹可以点击进入，文件不可选。
          选择目录后订阅只转存该目录（含子目录）里的文件。
        </p>
      </div>
    </Modal>
  );
}
