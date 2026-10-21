import { useEffect, useState } from "react";
import { Navigate, Route, Routes, useNavigate } from "react-router-dom";
import Layout from "./components/Layout";
import { Spinner } from "./components/ui";
import { ApiError, api } from "./lib/api";
import Login from "./pages/Login";
import Overview from "./pages/Overview";
import Libraries from "./pages/Libraries";
import LibraryDetail from "./pages/LibraryDetail";
import Tasks from "./pages/Tasks";
import Sources from "./pages/Sources";
import Settings from "./pages/Settings";

export default function App() {
  const [state, setState] = useState<"loading" | "authed" | "guest">("loading");
  const navigate = useNavigate();

  useEffect(() => {
    let mounted = true;
    api
      .get("/api/auth/me")
      .then(() => mounted && setState("authed"))
      .catch((err) => {
        if (!mounted) return;
        if (err instanceof ApiError && err.status === 401) setState("guest");
        else setState("guest");
      });
    return () => {
      mounted = false;
    };
  }, []);

  useEffect(() => {
    const handler = () => {
      setState("guest");
      navigate("/login", { replace: true });
    };
    window.addEventListener("cineflow:unauthorized", handler);
    return () => window.removeEventListener("cineflow:unauthorized", handler);
  }, [navigate]);

  if (state === "loading") {
    return (
      <div className="flex h-screen items-center justify-center gap-3 text-sm text-slate-400">
        <Spinner size={18} />
        正在连接服务…
      </div>
    );
  }

  return (
    <Routes>
      <Route path="/login" element={state === "authed" ? <Navigate to="/" replace /> : <Login onSuccess={() => setState("authed")} />} />
      <Route element={state === "authed" ? <Layout /> : <Navigate to="/login" replace />}>
        <Route path="/" element={<Overview />} />
        <Route path="/libraries" element={<Libraries />} />
        <Route path="/libraries/:id" element={<LibraryDetail />} />
        <Route path="/tasks" element={<Tasks />} />
        <Route path="/sources" element={<Sources />} />
        <Route path="/settings" element={<Settings />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Route>
    </Routes>
  );
}
