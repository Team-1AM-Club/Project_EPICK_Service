"use client";

import type { ReactNode } from "react";
import Image from "next/image";

import { getAuthClient } from "@/lib/api/auth";
import { type AuthState, useOptionalAuth } from "./auth-provider";
import styles from "./login-gate.module.css";

type LoginFrameProps = Readonly<{
  children: ReactNode;
  role?: "alert" | "status";
}>;

function LoginFrame({ children, role }: LoginFrameProps) {
  return (
    <div className={styles.page}>
      <header className={styles.header}>
        <Image
          className={styles.brand}
          src="/figma/epick.svg"
          alt="EPICK"
          width={104}
          height={27}
          priority
        />
      </header>
      <main className={styles.layout}>
        <section className={styles.panel} role={role} aria-live={role ? "polite" : undefined}>
          <div className={styles.topline} aria-hidden="true" />
          {children}
        </section>
      </main>
      <footer className={styles.footer}>© 2026 EPICK</footer>
    </div>
  );
}

function ShieldNote() {
  return (
    <div className={styles.footnote}>
      <svg viewBox="0 0 24 24" fill="none" aria-hidden="true">
        <path
          d="M12 3 4.5 6v5c0 4.5 3 7.7 7.5 10 4.5-2.3 7.5-5.5 7.5-10V6L12 3Z"
          stroke="currentColor"
          strokeWidth="1.4"
          strokeLinejoin="round"
        />
        <path
          d="m8.5 12 2.5 2.5 4.5-5"
          stroke="currentColor"
          strokeWidth="1.4"
          strokeLinecap="round"
          strokeLinejoin="round"
        />
      </svg>
      <p>
        당신의 이야기에 집중할 수 있도록.
        <br />
        <span>경험의 기록부터 소재의 발견까지, EPICK.</span>
      </p>
    </div>
  );
}

function GoogleMark() {
  return (
    <svg className={styles.googleMark} viewBox="0 0 48 48" aria-hidden="true">
      <path
        fill="#EA4335"
        d="M24 9.5c3.54 0 6.71 1.22 9.21 3.6l6.85-6.85C35.9 2.38 30.47 0 24 0 14.62 0 6.51 5.38 2.56 13.22l7.98 6.19C12.43 13.72 17.74 9.5 24 9.5Z"
      />
      <path
        fill="#4285F4"
        d="M46.98 24.55c0-1.57-.15-3.09-.38-4.55H24v9.02h12.94c-.58 2.96-2.26 5.48-4.78 7.18l7.73 6C44.4 38.04 46.98 31.88 46.98 24.55Z"
      />
      <path
        fill="#FBBC05"
        d="M10.53 28.59A14.4 14.4 0 0 1 9.77 24c0-1.59.27-3.13.76-4.59l-7.98-6.19A23.87 23.87 0 0 0 0 24c0 3.87.93 7.54 2.56 10.78l7.97-6.19Z"
      />
      <path
        fill="#34A853"
        d="M24 48c6.48 0 11.93-2.13 15.91-5.8l-7.73-6c-2.15 1.45-4.92 2.3-8.18 2.3-6.26 0-11.57-4.22-13.47-9.91l-7.98 6.19C6.51 42.62 14.62 48 24 48Z"
      />
    </svg>
  );
}

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
    return (
      <LoginFrame role="status">
        <h1 className={styles.title}>로그인 상태를{" "}<br />확인하고 있어요</h1>
        <p className={styles.description}>안전한 개인 작업공간을 준비하고 있습니다.</p>
        <div className={styles.progress} aria-hidden="true"><span /></div>
      </LoginFrame>
    );
  }

  if (state === "error") {
    return (
      <LoginFrame role="alert">
        <h1 className={styles.title}>잠시 연결이<br />지연되고 있어요</h1>
        <p className={styles.description}>로그인 상태를 확인하지 못했습니다.</p>
        <button className={styles.retryButton} type="button" onClick={() => window.location.reload()}>
          다시 시도하기
        </button>
      </LoginFrame>
    );
  }

  if (state === "unauthenticated") {
    const loginUrl = auth?.loginUrl("/") ?? getAuthClient().loginUrl("/");
    return (
      <LoginFrame>
        <h1 className={styles.title}>나의 다음을<br />시작해 볼까요?</h1>
        <a className={styles.googleButton} href={loginUrl} aria-label="Google로 로그인">
          <GoogleMark />
          <span>Google로 계속하기</span>
          <svg className={styles.arrow} viewBox="0 0 24 24" fill="none" aria-hidden="true">
            <path
              d="M5 12h14m-5-5 5 5-5 5"
              stroke="currentColor"
              strokeWidth="1.5"
              strokeLinecap="round"
              strokeLinejoin="round"
            />
          </svg>
        </a>
        <div className={styles.divider} />
        <ShieldNote />
      </LoginFrame>
    );
  }

  return children;
}
