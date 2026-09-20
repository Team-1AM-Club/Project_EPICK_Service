"use client";

import { useQueryClient } from "@tanstack/react-query";
import {
  createContext,
  type ReactNode,
  useContext,
  useEffect,
  useMemo,
  useState,
  useSyncExternalStore,
} from "react";

import { getAuthClient } from "@/lib/api/auth";
import { clearPrivateQueryState } from "@/lib/queries/query-client";
import { authStore } from "@/lib/state/auth-store";

export type AuthState = "loading" | "authenticated" | "unauthenticated" | "error";

type AuthContextValue = Readonly<{
  state: AuthState;
  loginUrl: (returnTo?: string) => string;
  logout: () => Promise<void>;
}>;

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const queryClient = useQueryClient();
  const snapshot = useSyncExternalStore(
    authStore.subscribe,
    authStore.getSnapshot,
    authStore.getSnapshot,
  );
  const [bootstrapState, setBootstrapState] = useState<AuthState>("loading");

  useEffect(() => {
    const controller = new AbortController();
    getAuthClient()
      .refresh()
      .then((token) => {
        if (!controller.signal.aborted) {
          setBootstrapState(token ? "authenticated" : "unauthenticated");
        }
      })
      .catch(() => {
        if (!controller.signal.aborted) setBootstrapState("error");
      });
    return () => controller.abort();
  }, []);

  const value = useMemo<AuthContextValue>(
    () => ({
      state: snapshot.accessToken ? "authenticated" : bootstrapState,
      loginUrl: (returnTo = "/") => getAuthClient().loginUrl(returnTo),
      logout: async () => {
        await queryClient.cancelQueries();
        await getAuthClient().logout();
        await clearPrivateQueryState(queryClient);
        setBootstrapState("unauthenticated");
      },
    }),
    [bootstrapState, queryClient, snapshot.accessToken],
  );
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const value = useContext(AuthContext);
  if (!value) throw new Error("useAuth must be used inside AuthProvider");
  return value;
}

export function useOptionalAuth(): AuthContextValue | null {
  return useContext(AuthContext);
}
