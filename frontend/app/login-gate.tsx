"use client";

import type { ReactNode } from "react";

import { getAuthClient } from "@/lib/api/auth";
import { type AuthState, useOptionalAuth } from "./auth-provider";

export function LoginGate({
  children,
  state: stateOverride,
}: {
  children: ReactNode;
  state?: AuthState;
}) {
  const auth = useOptionalAuth();
  const state = stateOverride ?? auth?.state ?? "unauthenticated";
  if (state === "loading") {
    return <main role="status" className="standard-page">로그인 상태를 확인하고 있습니다.</main>;
  }
  if (state === "error") {
    return (
      <main role="alert" className="standard-page">
        로그인 상태를 확인하지 못했습니다. 잠시 후 다시 시도해 주세요.
      </main>
    );
  }
  if (state === "unauthenticated") {
    const loginUrl = auth?.loginUrl("/") ?? getAuthClient().loginUrl("/");
    return (
      <main className="standard-page">
        <h1>EPICK에 로그인</h1>
        <p>개인 경험과 지원 작업은 로그인한 계정에만 표시됩니다.</p>
        <a href={loginUrl}>Google로 로그인</a>
      </main>
    );
  }
  return children;
}
