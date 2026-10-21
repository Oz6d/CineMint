import {
  useEffect,
  useState,
  type ButtonHTMLAttributes,
  type InputHTMLAttributes,
  type ReactNode,
  type SelectHTMLAttributes,
} from "react";
import { createPortal } from "react-dom";
import clsx from "clsx";
import { AlertTriangle, Loader2, X } from "lucide-react";
import { statusLabel } from "../lib/format";

export function Spinner({ size = 16, className }: { size?: number; className?: string }) {
  return <Loader2 size={size} className={clsx("animate-spin text-mint-400", className)} />;
}

type ButtonProps = ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: "primary" | "ghost" | "danger";
  loading?: boolean;
};

export function Button({ variant = "ghost", loading, className, children, ...rest }: ButtonProps) {
  const variantClass =
    variant === "primary" ? "btn-primary" : variant === "danger" ? "btn-danger" : "btn-ghost";
  return (
    <button className={clsx(variantClass, className)} disabled={rest.disabled || loading} {...rest}>
      {loading ? <Spinner size={15} className="text-current" /> : null}
      {children}
    </button>
  );
}

export function Badge({
  children,
  tone = "slate",
  className,
}: {
  children: ReactNode;
  tone?: "slate" | "mint" | "sky" | "amber" | "rose" | "violet";
  className?: string;
}) {
  const tones: Record<string, string> = {
    slate: "border-white/10 bg-white/[0.05] text-slate-300",
    mint: "border-mint-500/30 bg-mint-500/10 text-mint-400",
    sky: "border-sky-500/30 bg-sky-500/10 text-sky-300",
    amber: "border-amber-500/30 bg-amber-500/10 text-amber-300",
    rose: "border-rose-500/30 bg-rose-500/10 text-rose-300",
    violet: "border-violet-500/30 bg-violet-500/10 text-violet-300",
  };
  return <span className={clsx("chip", tones[tone], className)}>{children}</span>;
}

const STATUS_TONES: Record<string, "slate" | "mint" | "sky" | "amber" | "rose" | "violet"> = {
  matched: "sky",
  review: "amber",
  unmatched: "slate",
  organized: "mint",
  failed: "rose",
  ignored: "slate",
  missing: "rose",
  queued: "slate",
  running: "sky",
  success: "mint",
  partial: "amber",
  cancelled: "slate",
};

export function StatusBadge({ status }: { status: string }) {
  return <Badge tone={STATUS_TONES[status] || "slate"}>{statusLabel(status)}</Badge>;
}

export function Card({
  children,
  className,
  strong,
}: {
  children: ReactNode;
  className?: string;
  strong?: boolean;
}) {
  return <div className={clsx(strong ? "glass-strong" : "glass", "p-5", className)}>{children}</div>;
}

export function Progress({ value, className }: { value: number; className?: string }) {
  return (
    <div className={clsx("h-1.5 w-full overflow-hidden rounded-full bg-white/[0.06]", className)}>
      <div
        className="h-full rounded-full bg-gradient-to-r from-mint-500 to-sky-500 transition-all duration-500"
        style={{ width: `${Math.min(100, Math.max(0, value))}%` }}
      />
    </div>
  );
}

export function Field({
  label,
  hint,
  children,
  className,
}: {
  label?: string;
  hint?: string;
  children: ReactNode;
  className?: string;
}) {
  return (
    <div className={className}>
      {label ? <label className="label">{label}</label> : null}
      {children}
      {hint ? <p className="mt-1.5 text-[11px] leading-relaxed text-slate-500">{hint}</p> : null}
    </div>
  );
}

export function Input({ className, ...rest }: InputHTMLAttributes<HTMLInputElement>) {
  return <input className={clsx("input", className)} {...rest} />;
}

export function Select({ className, children, ...rest }: SelectHTMLAttributes<HTMLSelectElement>) {
  return (
    <select className={clsx("input", className)} {...rest}>
      {children}
    </select>
  );
}

export function Switch({
  checked,
  onChange,
  label,
  hint,
  disabled = false,
}: {
  checked: boolean;
  onChange: (value: boolean) => void;
  label: string;
  hint?: string;
  disabled?: boolean;
}) {
  return (
    <button
      type="button"
      disabled={disabled}
      onClick={() => {
        if (!disabled) onChange(!checked);
      }}
      className={clsx(
        "flex w-full items-center justify-between gap-4 rounded-xl border border-white/[0.07] bg-white/[0.02] px-3.5 py-2.5 text-left transition",
        disabled ? "cursor-not-allowed opacity-45" : "hover:border-white/15",
      )}
    >
      <span>
        <span className={clsx("block text-sm", disabled ? "text-slate-400" : "text-slate-200")}>{label}</span>
        {hint ? <span className="mt-0.5 block text-[11px] text-slate-500">{hint}</span> : null}
      </span>
      <span
        className={clsx(
          "relative h-5 w-9 shrink-0 rounded-full transition",
          checked ? "bg-mint-500/80" : "bg-white/15",
          disabled ? "opacity-60" : "",
        )}
      >
        <span
          className={clsx(
            "absolute top-0.5 h-4 w-4 rounded-full bg-white shadow transition-all",
            checked ? "left-[18px]" : "left-0.5",
          )}
        />
      </span>
    </button>
  );
}

