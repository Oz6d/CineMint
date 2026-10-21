import { useState } from "react";
import { Film, KeyRound } from "lucide-react";
import { api } from "../lib/api";
import { useToast } from "../lib/toast";
import { Button, Input } from "../components/ui";

export default function Login({ onSuccess }: { onSuccess: () => void }) {
  const [password, setPassword] = useState("");
  const [loading, setLoading] = useState(false);
  const toast = useToast();

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!password) return;
    setLoading(true);
    try {
      await api.post("/api/auth/login", { password });
      onSuccess();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "登录失败");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="flex min-h-screen items-center justify-center px-4">
      <div className="w-full max-w-sm">
        <div className="mb-8 flex flex-col items-center gap-3">
          <div className="flex h-14 w-14 items-center justify-center rounded-2xl bg-gradient-to-br from-mint-500 to-sky-500 shadow-glow">
            <Film size={26} className="text-ink-950" />
          </div>
          <div className="text-center">
            <h1 className="text-xl font-semibold tracking-wide text-white">影序 CineFlow</h1>
            <p className="mt-1 text-xs text-slate-500">自托管的影视库重命名 · 整理 · 刮削</p>
          </div>
        </div>
        <form onSubmit={submit} className="glass space-y-4 p-6">
          <div>
            <label className="label">管理密码</label>
            <Input
              type="password"
              autoFocus
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              placeholder="请输入密码"
            />
          </div>
          <Button type="submit" variant="primary" className="w-full" loading={loading}>
            <KeyRound size={15} />
            登录
          </Button>
          <p className="text-center text-[11px] leading-relaxed text-slate-500">
            首次部署默认密码为 <code className="rounded bg-white/10 px-1 py-0.5 text-slate-300">cineflow</code>
            ，登录后请到「设置 → 安全」修改。
          </p>
        </form>
      </div>
    </div>
  );
}
