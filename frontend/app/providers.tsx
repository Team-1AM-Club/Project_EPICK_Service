"use client";

import { QueryClientProvider } from "@tanstack/react-query";
import { Component, type ErrorInfo, type ReactNode, useState } from "react";

import { createEpikQueryClient } from "@/lib/queries/query-client";
import { AuthProvider } from "./auth-provider";
import { DevelopmentFixtureProvider } from "./development-fixtures";

export function AppProviders({ children }: { children: ReactNode }) {
  const [queryClient] = useState(createEpikQueryClient);
  const fixtureEnabled =
    process.env.NODE_ENV !== "production" &&
    process.env.NEXT_PUBLIC_EPICK_DEMO_FIXTURES === "true";
  const content = fixtureEnabled ? (
    <DevelopmentFixtureProvider>{children}</DevelopmentFixtureProvider>
  ) : (
    children
  );
  return (
    <AppErrorBoundary>
      <QueryClientProvider client={queryClient}>
        <AuthProvider>{content}</AuthProvider>
      </QueryClientProvider>
    </AppErrorBoundary>
  );
}

class AppErrorBoundary extends Component<
  { children: ReactNode },
  { error: Error | null }
> {
  state: { error: Error | null } = { error: null };

  static getDerivedStateFromError(error: Error) {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    // Production reporting belongs behind an explicit, consent-aware adapter.
    void error;
    void info;
  }

  render() {
    if (this.state.error) {
      return (
        <main role="alert" className="standard-page">
          <h1>화면을 불러오지 못했습니다.</h1>
          <p>잠시 후 다시 시도해 주세요.</p>
          <button type="button" onClick={() => window.location.reload()}>
            다시 불러오기
          </button>
        </main>
      );
    }
    return this.props.children;
  }
}
