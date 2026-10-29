import { useEffect, useState } from "react";
import type { MediaItem } from "../types";
import { api } from "../lib/api";
import { useToast } from "../lib/toast";
import { Button, Field, Input, Modal } from "./ui";

export type TargetNameItem = {
  id: number;
  file_name: string;
  planned_name?: string;
  auto_name?: string;
  target_name_override?: string;
};

export function TargetNameModal({
  item,
  onClose,
  onSaved,
}: {
  item: TargetNameItem | null;
  onClose: () => void;
  onSaved?: (updated: MediaItem) => void;
}) {
  const toast = useToast();
  const [value, setValue] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!item) return;
    setValue(item.target_name_override || item.planned_name || item.auto_name || item.file_name || "");
  }, [item?.id, item?.target_name_override, item?.planned_name, item?.auto_name, item?.file_name]);

  const save = async (targetName: string) => {
    if (!item || busy) return;
    setBusy(true);
    try {
      const updated = await api.put<MediaItem>(`/api/items/${item.id}`, { target_name: targetName });
      toast.success(targetName.trim() ? "已保存整理后名称" : "已恢复自动命名");
      onSaved?.(updated);
      onClose();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "保存失败");
    } finally {
      setBusy(false);
    }
  };

  return (
    <Modal
      open={Boolean(item)}
      onClose={() => {
        if (!busy) onClose();
      }}
      title="修改整理后名称"
      width="max-w-lg"
      footer={
        <>
          <Button className="mr-auto" onClick={() => save("")} disabled={busy}>
            恢复自动
          </Button>
          <Button variant="primary" loading={busy} onClick={() => save(value)}>
            保存名称
          </Button>
        </>
      }
    >
      <div className="space-y-4">
        <div className="rounded-xl border border-white/[0.06] bg-white/[0.02] px-3.5 py-2.5 text-[11px] text-slate-400">
          当前文件：<span className="break-all font-mono text-slate-300">{item?.file_name}</span>
        </div>
        <Field
          label="整理后名称"
          hint="只改文件名，不会移动文件；保存后整理 / 仅重命名都会使用这个名称。"
        >
          <Input
            value={value}
            onChange={(event) => setValue(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter") void save(value);
            }}
            autoFocus
            className="font-mono text-[12px]"
          />
        </Field>
        <div className="text-[10px] leading-relaxed text-slate-500">
          自动命名：<span className="break-all font-mono text-slate-400">{item?.auto_name || item?.planned_name || item?.file_name}</span>
          {item?.target_name_override ? <span className="ml-1 text-mint-300">· 当前为手动名称</span> : null}
        </div>
      </div>
    </Modal>
  );
}
