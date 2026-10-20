// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
import { expect, test } from "@playwright/test";
import { randomBytes } from "node:crypto";

test("Telegram answer opt-in and route restoration use real owner-scoped APIs", async ({ page, baseURL }, testInfo) => {
  test.skip(!baseURL || !["127.0.0.1", "localhost", "ui"].includes(new URL(baseURL).hostname), "Disposable local Docker tenant only");
  test.setTimeout(120_000);
  const run = randomBytes(6).toString("hex");
  const signup = await page.request.post("/api/v1/auth/signup", { data: {
    org_name: `Reply test ${run}`, admin_name: "Reply Test Admin",
    admin_email: `reply-${run}@example.test`, password: randomBytes(24).toString("hex") + "aA9!",
  } });
  expect(signup.status()).toBe(201);
  const auth = await signup.json();
  const headers = { Authorization: `Bearer ${auth.access_token}` };
  expect((await page.request.put("/api/v1/org/onboarding", { headers,
    data: { onboarding_complete: true, onboarding_step: 4 } })).ok()).toBeTruthy();
  const agent = await page.request.post("/api/v1/agents", { headers, data: {
    name: `Reply agent ${run}`, agent_type: "custom", domain: "ops", initial_status: "shadow",
    system_prompt_text: "Answer synthetic tasks without tools.", authorized_tools: [],
    llm_routing: "disabled", llm: { model: "vllm:scripted/final-only" },
  } });
  expect(agent.status()).toBe(201);
  const aid = (await agent.json()).agent_id;
  expect(aid).toMatch(/^[0-9a-f-]{36}$/);
  const workflow = await page.request.post("/api/v1/workflows", { headers, data: {
    name: `Reply workflow ${run}`,
    definition: { steps: [{ id: "check", name: "Check request", type: "condition", condition: "true" }] },
  } });
  expect(workflow.status()).toBe(201);
  const wid = (await workflow.json()).workflow_id;
  const created = await page.request.post("/api/v1/channels", { headers, data: {
    name: `Private Telegram ${run}`, provider: "telegram", target_kind: "workflow", target_id: wid,
    credentials: { access_token: "123:synthetic", webhook_secret: randomBytes(32).toString("hex") }, auto_reply: true,
  } });
  expect(created.status()).toBe(201);
  const original = await created.json();
  expect(original.reply_mode).toBe("status");
  const errors: string[] = [];
  const agentRequests: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  page.on("request", (request) => {
    if (new URL(request.url()).pathname === "/api/v1/agents") agentRequests.push(request.url());
  });
  for (const viewport of [{ width: 1440, height: 1000 }, { width: 390, height: 844 }]) {
    await page.setViewportSize(viewport);
    await page.goto("/dashboard/channels");
    await page.getByRole("row", { name: new RegExp(`Private Telegram ${run}`) }).getByRole("button", { name: "Open", exact: true }).click();
    const settings = page.getByRole("form", { name: "Destination and reply settings" });
    await expect(settings.getByLabel("Reply content")).toHaveValue("status");
    await settings.getByLabel("Destination type").selectOption("agent");
    const agentSelect = settings.getByRole("combobox", { name: "Agent", exact: true });
    await expect(agentSelect.locator(`option[value="${aid}"]`)).toHaveText(`Reply agent ${run}`);
    await agentSelect.selectOption(aid);
    await settings.getByLabel("Reply content").selectOption("agent_answer");
    await expect(settings.getByRole("note")).toContainText("Final answers leave your workspace");
    await settings.getByRole("button", { name: "Save settings" }).click();
    await expect(page.getByRole("status")).toContainText("settings saved");
    await page.reload();
    await page.getByRole("row", { name: new RegExp(`Private Telegram ${run}`) }).getByRole("button", { name: "Open", exact: true }).click();
    await expect(settings.getByLabel("Reply content")).toHaveValue("agent_answer");
    await expect(page.getByLabel("Callback URL", { exact: true })).toHaveValue(original.callback_url);
    await expect(page.getByLabel("Enabled", { exact: true })).not.toBeChecked();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBeTruthy();
    await page.screenshot({ path: testInfo.outputPath(`telegram-answers-${viewport.width}.png`), fullPage: true });
    await settings.getByLabel("Destination type").selectOption("workflow");
    await settings.getByRole("combobox", { name: "Workflow", exact: true }).selectOption(wid);
    await expect(settings.getByLabel("Reply content")).toHaveValue("status");
    await settings.getByRole("button", { name: "Save settings" }).click();
    await expect(page.getByRole("status")).toContainText("settings saved");
  }
  const after = await page.request.get("/api/v1/channels", { headers });
  expect((await after.json()).bindings.find((item: { id: string }) => item.id === original.id)).toMatchObject({
    target_kind: "workflow", target_id: wid, reply_mode: "status", enabled: false,
    auto_reply: true, callback_url: original.callback_url,
  });
  expect(errors).toEqual([]);
  expect(agentRequests.some((url) => new URL(url).searchParams.get("per_page") === "100")).toBeTruthy();
});
