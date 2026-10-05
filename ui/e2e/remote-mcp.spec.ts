// SPDX-License-Identifier: Apache-2.0
import { randomUUID } from "node:crypto";
import { expect, test } from "@playwright/test";
import { setSessionToken } from "./helpers/auth";

const base = process.env.BASE_URL || "";
test.skip(process.env.MCP_LOCAL_FIXTURE !== "1" || !/^http:\/\/(127\.0\.0\.1|localhost):\d+$/.test(base),
  "Requires the isolated local Docker remote_mcp_browser fixture, never production");

test("real MCP registration, review, probe, agent selection and reload", async ({ page, request }, testInfo) => {
  test.setTimeout(120_000);
  const id = randomUUID().replaceAll("-", "").slice(0, 10);
  const signup = await request.post("/api/v1/auth/signup", { data: {
    org_name: `MCP regression ${id}`, admin_name: "Local MCP Tester",
    admin_email: `mcp-${id}@example.test`, password: `Local-only-${randomUUID()}!`,
  } });
  expect(signup.status(), await signup.text()).toBe(201);
  const { access_token: token } = await signup.json();
  const headers = { Authorization: `Bearer ${token}` };
  await setSessionToken(page, token);
  await page.goto("/dashboard/connectors/remote-mcp");
  await expect(page.getByRole("heading", { name: "Remote MCP connections" })).toBeVisible();
  await page.getByLabel("Connection name", { exact: true }).fill(`mcp_browser_${id}`);
  await page.getByLabel("HTTPS MCP endpoint").fill("https://tools.example.test/mcp");
  await page.getByLabel("Bearer token", { exact: true }).fill("invalid-token");
  await page.getByRole("button", { name: "Connect and discover tools" }).click();
  await expect(page.getByRole("alert")).toContainText("connection failed");
  await page.getByLabel("Bearer token", { exact: true }).fill("synthetic-browser-mcp-token");
  await page.getByRole("button", { name: "Connect and discover tools" }).click();
  await expect(page.getByLabel("Approve read-only gnani_transcribe")).toBeVisible();
  await expect(page.getByLabel("Bearer token", { exact: true })).toHaveValue("");
  await expect(page.getByLabel("Approve read-only gnani_voice_reply")).toBeDisabled();
  await page.getByLabel("Approve read-only gnani_transcribe").check();
  await page.getByRole("button", { name: "Save read-only review" }).click();
  await expect(page.getByRole("status").filter({ hasText: "Tool review saved" })).toBeVisible();
  await page.getByLabel("Read-only connection probe").selectOption("gnani_transcribe");
  await page.getByLabel("Arguments (JSON)").fill('{"text":"Docker browser round trip"}');
  await page.getByRole("button", { name: "Run read-only probe" }).click();
  await expect(page.getByRole("status").filter({ hasText: "Read-only probe completed" })).toBeVisible();
  await expect(page.locator("pre").filter({ hasText: "Docker browser round trip" })).toBeVisible();
  await page.screenshot({ path: testInfo.outputPath("remote-mcp-desktop.png"), fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBeTruthy();
  await page.screenshot({ path: testInfo.outputPath("remote-mcp-mobile.png"), fullPage: true });
  await page.setViewportSize({ width: 1440, height: 1000 });

  const companyResponse = await request.post("/api/v1/companies", { headers, data: {
    name: `Synthetic MCP company ${id}`, pan: "TESTONLY01",
  } });
  expect(companyResponse.ok()).toBeTruthy();
  const company = await companyResponse.json();
  const companyId = company.id ?? company.company_id;
  expect(companyId).toBeTruthy();
  const create = await request.post("/api/v1/agents", { headers, data: {
    name: `MCP browser agent ${id}`, agent_type: "support_triage", domain: "ops",
    company_id: companyId, connector_ids: [], authorized_tools: [], system_prompt: "support_triage",
  } });
  expect(create.ok(), await create.text()).toBeTruthy();
  const agentId = (await create.json()).agent_id;
  await page.goto(`/dashboard/agents/${agentId}`);
  await page.getByRole("button", { name: /^config$/i }).click();
  await page.getByRole("button", { name: "Edit", exact: true }).click();
  await page.getByLabel(`mcp_browser_${id}`, { exact: true }).check();
  await page.getByLabel("gnani_transcribe Reviewed read", { exact: true }).check();
  await page.getByLabel("gnani_voice_reply Write / approval required", { exact: true }).check();
  await page.getByRole("button", { name: "Save Config", exact: true }).click();
  await expect(page.getByRole("button", { name: "Edit", exact: true })).toBeVisible();
  await page.reload();
  await page.getByRole("button", { name: /^config$/i }).click();
  await page.getByRole("button", { name: "Edit", exact: true }).click();
  await expect(page.getByLabel(`mcp_browser_${id}`, { exact: true })).toBeChecked();
  await expect(page.getByLabel("gnani_transcribe Reviewed read", { exact: true })).toBeChecked();
  await expect(page.getByLabel("gnani_voice_reply Write / approval required", { exact: true })).toBeChecked();
  const stored = await request.get(`/api/v1/agents/${agentId}`, { headers });
  expect((await stored.json()).authorized_tools).toEqual([
    `mcp_browser_${id}__gnani_transcribe`, `mcp_browser_${id}__gnani_voice_reply`,
  ]);
});
