/**
 * SDK launch/discovery/workflow regression coverage.
 *
 * Hermetic local test: API responses are route-mocked so the UI contract can be
 * verified without production credentials.
 */
import { expect, test, type Page, type Route } from "@playwright/test";

const APP = process.env.BASE_URL || "http://127.0.0.1:5176";

function json(route: Route, body: unknown, status = 200) {
  return route.fulfill({
    status,
    contentType: "application/json",
    body: JSON.stringify(body),
  });
}

async function setSessionCookies(page: Page) {
  const url = new URL(APP);
  const isHttps = url.protocol === "https:";
  await page.context().addCookies([
    {
      name: "agenticorg_session",
      value: "e2e-session",
      domain: url.hostname,
      path: "/",
      httpOnly: true,
      secure: isHttps,
      sameSite: "Lax",
    },
    {
      name: "agenticorg_csrf",
      value: "e2e-csrf",
      domain: url.hostname,
      path: "/",
      httpOnly: false,
      secure: isHttps,
      sameSite: "Lax",
    },
  ]);
}

async function mockApi(page: Page) {
  await page.route("**/api/v1/**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;

    if (path.endsWith("/auth/me")) {
      return json(route, {
        email: "qa@agenticorg.ai",
        name: "QA Admin",
        role: "admin",
        domain: "all",
        tenant_id: "tenant-e2e",
        onboarding_complete: true,
      });
    }

    if (path.endsWith("/companies")) {
      return json(route, {
        items: [{ id: "company-e2e", name: "E2E Company" }],
        total: 1,
      });
    }

    if (path.endsWith("/a2a/agent-card")) {
      return json(route, {
        name: "AgenticOrg",
        skills: [
          {
            id: "commerce_sales_agent",
            name: "Commerce Sales Agent",
            description: "Grantex-grounded buyer/seller discovery",
            domain: "commerce",
            tools: ["grantex_commerce:buyer_discovery_preview"],
          },
          {
            id: "contract_intelligence",
            name: "Contract Intelligence",
            description: "Contract and policy review",
            domain: "ops",
            tools: ["search_content_fulltext", "search_issues"],
          },
        ],
      });
    }

    if (path.endsWith("/a2a/agents")) {
      return json(route, {
        agents: [
          { id: "commerce_sales_agent", name: "Commerce Sales Agent", domain: "commerce" },
          { id: "contract_intelligence", name: "Contract Intelligence", domain: "ops" },
        ],
      });
    }

    if (path.endsWith("/mcp/tools")) {
      return json(route, {
        tools: [
          {
            name: "agenticorg_commerce_sales_agent",
            description: "Run the commerce seller agent",
            inputSchema: { type: "object" },
          },
          {
            name: "agenticorg_contract_intelligence",
            description: "Run contract intelligence",
            inputSchema: { type: "object" },
          },
        ],
      });
    }

    return json(route, { items: [], total: 0 });
  });

  await setSessionCookies(page);
}

test.describe("SDK launch, discovery, KB, and workflow examples", () => {
  test("Integrations page exposes current Python and TypeScript SDK contract", async ({ page }) => {
    await mockApi(page);

    await page.goto(`${APP}/dashboard/integrations`, { waitUntil: "domcontentloaded" });

    await expect(page.getByRole("heading", { name: "External Integrations" })).toBeVisible();

    const python = page.getByTestId("sdk-snippet-python");
    await expect(python).toContainText("AgentRunResult");
    await expect(python).toContainText('client.agents.run(');
    await expect(python).toContainText('"commerce_sales_agent"');
    await expect(python).toContainText(".status");
    await expect(python).toContainText(".confidence");
    await expect(python).toContainText(".output");
    await expect(python).toContainText("client.agents.generate(");
    await expect(python).toContainText("client.knowledge.search(");
    await expect(python).toContainText("client.workflows.generate(");
    await expect(python).toContainText("client.workflows.run(");

    const ts = page.getByTestId("sdk-snippet-typescript");
    await expect(ts).toContainText('new AgenticOrg({ apiKey: "your-key" })');
    await expect(ts).toContainText('client.agents.run("commerce_sales_agent"');
    await expect(ts).toContainText("client.agents.generate(");
    await expect(ts).toContainText("client.knowledge.search(");
    await expect(ts).toContainText("client.workflows.generate(");
    await expect(ts).toContainText("client.workflows.run(");

    await expect(page.locator("main")).toContainText("Commerce Sales Agent");
    await expect(page.locator("main")).toContainText("agenticorg_commerce_sales_agent");
  });
});
