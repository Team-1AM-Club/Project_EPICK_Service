"use client";

import { createContext, type ReactNode, useContext } from "react";

export type DevelopmentFixture = Readonly<{
  label: string;
  enabled: true;
}>;

const FixtureContext = createContext<DevelopmentFixture | null>(null);

/**
 * Visual-development fixtures are opt-in and memory-only. They never replace
 * authenticated W1/PostgreSQL data and are not mounted in production.
 */
export function DevelopmentFixtureProvider({ children }: { children: ReactNode }) {
  return <FixtureContext.Provider value={{ label: "explicit-development-fixture", enabled: true }}>{children}</FixtureContext.Provider>;
}

export function useDevelopmentFixture() {
  return useContext(FixtureContext);
}
