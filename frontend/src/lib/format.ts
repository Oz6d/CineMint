export function formatSize(bytes?: number | null): string {
  if (!bytes) return "0 B";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let value = bytes;
  let index = 0;
  while (value >= 1024 && index < units.length - 1) {
    value /= 1024;
    index += 1;
  }
  return `${value >= 10 || index === 0 ? Math.round(value) : value.toFixed(1)} ${units[index]}`;
}

export function formatTime(iso?: string | null): string {
  if (!iso) return "—";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  const now = Date.now();
  const diff = now - date.getTime();
  if (diff < 60_000) return "刚刚";
  if (diff < 3_600_000) return `${Math.floor(diff / 60_000)} 分钟前`;
  if (diff < 86_400_000) return `${Math.floor(diff / 3_600_000)} 小时前`;
  return date.toLocaleString("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export function formatDateTime(iso?: string | null): string {
  if (!iso) return "—";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  return date.toLocaleString("zh-CN", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export function statusLabel(status: string): string {
  const map: Record<string, string> = {
    matched: "已匹配",
    review: "待确认",
    unmatched: "未识别",
    organized: "已整理",
    failed: "失败",
    ignored: "已忽略",
    missing: "源缺失",
    queued: "排队中",
    running: "执行中",
    success: "已完成",
    partial: "部分完成",
    cancelled: "已取消",
  };
  return map[status] || status;
}

export function taskTypeLabel(type: string): string {
  if (type === "scan") return "扫描识别";
  if (type === "organize") return "整理刮削";
  if (type === "rename") return "仅重命名";
  return type;
}

export function seasonEpisode(season?: number | null, episode?: number | null): string {
  if (season == null && episode == null) return "";
  const s = season != null ? `S${String(season).padStart(2, "0")}` : "";
  const e = episode != null ? `E${String(episode).padStart(2, "0")}` : "";
  return `${s}${e}`;
}
