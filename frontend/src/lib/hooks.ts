import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, api } from "./api";

export function useAsync<T>(path: string | null, deps: unknown[] = [], options?: { pollMs?: number }) {
  const [data, setData] = useState<T | null>(null);
  const [loading, setLoading] = useState(Boolean(path));
  const [error, setError] = useState<string | null>(null);
  const mounted = useRef(true);
  const pollMs = options?.pollMs;

  const reload = useCallback(async () => {
    if (!path) return;
    try {
      const result = await api.get<T>(path);
      if (mounted.current) {
        setData(result);
        setError(null);
      }
    } catch (err) {
      if (mounted.current) {
        if (err instanceof ApiError && err.status === 401) {
          window.dispatchEvent(new CustomEvent("cinemint:unauthorized"));
        }
        setError(err instanceof Error ? err.message : String(err));
      }
    } finally {
      if (mounted.current) setLoading(false);
    }
  }, [path]);

  useEffect(() => {
    mounted.current = true;
    void reload();
    return () => {
      mounted.current = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [path, ...deps]);

  useEffect(() => {
    if (!pollMs || !path) return;
    const timer = window.setInterval(() => void reload(), pollMs);
    return () => window.clearInterval(timer);
  }, [pollMs, reload, path]);

  return { data, loading, error, reload, setData };
}
