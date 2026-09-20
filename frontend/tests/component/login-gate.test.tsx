import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { LoginGate } from "@/app/login-gate";

describe("LoginGate", () => {
  it("renders loading, error, and signed-out states", () => {
    const { rerender } = render(<LoginGate state="loading"><p>private</p></LoginGate>);
    expect(screen.getByRole("status")).toHaveTextContent("로그인 상태를 확인");

    rerender(<LoginGate state="error"><p>private</p></LoginGate>);
    expect(screen.getByRole("alert")).toHaveTextContent("로그인 상태를 확인하지 못했습니다");

    rerender(<LoginGate state="unauthenticated"><p>private</p></LoginGate>);
    expect(screen.getByRole("link", { name: "Google로 로그인" })).toHaveAttribute(
      "href",
      expect.stringContaining("/api/v1/auth/google/start"),
    );
  });

  it("renders private children only for an authenticated session", () => {
    render(<LoginGate state="authenticated"><p>private workspace</p></LoginGate>);
    expect(screen.getByText("private workspace")).toBeInTheDocument();
  });
});
