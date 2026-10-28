import { useQuery, useQueryClient } from "@tanstack/react-query";
import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import type { ReactNode } from "react";

import { ApiError, api, onSessionExpired, setCsrfToken } from "../api/client";
import type { Me } from "../api/types";

interface AuthState {
  me: Me | null;
  loading: boolean;
  /** Set when the session ended without the user logging out. */
  expired: boolean;
  login: (email: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
}

const AuthContext = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const qc = useQueryClient();
  const [expired, setExpired] = useState(false);
  const meQuery = useQuery({
    queryKey: ["me"],
    queryFn: async () => {
      try {
        return await api<Me>("/api/auth/me");
      } catch (err) {
        if (err instanceof ApiError && err.status === 401) return null;
        throw err;
      }
    },
    staleTime: Infinity,
    retry: (count, err) => !(err instanceof ApiError && err.status < 500) && count < 3,
  });
  const me = meQuery.data ?? null;

  useEffect(() => setCsrfToken(me?.csrf_token ?? null), [me]);

  const endSession = useCallback(() => {
    setCsrfToken(null);
    // Never show the previous user's cached data to the next one. The "me" query
    // is kept (and set to null) because the app's observers are subscribed to it.
    qc.removeQueries({ predicate: (q) => q.queryKey[0] !== "me" });
    qc.setQueryData(["me"], null);
  }, [qc]);

  useEffect(
    () =>
      onSessionExpired(() => {
        if (qc.getQueryData(["me"])) setExpired(true);
        endSession();
      }),
    [qc, endSession],
  );

  const login = useCallback(
    async (email: string, password: string) => {
      const result = await api<Me>("/api/auth/login", {
        method: "POST",
        body: { email, password },
      });
      setCsrfToken(result.csrf_token);
      setExpired(false);
      qc.removeQueries({ predicate: (q) => q.queryKey[0] !== "me" });
      qc.setQueryData(["me"], result);
    },
    [qc],
  );

  const logout = useCallback(async () => {
    try {
      await api("/api/auth/logout", { method: "POST" });
    } finally {
      setExpired(false);
      endSession();
    }
  }, [endSession]);

  const value = useMemo(
    () => ({ me, loading: meQuery.isPending, expired, login, logout }),
    [me, meQuery.isPending, expired, login, logout],
  );
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthState {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth outside AuthProvider");
  return ctx;
}
