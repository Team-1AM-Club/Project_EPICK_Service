import { expect, test } from "@playwright/test";

test.describe("authenticated private workspace", () => {
  test.skip(
    !process.env.EPICK_E2E_AUTH_FIXTURE,
    "Requires the deterministic W1 OIDC/PostgreSQL fixture server.",
  );

  test("isolates two owners and revokes logout", async ({ browser }) => {
    const ownerOne = await browser.newContext({ extraHTTPHeaders: { "X-Test-Identity": "owner-1" } });
    const ownerTwo = await browser.newContext({ extraHTTPHeaders: { "X-Test-Identity": "owner-2" } });
    const firstPage = await ownerOne.newPage();
    const secondPage = await ownerTwo.newPage();
    await Promise.all([firstPage.goto("/"), secondPage.goto("/")]);
    await expect(firstPage.getByText("owner-1 workspace")).toBeVisible();
    await expect(secondPage.getByText("owner-2 workspace")).toBeVisible();
    await expect(firstPage.getByText("owner-2 workspace")).toHaveCount(0);
    await firstPage.getByRole("button", { name: "로그아웃" }).click();
    await expect(firstPage.getByRole("link", { name: "Google로 로그인" })).toBeVisible();
    await Promise.all([ownerOne.close(), ownerTwo.close()]);
  });
});
