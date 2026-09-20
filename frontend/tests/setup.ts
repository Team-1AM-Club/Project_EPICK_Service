import "@testing-library/jest-dom/vitest";
import { afterAll, afterEach, beforeAll } from "vitest";

import { mockApiServer } from "./mocks/server";

process.env.NEXT_PUBLIC_W1_API_URL ??= "http://localhost:8000";

beforeAll(() => mockApiServer.listen({ onUnhandledRequest: "error" }));
afterEach(() => mockApiServer.resetHandlers());
afterAll(() => mockApiServer.close());

afterEach(() => {
  window.localStorage.clear();
  window.sessionStorage.clear();
});
