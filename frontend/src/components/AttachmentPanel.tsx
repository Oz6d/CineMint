import { useState } from "react";
import {
  AlertTriangle,
  FileCode2,
  FileText,
  FolderOpen,
  Image as ImageIcon,
  Link2,
  Loader2,
  Music,
  Paperclip,
  Unlink,
} from "lucide-react";
import clsx from "clsx";
import type { Attachment, AttachmentPayload } from "../types";
import { formatSize } from "../lib/format";

const KIND_ORDER = ["subtitle", "audio", "image", "meta"];
const KIND_LABEL: Record<string, string> = {
  subtitle: "字幕",
  audio: "音轨",
  image: "图片",
  meta: "元信息",
};

function KindIcon({ kind, size = 13 }: { kind: string; size?: number }) {
  if (kind === "subtitle") return <FileText size={size} />;
  if (kind === "audio") return <Music size={size} />;
  if (kind === "image") return <ImageIcon size={size} />;
  return <FileCode2 size={size} />;
}

function kindTone(kind: string): string {
  switch (kind) {
    case "subtitle":
      return "text-sky-300";
    case "audio":
      return "text-amber-300";
    case "image":
      return "text-violet-300";
    default:
      return "text-slate-300";
  }
}

function ActionButton({
  action,
  busy,
  onClick,
}: {
  action: "link" | "unlink";
  busy: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      disabled={busy}
      onClick={onClick}
      title={action === "link" ? "手动关联到当前视频" : "取消手动关联，恢复自动匹配"}
      className={clsx(
        "flex shrink-0 items-center gap-1 rounded-md border px-1.5 py-0.5 text-[10px] transition",
        action === "link"
          ? "border-mint-400/25 bg-mint-400/10 text-mint-300 hover:bg-mint-400/20"
          : "border-white/10 bg-white/[0.03] text-slate-400 hover:text-slate-200",
        busy && "cursor-wait opacity-60",
      )}
    >
      {busy ? (
        <Loader2 size={10} className="animate-spin" />
      ) : action === "link" ? (
        <Link2 size={10} />
      ) : (
        <Unlink size={10} />
      )}
      {action === "link" ? "关联" : "取消"}
    </button>
  );
}

function AttachmentRow({
  item,
  action,
  busy = false,
  onAction,
}: {
  item: Attachment;
  action?: "link" | "unlink";
  busy?: boolean;
  onAction?: () => void;
}) {
  return (
    <div className="flex items-start gap-2 rounded-lg border border-white/[0.05] bg-white/[0.02] px-2.5 py-1.5">
      <span className={clsx("mt-0.5 shrink-0", kindTone(item.kind))}>
        <KindIcon kind={item.kind} />
      </span>
      <div className="min-w-0 flex-1">
        <div className="break-all text-[11px] leading-snug text-slate-300">{item.name}</div>
        <div className="mt-0.5 flex flex-wrap items-center gap-x-2 gap-y-0.5 text-[10px] text-slate-500">
          <span>{item.kind_label}</span>
          {item.size ? <span>{formatSize(item.size)}</span> : null}
          {item.scope === "folder" ? (
            <span className="rounded bg-violet-400/10 px-1 py-px text-violet-300">文件夹级</span>
          ) : null}
          {item.manual ? (
            <span className="rounded bg-mint-400/10 px-1 py-px text-mint-300">手动</span>
          ) : null}
          {item.reason ? <span>{item.reason}</span> : null}
        </div>
      </div>
      {action && onAction ? <ActionButton action={action} busy={busy} onClick={onAction} /> : null}
    </div>
  );
}

function videoLabel(video: {
  name: string;
  season?: number | null;
  episode?: number | null;
}): string {
  const season = video.season;
  const episode = video.episode;
  if (season !== null && season !== undefined && episode !== null && episode !== undefined) {
    return `S${String(season).padStart(2, "0")}E${String(episode).padStart(2, "0")} · ${video.name}`;
  }
  return video.name;
}

