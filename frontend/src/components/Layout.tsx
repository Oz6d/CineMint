import { useEffect } from "react";
import { NavLink, Outlet, useNavigate } from "react-router-dom";
import {
  BellRing,
  CalendarDays,
  CloudDownload,
  Film,
  FolderOpen,
  LayoutDashboard,
  Library,
  ListChecks,
  LogOut,
  Menu,
  Settings,
  X,
} from "lucide-react";
import clsx from "clsx";
import { useState } from "react";
import { api } from "../lib/api";
import { useAsync } from "../lib/hooks";
import { Progress, Spinner } from "./ui";
import type { Task } from "../types";

const NAV = [
  { to: "/", label: "总览", icon: LayoutDashboard, end: true },
  { to: "/libraries", label: "影视库", icon: Library },
  { to: "/subscriptions", label: "订阅管理", icon: CloudDownload },
  { to: "/bangumi", label: "追更日历", icon: CalendarDays },
  { to: "/notifications", label: "通知渠道", icon: BellRing },
  { to: "/tasks", label: "任务中心", icon: ListChecks },
  { to: "/sources", label: "文件源", icon: FolderOpen },
  { to: "/settings", label: "设置", icon: Settings },
];

export default function Layout() {
  const [mobileOpen, setMobileOpen] = useState(false);
  const navigate = useNavigate();
  const { data: activeTasks } = useAsync<Task[]>("/api/tasks/active", [], { pollMs: 2500 });
  const running = (activeTasks || []).filter((task) => task.status === "running" || task.status === "queued");

  useEffect(() => {
    const handler = () => navigate("/login", { replace: true });
    window.addEventListener("cinemint:unauthorized", handler);
    return () => window.removeEventListener("cinemint:unauthorized", handler);
  }, [navigate]);

  const logout = async () => {
    await api.post("/api/auth/logout");
    navigate("/login", { replace: true });
  };

  return (
    <div className="flex min-h-screen">
      {/* 侧边栏 */}
      <aside
        className={clsx(
          "fixed inset-y-0 left-0 z-40 flex w-60 shrink-0 flex-col border-r border-white/[0.06] bg-ink-900/80 px-4 py-5 backdrop-blur-2xl transition-transform duration-200 lg:sticky lg:top-0 lg:h-screen lg:translate-x-0",
          mobileOpen ? "translate-x-0" : "-translate-x-full",
        )}
      >
        <div className="mb-7 flex items-center justify-between">
          <div className="flex items-center gap-2.5">
            <div className="flex h-9 w-9 items-center justify-center rounded-xl bg-gradient-to-br from-mint-500 to-sky-500 shadow-glow">
              <Film size={18} className="text-ink-950" />
            </div>
            <div>
              <div className="text-sm font-semibold tracking-wide text-white">影序 CineMint</div>
              <div className="text-[10px] text-slate-500">影视库智能整理</div>
            </div>
          </div>
          <button className="rounded-lg p-1 text-slate-400 lg:hidden" onClick={() => setMobileOpen(false)}>
            <X size={16} />
          </button>
        </div>

        <nav className="space-y-1">
          {NAV.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              end={item.end}
              onClick={() => setMobileOpen(false)}
              className={({ isActive }) =>
                clsx(
                  "flex items-center gap-3 rounded-xl px-3 py-2.5 text-sm transition",
                  isActive
                    ? "bg-gradient-to-r from-mint-500/15 to-sky-500/10 text-mint-400 shadow-[inset_0_0_0_1px_rgba(94,234,212,0.15)]"
                    : "text-slate-400 hover:bg-white/[0.04] hover:text-slate-200",
                )
              }
            >
              <item.icon size={17} />
              {item.label}
            </NavLink>
          ))}
        </nav>

        <div className="mt-auto space-y-3 pt-6">
          {running.length > 0 ? (
            <div className="rounded-xl border border-sky-500/20 bg-sky-500/[0.07] px-3 py-2.5">
              {running.slice(0, 2).map((task) => (
                <div key={task.id} className="mb-1.5 last:mb-0">
                  <div className="flex items-center justify-between text-[11px] text-sky-200">
                    <span className="truncate">
                      {task.type === "scan" ? "扫描识别" : "整理刮削"} · {task.library_name}
                    </span>
                    <Spinner size={11} />
                  </div>
                  <Progress value={task.progress} className="mt-1.5 h-1" />
                </div>
              ))}
            </div>
          ) : null}
          <button
            onClick={logout}
            className="flex w-full items-center gap-3 rounded-xl px-3 py-2.5 text-sm text-slate-500 transition hover:bg-white/[0.04] hover:text-slate-300"
          >
            <LogOut size={16} />
            退出登录
          </button>
        </div>
      </aside>

      {mobileOpen ? (
        <div className="fixed inset-0 z-30 bg-ink-950/70 backdrop-blur-sm lg:hidden" onClick={() => setMobileOpen(false)} />
      ) : null}

      {/* 主区域 */}
      <div className="flex min-w-0 flex-1 flex-col">
        <header className="sticky top-0 z-20 flex items-center gap-3 border-b border-white/[0.06] bg-ink-950/70 px-4 py-3 backdrop-blur-xl lg:px-7">
          <button className="rounded-lg p-1.5 text-slate-300 hover:bg-white/10 lg:hidden" onClick={() => setMobileOpen(true)}>
            <Menu size={18} />
          </button>
          <div className="flex-1" />
          {running.length > 0 ? (
            <div className="hidden items-center gap-2 rounded-full border border-sky-500/25 bg-sky-500/10 px-3 py-1 text-[11px] text-sky-200 sm:flex">
              <Spinner size={12} />
              {running.length} 个任务执行中
            </div>
          ) : null}
          <div className="flex items-center gap-2 rounded-full border border-white/[0.08] bg-white/[0.03] px-3 py-1 text-[11px] text-slate-400">
            <span className="h-1.5 w-1.5 rounded-full bg-mint-400" />
            服务运行中
          </div>
        </header>
        <main className="min-w-0 flex-1 px-4 py-5 lg:px-7 lg:py-7">
          <Outlet />
        </main>
      </div>
    </div>
  );
}
