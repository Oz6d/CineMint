import { useEffect, useMemo, useState } from "react";
import { AlertTriangle, CheckCheck, FilePenLine, FolderPen, TextCursorInput } from "lucide-react";
import clsx from "clsx";
import { api } from "../lib/api";
import { useToast } from "../lib/toast";
import { Button, Modal, Spinner, Switch } from "./ui";

export interface RenamePlanItem {
  id: number;
  file_name: string;
  source_path: string;
  target_path: string;
  desired_file_name: string;
  folder_steps: { old_path: string; new_path: string }[];
  file_changed: boolean;
  folder_changed: boolean;
  changed: boolean;
  warnings: string[];
  error: string;
}

export function RenameModal({
  open,
  libraryId,
  itemIds,
  onClose,
  onStarted,
}: {
  open: boolean;
  libraryId: number;
  itemIds: number[];
  onClose: () => void;
  onStarted: (taskId: number) => void;
}) {
  const toast = useToast();
  const [renameFiles, setRenameFiles] = useState(true);
  const [renameFolders, setRenameFolders] = useState(true);
  const [plan, setPlan] = useState<RenamePlanItem[] | null>(null);
  const [loading, setLoading] = useState(false);
  const [starting, setStarting] = useState(false);
  const key = useMemo(() => itemIds.join(","), [itemIds]);

  useEffect(() => {
    if (!open) return;
    setRenameFiles(true);
    setRenameFolders(true);
  }, [open, key]);

  useEffect(() => {
    if (!open || !itemIds.length) return;
    let alive = true;
    setLoading(true);
    setPlan(null);
    api
      .post<{ items: RenamePlanItem[] }>(`/api/libraries/${libraryId}/rename-plan`, {
        item_ids: itemIds,
        rename_files: renameFiles,
        rename_folders: renameFolders,
      })
      .then((result) => {
        if (alive) setPlan(result.items || []);
      })
      .catch((err) => {
        if (alive) {
          setPlan([]);
          toast.error(err instanceof Error ? err.message : "生成重命名预览失败");
        }
      })
      .finally(() => {
        if (alive) setLoading(false);
      });
    return () => {
      alive = false;
    };
  }, [open, libraryId, key, renameFiles, renameFolders]);

  const usable = (plan || []).filter((entry) => !entry.error && entry.changed);
  const canStart = Boolean(renameFiles || renameFolders) && usable.length > 0 && !loading && !starting;
  const folderCount = (plan || []).filter((entry) => entry.folder_changed).length;
  const fileCount = (plan || []).filter((entry) => entry.file_changed).length;

  const start = async () => {
    if (!canStart) return;
    setStarting(true);
    try {
      const result = await api.post<{ task_id: number }>(`/api/libraries/${libraryId}/rename`, {
        item_ids: itemIds,
        rename_files: renameFiles,
        rename_folders: renameFolders,
      });
      toast.success("仅重命名任务已提交，进度可在任务中心查看");
      onStarted(result.task_id);
      onClose();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "提交重命名任务失败");
    } finally {
      setStarting(false);
    }
  };

  return (
    <Modal
      open={open}
      onClose={onClose}
      title="仅重命名（不移动）"
      width="max-w-3xl"
      footer={
        <>
          <Button onClick={onClose}>取消</Button>
          <Button variant="primary" loading={starting} disabled={!canStart} onClick={start}>
            <CheckCheck size={15} /> 确认重命名
          </Button>
        </>
      }
    >
      <div className="space-y-4">
        <p className="rounded-xl border border-sky-400/15 bg-sky-400/[0.05] px-3.5 py-2.5 text-[11px] leading-relaxed text-slate-400">
          只改当前目录里的文件名 / 文件夹名，<span className="text-slate-200">不会移动到「目标目录」</span>，也不会刮削。
          附带字幕、外挂音轨和 NFO 会跟随视频文件一起改名。
        </p>

        <div className="grid gap-2 sm:grid-cols-2">
          <Switch
            checked={renameFiles}
            onChange={setRenameFiles}
            label="重命名文件"
            hint="按当前电影 / 剧集文件模板改名"
          />
          <Switch
            checked={renameFolders}
            onChange={setRenameFolders}
            label="重命名文件夹"
            hint="按当前文件夹模板改名，目录层级不符时自动跳过"
          />
        </div>

        {!renameFiles && !renameFolders ? (
          <div className="flex items-start gap-2 rounded-xl border border-amber-400/20 bg-amber-400/[0.06] px-3.5 py-2.5 text-[11px] leading-relaxed text-amber-200">
            <AlertTriangle size={14} className="mt-0.5 shrink-0" />
            文件重命名和文件夹重命名都没有选择，当前不会做任何修改。至少选择一项后才能执行。
          </div>
        ) : null}

        <div className="flex flex-wrap items-center gap-2 text-[11px] text-slate-500">
          <span>已选 {itemIds.length} 个文件</span>
          {plan ? <span>· 可改动 {usable.length} 个</span> : null}
          {plan && renameFiles ? <span>· 文件改名 {fileCount} 个</span> : null}
          {plan && renameFolders ? <span>· 文件夹改名 {folderCount} 个</span> : null}
        </div>

        {loading ? (
          <div className="flex h-36 items-center justify-center gap-2 rounded-xl border border-white/[0.06] bg-white/[0.02] text-xs text-slate-500">
            <Spinner /> 正在计算重命名结果…
          </div>
        ) : (
          <div className="space-y-2">
            {(plan || []).map((entry) => (
              <div key={entry.id} className="rounded-xl border border-white/[0.06] bg-white/[0.02] px-3.5 py-3">
                <div className="flex flex-wrap items-center gap-1.5">
                  <span className="break-all text-[12px] text-slate-200">{entry.file_name}</span>
                  {entry.error ? null : entry.file_changed ? (
                    <span className="rounded bg-mint-400/10 px-1.5 py-px text-[10px] text-mint-300">
                      <FilePenLine size={10} className="mr-0.5 inline" /> 文件
                    </span>
                  ) : null}
                  {entry.error ? null : entry.folder_changed ? (
                    <span className="rounded bg-sky-400/10 px-1.5 py-px text-[10px] text-sky-300">
                      <FolderPen size={10} className="mr-0.5 inline" /> 文件夹
                    </span>
                  ) : null}
                  {!entry.error && !entry.changed ? (
                    <span className="rounded bg-white/[0.05] px-1.5 py-px text-[10px] text-slate-500">无需改动</span>
                  ) : null}
                </div>
                {entry.error ? (
                  <div className="mt-1 text-[11px] text-rose-300">无法重命名：{entry.error}</div>
                ) : (
                  <div className="mt-2 space-y-1.5 font-mono text-[11px] leading-relaxed">
                    <div className="flex gap-2">
                      <span className="h-fit shrink-0 select-none rounded bg-white/[0.06] px-1.5 py-px text-[10px] text-slate-500">原</span>
                      <span className="min-w-0 break-all text-slate-500">{entry.source_path}</span>
                    </div>
                    <div className="flex gap-2">
                      <span
                        className={clsx(
                          "h-fit shrink-0 select-none rounded px-1.5 py-px text-[10px]",
                          entry.changed ? "bg-mint-400/10 text-mint-300" : "bg-white/[0.06] text-slate-500",
                        )}
                      >
                        新
                      </span>
                      <span className={clsx("min-w-0 break-all", entry.changed ? "text-mint-200" : "text-slate-500")}>
                        {entry.target_path}
                      </span>
                    </div>
                  </div>
                )}
                {entry.warnings?.length ? (
                  <div className="mt-2 space-y-0.5 text-[10px] leading-relaxed text-amber-200/80">
                    {entry.warnings.map((warning, index) => (
                      <div key={`${entry.id}-${index}`} className="flex gap-1.5">
                        <AlertTriangle size={11} className="mt-0.5 shrink-0" />
                        <span>{warning}</span>
                      </div>
                    ))}
                  </div>
                ) : null}
              </div>
            ))}
            {!plan?.length && !loading ? (
              <div className="rounded-xl border border-dashed border-white/10 px-3.5 py-6 text-center text-[11px] text-slate-500">
                没有可重命名的条目。
              </div>
            ) : null}
          </div>
        )}

        <p className="flex items-start gap-1.5 text-[11px] leading-relaxed text-slate-500">
          <TextCursorInput size={12} className="mt-0.5 shrink-0" />
          文件夹重命名只改当前作品对应的最后几层目录：电影改作品文件夹，剧集改剧名 / 季文件夹。目录层级不足、文件夹内混有其他作品或目标文件夹已存在时，会自动跳过文件夹改名，文件改名仍可继续。
        </p>
      </div>
    </Modal>
  );
}