export function AttachmentPanel({
  data,
  loading = false,
  compact = false,
  className,
  onLink,
  onUnlink,
}: {
  data: AttachmentPayload | null;
  loading?: boolean;
  compact?: boolean;
  className?: string;
  onLink?: (path: string, targetVideoPath?: string) => Promise<void>;
  onUnlink?: (path: string) => Promise<void>;
}) {
  const [busyPath, setBusyPath] = useState<string | null>(null);
  const [targetVideoByPath, setTargetVideoByPath] = useState<Record<string, string>>({});
  const items = data?.items || [];
  const folderItems = data?.folder_items || [];
  const unmatched = data?.unmatched || [];
  const videos = data?.videos || [];
  const currentPath = data?.current_path || "";
  const multiVideos = videos.length > 1;
  const total = data?.total ?? items.length + folderItems.length;
  const counts = data?.counts || {};
  const hasManual = items.some((item) => item.manual);

  const grouped = KIND_ORDER.map((kind) => ({
    kind,
    label: KIND_LABEL[kind] || "文件",
    items: [...items, ...folderItems].filter((item) => item.kind === kind),
  })).filter((group) => group.items.length > 0);

  const runAction = async (path: string, action?: (path: string) => Promise<void>) => {
    if (!action) return;
    setBusyPath(path);
    try {
      await action(path);
    } finally {
      setBusyPath(null);
    }
  };

  return (
    <div
      className={clsx(
        "rounded-xl border border-white/[0.06] bg-white/[0.02]",
        compact ? "px-3 py-2.5" : "px-3.5 py-3",
        className,
      )}
    >
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-1.5 text-[12px] font-medium text-slate-200">
          <Paperclip size={13} className="text-slate-500" />
          附带文件
          {total > 0 ? <span className="text-mint-300">{total}</span> : null}
        </div>
        {loading ? (
          <span className="flex items-center gap-1 text-[10px] text-slate-500">
            <Loader2 size={11} className="animate-spin" /> 读取中…
          </span>
        ) : total > 0 ? (
          <div className="flex flex-wrap gap-1">
            {KIND_ORDER.filter((kind) => counts[kind]).map((kind) => (
              <span key={kind} className="rounded bg-white/[0.05] px-1.5 py-px text-[10px] text-slate-400">
                {KIND_LABEL[kind]} {counts[kind]}
              </span>
            ))}
          </div>
        ) : null}
      </div>

      {loading && !data ? (
        <div className="mt-2 text-[11px] text-slate-500">正在读取同目录及 Subs / Sub / Subtitles 字幕子目录…</div>
      ) : data?.error ? (
        <div className="mt-2 flex items-start gap-1.5 text-[11px] leading-relaxed text-rose-300">
          <AlertTriangle size={12} className="mt-0.5 shrink-0" />
          <span>读取附带文件失败：{data.error}</span>
        </div>
      ) : total === 0 && !unmatched.length ? (
        <div className="mt-2 text-[11px] leading-relaxed text-slate-500">
          同目录和字幕子目录里没有检测到字幕、外挂音轨、海报或 NFO。整理时只移动正片。
        </div>
      ) : (
        <div className={clsx("mt-2 space-y-2", compact && "max-h-64 overflow-y-auto pr-0.5")}>
          {grouped.map((group) => (
            <div key={group.kind}>
              <div className="mb-1 flex items-center gap-1 text-[10px] text-slate-500">
                <span className={kindTone(group.kind)}>
                  <KindIcon kind={group.kind} size={11} />
                </span>
                {group.label}
                <span className="text-slate-600">{group.items.length}</span>
              </div>
              <div className="space-y-1">
                {group.items.map((item) => (
                  <AttachmentRow
                    key={item.path}
                    item={item}
                    action={item.manual && onUnlink ? "unlink" : undefined}
                    busy={busyPath === item.path}
                    onAction={() => runAction(item.path, onUnlink)}
                  />
                ))}
              </div>
            </div>
          ))}

          {folderItems.some((item) => item.scope === "folder") ? (
            <div className="flex items-start gap-1.5 pt-0.5 text-[10px] leading-relaxed text-slate-500">
              <FolderOpen size={11} className="mt-0.5 shrink-0" />
              文件夹级资源会保留原文件名，随该文件夹里第一个整理的文件移动；目标已有同名文件时不覆盖。
            </div>
          ) : null}

          {hasManual ? (
            <div className="flex items-start gap-1.5 pt-0.5 text-[10px] leading-relaxed text-slate-500">
              <Link2 size={11} className="mt-0.5 shrink-0" />
              手动关联优先于自动匹配；整理时会按这个归属移动。取消后会恢复自动判断。
            </div>
          ) : null}

          {unmatched.length ? (
            <div className="rounded-lg border border-amber-500/20 bg-amber-500/[0.05] px-2.5 py-2">
              <div className="flex items-center gap-1 text-[10px] text-amber-200">
                <AlertTriangle size={11} /> 有 {unmatched.length} 个文件未能确定归属
              </div>
              <div className="mt-1 space-y-1">
                {unmatched.map((item) => (
                  <div
                    key={item.path}
                    className="flex items-center gap-2 rounded-md border border-amber-500/10 bg-amber-500/[0.03] px-2 py-1"
                  >
                    <span className={clsx("shrink-0", kindTone(item.kind))}>
                      <KindIcon kind={item.kind} size={11} />
                    </span>
                    <div className="min-w-0 flex-1">
                      <div className="break-all text-[10px] leading-relaxed text-amber-100/80">{item.name}</div>
                      <div className="text-[9px] text-amber-100/40">{item.reason || item.kind_label}</div>
                    </div>
                    {onLink ? (
                      <div className="flex shrink-0 items-center gap-1">
                        {multiVideos ? (
                          <select
                            value={targetVideoByPath[item.path] || currentPath || videos[0]?.path || ""}
                            onChange={(event) =>
                              setTargetVideoByPath((prev) => ({
                                ...prev,
                                [item.path]: event.target.value,
                              }))
                            }
                            title="选择要关联的视频"
                            className="max-w-[9.5rem] rounded-md border border-white/10 bg-slate-900/80 px-1 py-0.5 text-[9px] text-slate-300"
                          >
                            {videos.map((video) => (
                              <option key={video.path} value={video.path}>
                                {videoLabel(video)}
                                {video.path === currentPath ? "（当前）" : ""}
                              </option>
                            ))}
                          </select>
                        ) : null}
                        <ActionButton
                          action="link"
                          busy={busyPath === item.path}
                          onClick={() =>
                            runAction(item.path, () =>
                              onLink(
                                item.path,
                                targetVideoByPath[item.path] || currentPath || undefined,
                              ),
                            )
                          }
                        />
                      </div>
                    ) : null}
                  </div>
                ))}
              </div>
              <div className="mt-1 text-[10px] leading-relaxed text-amber-100/50">
                {onLink
                  ? multiVideos
                    ? "选择目标集后点「关联」，可把该文件挂到同目录其他视频；手动关联会随整理移动。"
                    : "点右侧「关联」可直接挂到当前视频，手动关联会随整理移动；也可以改名成带 S01E02 / 第2集 / 02 的名称。"
                  : "这些文件没有足够的名称信息与当前视频对应，整理时不会移动。字幕 / 音轨可改成包含 S01E02 / 第2集 / 02 的名称；图片可改成 poster / fanart，或与视频同名。"}
              </div>
            </div>
          ) : null}
        </div>
      )}
    </div>
  );
}