export function Modal({
  open,
  onClose,
  title,
  children,
  width = "max-w-xl",
  footer,
  top,
}: {
  open: boolean;
  onClose: () => void;
  title: ReactNode;
  children: ReactNode;
  width?: string;
  footer?: ReactNode;
  /** 固定在标题下方、不随内容滚动的区域 */
  top?: ReactNode;
}) {
  useEffect(() => {
    if (!open) return;
    const handler = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [open, onClose]);

  if (!open) return null;
  return createPortal(
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
      <div className="absolute inset-0 bg-ink-950/80 backdrop-blur-sm" onClick={onClose} />
      <div
        className={clsx(
          "glass-strong relative z-10 flex max-h-[88vh] w-full flex-col overflow-hidden rounded-2xl animate-fade-up",
          width,
        )}
      >
        <div className="flex items-start justify-between gap-4 px-5 pb-3 pt-5">
          <h3 className="text-base font-semibold text-white">{title}</h3>
          <button className="rounded-lg p-1 text-slate-400 transition hover:bg-white/10 hover:text-white" onClick={onClose}>
            <X size={17} />
          </button>
        </div>
        {top ? <div className="shrink-0 px-5 pb-3">{top}</div> : null}
        <div className="min-h-0 flex-1 overflow-y-auto px-5 pb-5">{children}</div>
        {footer ? (
          <div className="flex items-center justify-end gap-2 border-t border-white/[0.06] px-5 py-3.5">
            {footer}
          </div>
        ) : null}
      </div>
    </div>,
    document.body,
  );
}

export function Drawer({
  open,
  onClose,
  title,
  children,
  width = "max-w-3xl",
}: {
  open: boolean;
  onClose: () => void;
  title: ReactNode;
  children: ReactNode;
  width?: string;
}) {
  if (!open) return null;
  return createPortal(
    <div className="fixed inset-0 z-50 flex justify-end">
      <div className="absolute inset-0 bg-ink-950/75 backdrop-blur-sm" onClick={onClose} />
      <div
        className={clsx(
          "relative z-10 h-full w-full overflow-y-auto border-l border-white/10 bg-ink-900/95 backdrop-blur-2xl animate-fade-up",
          width,
        )}
      >
        <div className="sticky top-0 z-10 flex items-center justify-between gap-4 border-b border-white/[0.06] bg-ink-900/85 px-5 py-4 backdrop-blur-xl">
          <h3 className="text-base font-semibold text-white">{title}</h3>
          <button className="rounded-lg p-1.5 text-slate-400 transition hover:bg-white/10 hover:text-white" onClick={onClose}>
            <X size={17} />
          </button>
        </div>
        <div className="p-5">{children}</div>
      </div>
    </div>,
    document.body,
  );
}

export function ConfirmDialog({
  open,
  title,
  message,
  confirmText = "确认",
  danger,
  onConfirm,
  onClose,
}: {
  open: boolean;
  title: string;
  message: ReactNode;
  confirmText?: string;
  danger?: boolean;
  onConfirm: () => void;
  onClose: () => void;
}) {
  return (
    <Modal
      open={open}
      onClose={onClose}
      title={title}
      width="max-w-md"
      footer={
        <>
          <Button onClick={onClose}>取消</Button>
          <Button variant={danger ? "danger" : "primary"} onClick={onConfirm}>
            {confirmText}
          </Button>
        </>
      }
    >
      <div className="flex items-start gap-3 text-sm text-slate-300">
        {danger ? <AlertTriangle size={18} className="mt-0.5 shrink-0 text-rose-400" /> : null}
        <div className="leading-relaxed">{message}</div>
      </div>
    </Modal>
  );
}

export function EmptyState({
  icon,
  title,
  description,
  action,
}: {
  icon?: ReactNode;
  title: string;
  description?: string;
  action?: ReactNode;
}) {
  return (
    <div className="flex flex-col items-center justify-center gap-3 rounded-2xl border border-dashed border-white/10 bg-white/[0.015] px-6 py-14 text-center">
      {icon ? <div className="text-mint-400/80">{icon}</div> : null}
      <div className="text-sm font-medium text-slate-200">{title}</div>
      {description ? <div className="max-w-md text-xs leading-relaxed text-slate-500">{description}</div> : null}
      {action}
    </div>
  );
}

export function TitleLogo({
  url,
  title,
  className,
}: {
  url?: string | null;
  title?: string;
  className?: string;
}) {
  const [failed, setFailed] = useState(false);
  useEffect(() => setFailed(false), [url]);
  if (!url || failed) return <>{title || ""}</>;
  return (
    <img
      src={url}
      alt={title || ""}
      loading="lazy"
      onError={() => setFailed(true)}
      className={clsx("block max-h-8 w-auto max-w-[280px] object-contain object-left", className)}
    />
  );
}

export function Poster({
  url,
  title,
  subtitle,
  className,
  rounded = "rounded-xl",
}: {
  url?: string | null;
  title?: string;
  subtitle?: string;
  className?: string;
  rounded?: string;
}) {
  const [failed, setFailed] = useState(false);
  useEffect(() => setFailed(false), [url]);
  return (
    <div className={clsx("relative overflow-hidden poster-fallback", rounded, className)}>
      {url && !failed ? (
        <img
          src={url}
          alt={title || ""}
          loading="lazy"
          className="h-full w-full object-cover"
          onError={() => setFailed(true)}
        />
      ) : (
        <div className="flex h-full w-full flex-col items-center justify-center gap-1 px-2 text-center">
          <span className="text-[13px] font-medium leading-tight text-white/85">{title || "待识别"}</span>
          {subtitle ? <span className="text-[10px] text-white/40">{subtitle}</span> : null}
        </div>
      )}
    </div>
  );
}
