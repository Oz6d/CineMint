import { createContext, useCallback, useContext, useMemo, useState, type ReactNode } from "react";
import { CheckCircle2, Info, TriangleAlert, X } from "lucide-react";

type ToastKind = "success" | "error" | "info";
interface Toast {
  id: number;
  kind: ToastKind;
  message: string;
}

interface ToastContextValue {
  push: (message: string, kind?: ToastKind) => void;
  success: (message: string) => void;
  error: (message: string) => void;
}

const ToastContext = createContext<ToastContextValue | null>(null);

let nextId = 1;

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);

  const push = useCallback((message: string, kind: ToastKind = "info") => {
    const id = nextId++;
    setToasts((current) => [...current, { id, kind, message }]);
    window.setTimeout(() => {
      setToasts((current) => current.filter((toast) => toast.id !== id));
    }, kind === "error" ? 6000 : 3800);
  }, []);

  const value = useMemo<ToastContextValue>(
    () => ({
      push,
      success: (message: string) => push(message, "success"),
      error: (message: string) => push(message, "error"),
    }),
    [push],
  );

  return (
    <ToastContext.Provider value={value}>
      {children}
      <div className="pointer-events-none fixed bottom-5 right-5 z-[100] flex w-[min(92vw,380px)] flex-col gap-2">
        {toasts.map((toast) => (
          <div
            key={toast.id}
            className={`pointer-events-auto flex items-start gap-3 rounded-xl border px-4 py-3 text-sm shadow-card backdrop-blur-xl animate-fade-up ${
              toast.kind === "success"
                ? "border-mint-500/30 bg-mint-500/10 text-mint-400"
                : toast.kind === "error"
                  ? "border-rose-500/30 bg-rose-500/10 text-rose-200"
                  : "border-white/10 bg-ink-800/90 text-slate-200"
            }`}
          >
            {toast.kind === "success" ? (
              <CheckCircle2 size={17} className="mt-0.5 shrink-0" />
            ) : toast.kind === "error" ? (
              <TriangleAlert size={17} className="mt-0.5 shrink-0" />
            ) : (
              <Info size={17} className="mt-0.5 shrink-0" />
            )}
            <span className="flex-1 leading-relaxed">{toast.message}</span>
            <button
              className="text-current/60 transition hover:text-current"
              onClick={() => setToasts((current) => current.filter((item) => item.id !== toast.id))}
            >
              <X size={14} />
            </button>
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  );
}

export function useToast(): ToastContextValue {
  const context = useContext(ToastContext);
  if (!context) {
    throw new Error("useToast 必须在 ToastProvider 内使用");
  }
  return context;
}
