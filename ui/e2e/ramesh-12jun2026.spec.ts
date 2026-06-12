import { expect, Page, test } from "@playwright/test";
import { Buffer } from "node:buffer";

async function installRoutes(page: Page) {
  await page.route("**/api/v1/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname;
    const method = request.method();

    if (path.endsWith("/auth/me")) {
      await route.fulfill({
        json: {
          email: "qa@example.com",
          name: "QA Partner",
          role: "admin",
          domain: "all",
          tenant_id: "tenant-1",
          onboarding_complete: true,
        },
      });
      return;
    }

    if (path.endsWith("/product-facts")) {
      await route.fulfill({ json: { version: "test", connector_count: 3, agent_count: 1, tool_count: 20 } });
      return;
    }

    if (path.endsWith("/companies/bulk-upload/template")) {
      await route.fulfill({
        contentType: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        body: "template",
      });
      return;
    }

    if (path.endsWith("/companies/bulk-upload") && method === "POST") {
      await route.fulfill({
        json: {
          created_count: 2,
          validated_count: 2,
          failed_count: 1,
          created: [
            { row_number: 2, id: "co-1", name: "Acme Manufacturing", gstin: "29AABCU9603R1ZM" },
            { row_number: 3, id: "co-2", name: "Beta Traders", gstin: "27AABCU9603R1ZV" },
          ],
          errors: [
            { row_number: 4, identifier: "Broken Client", errors: ["row 4: gstin format is invalid"] },
          ],
        },
      });
      return;
    }

    if (path.endsWith("/companies")) {
      await route.fulfill({ json: { items: [], total: 0, page: 1, per_page: 50 } });
      return;
    }

    if (path.endsWith("/partner-dashboard")) {
      await route.fulfill({
        json: {
          total_clients: 0,
          active_clients: 0,
          inactive_clients: 0,
          avg_health_score: 0,
          total_pending_filings: 0,
          total_overdue: 0,
          revenue_per_month_inr: 0,
          clients: [],
          upcoming_deadlines: [],
        },
      });
      return;
    }

    if (path.endsWith("/connectors/traces/reconcile") && method === "POST") {
      await route.fulfill({
        json: {
          status: "reconciled",
          summary: {
            books_rows: 1,
            traces_rows: 1,
            matched: 1,
            missing_in_traces: 0,
            extra_in_traces: 0,
            amount_mismatches: 0,
            row_errors: 0,
          },
          matched: [],
          missing_in_traces: [],
          extra_in_traces: [],
          amount_mismatches: [],
          row_errors: [],
        },
      });
      return;
    }

    if (path.endsWith("/connectors/gstn/eway-bills/bulk-generate") && method === "POST") {
      await route.fulfill({
        json: {
          status: "completed",
          summary: { input_rows: 1, generated: 1, failed: 0, submitted_to_gstn: false },
          generated: [{ row_number: 1, client_reference: "INV-001", result: { status: "validated" } }],
          failed: [],
        },
      });
      return;
    }

    if (path.endsWith("/ca-capabilities/status")) {
      await route.fulfill({
        json: {
          items: [
            { id: "traces_reconciliation", label: "TRACES TDS reconciliation", status: "available_offline_reconciliation", residual: "Live portal download requires credentials." },
            { id: "eway_bill_bulk", label: "GSTN e-way bill bulk preparation", status: "available_validation_payload_generation", residual: "Live GSTN submission requires credentials." },
            { id: "professional_tax_portals", label: "Professional Tax state portal filing", status: "available_draft_and_manual_acknowledgement", residual: "Live state portal filing requires credentials." },
            { id: "client_portal", label: "Client-facing portal", status: "available_invite_token_portal", residual: "Invite-token portal is available." },
            { id: "ca_firm_billing", label: "CA-firm client billing", status: "available_invoice_and_payment_tracking", residual: "Manual payment tracking is available." },
          ],
        },
      });
      return;
    }

    if (path.endsWith("/professional-tax/states")) {
      await route.fulfill({
        json: {
          items: [
            { state_code: "KA", state: "Karnataka", portal_name: "Karnataka Professional Tax", portal_url: "https://ptax.karnataka.gov.in/", supports_online_return: true },
            { state_code: "MH", state: "Maharashtra", portal_name: "Maharashtra GST Department - Profession Tax", portal_url: "https://mahagst.gov.in/en/profession-tax", supports_online_return: true },
          ],
        },
      });
      return;
    }

    if (path.endsWith("/professional-tax/returns") && method === "GET") {
      await route.fulfill({
        json: [
          { id: "pt-1", company_id: "co-1", state_code: "KA", filing_period: "2026-06", total_payable: "400.00", status: "ready" },
        ],
      });
      return;
    }

    if (path.includes("/professional-tax/companies/") && path.includes("/registrations/") && method === "PUT") {
      await route.fulfill({ json: { id: "reg-1", company_id: "co-1", state_code: "KA", registration_number: "PT-KA-12345", status: "active", created_at: "2026-06-12T00:00:00Z" } });
      return;
    }

    if (path.endsWith("/professional-tax/returns/prepare") && method === "POST") {
      await route.fulfill({
        json: {
          id: "pt-1",
          company_id: "co-1",
          state_code: "KA",
          filing_period: "2026-06",
          employee_count: 2,
          gross_wages: "110000.00",
          pt_amount: "400.00",
          interest: "0.00",
          penalty: "0.00",
          total_payable: "400.00",
          status: "ready",
          line_items: [],
          payload: { total_payable: "400.00" },
          portal_response: {},
          created_at: "2026-06-12T00:00:00Z",
        },
      });
      return;
    }

    if (path.includes("/professional-tax/returns/") && path.endsWith("/submit") && method === "POST") {
      await route.fulfill({
        json: {
          id: "pt-1",
          company_id: "co-1",
          state_code: "KA",
          filing_period: "2026-06",
          total_payable: "400.00",
          status: "ready_for_manual_upload",
          line_items: [],
          payload: {},
          portal_response: { status: "ready_for_manual_upload" },
          created_at: "2026-06-12T00:00:00Z",
        },
      });
      return;
    }

    if (path.endsWith("/client-portal/invites") && method === "GET") {
      await route.fulfill({ json: [{ id: "invite-1", company_id: "co-1", client_email: "client@example.com", client_name: "Client CFO", status: "pending", expires_at: "2026-06-26T00:00:00Z", created_at: "2026-06-12T00:00:00Z" }] });
      return;
    }

    if (path.endsWith("/client-portal/invites") && method === "POST") {
      await route.fulfill({ json: { id: "invite-1", company_id: "co-1", client_email: "client@example.com", client_name: "Client CFO", status: "pending", expires_at: "2026-06-26T00:00:00Z", created_at: "2026-06-12T00:00:00Z", invite_token: "aocpi_testtoken" } });
      return;
    }

    if (path.endsWith("/client-portal/documents") && method === "GET") {
      await route.fulfill({ json: [{ id: "doc-1", company_id: "co-1", title: "June Compliance Pack", document_type: "compliance_report", filing_period: "2026-06", status: "published", visible_to_client: true, created_at: "2026-06-12T00:00:00Z" }] });
      return;
    }

    if (path.endsWith("/client-portal/documents") && method === "POST") {
      await route.fulfill({ json: { id: "doc-1", company_id: "co-1", title: "June Compliance Pack", document_type: "compliance_report", filing_period: "2026-06", status: "published", visible_to_client: true, created_at: "2026-06-12T00:00:00Z" } });
      return;
    }

    if (path.endsWith("/ca-billing/service-plans") && method === "GET") {
      await route.fulfill({ json: [{ id: "plan-1", name: "Monthly Compliance Retainer", currency: "INR", default_fee: "15000.00", billing_cycle: "monthly", tax_rate_percent: "18", active: true, created_at: "2026-06-12T00:00:00Z" }] });
      return;
    }

    if (path.endsWith("/ca-billing/service-plans") && method === "POST") {
      await route.fulfill({ json: { id: "plan-1", name: "Monthly Compliance Retainer", currency: "INR", default_fee: "15000.00", billing_cycle: "monthly", tax_rate_percent: "18", active: true, created_at: "2026-06-12T00:00:00Z" } });
      return;
    }

    if (path.endsWith("/ca-billing/invoices") && method === "GET") {
      await route.fulfill({ json: [{ id: "inv-1", company_id: "co-1", invoice_number: "CA-202606-0001", issue_date: "2026-06-12", due_date: "2026-06-27", currency: "INR", total: "17700.00", balance_due: "17700.00", status: "sent", line_items: [] }] });
      return;
    }

    if (path.endsWith("/ca-billing/invoices") && method === "POST") {
      await route.fulfill({ json: { id: "inv-1", company_id: "co-1", invoice_number: "CA-202606-0001", issue_date: "2026-06-12", due_date: "2026-06-27", currency: "INR", total: "17700.00", balance_due: "17700.00", status: "sent", line_items: [] } });
      return;
    }

    if (path.includes("/ca-billing/invoices/") && path.endsWith("/send") && method === "POST") {
      await route.fulfill({ json: { id: "inv-1", company_id: "co-1", invoice_number: "CA-202606-0001", issue_date: "2026-06-12", due_date: "2026-06-27", currency: "INR", total: "17700.00", balance_due: "17700.00", status: "sent", line_items: [] } });
      return;
    }

    if (path.includes("/ca-billing/invoices/") && path.endsWith("/payments") && method === "POST") {
      await route.fulfill({ json: { id: "inv-1", company_id: "co-1", invoice_number: "CA-202606-0001", issue_date: "2026-06-12", due_date: "2026-06-27", currency: "INR", total: "17700.00", balance_due: "0.00", status: "paid", line_items: [] } });
      return;
    }

    await route.fulfill({ json: {} });
  });
}

test.describe("Ramesh 12 Jun 2026 CA feedback", () => {
  test("Companies page uploads bulk clients and shows row-level report", async ({ page, baseURL }) => {
    await installRoutes(page);

    await page.goto(`${baseURL}/dashboard/companies`, { waitUntil: "domcontentloaded" });
    await expect(page.getByRole("button", { name: /Upload Clients/ })).toBeVisible();

    await page.getByTestId("company-bulk-upload-input").setInputFiles({
      name: "clients.csv",
      mimeType: "text/csv",
      buffer: Buffer.from("Company Name,GSTIN,PAN,State\nAcme,29AABCU9603R1ZM,AABCU9603R,KA\n"),
    });

    await expect(page.getByTestId("bulk-upload-report")).toContainText("2 created");
    await expect(page.getByTestId("bulk-upload-report")).toContainText("1 failed");
    await expect(page.getByTestId("bulk-upload-report")).toContainText("gstin format is invalid");
  });

  test("CA Operations page exercises TRACES and e-way bill endpoints", async ({ page, baseURL }) => {
    await installRoutes(page);

    await page.goto(`${baseURL}/dashboard/ca-operations`, { waitUntil: "domcontentloaded" });
    await expect(page.locator("main")).toContainText("TRACES Reconciliation");
    await expect(page.locator("main")).toContainText("Professional Tax state portal filing");
    await expect(page.locator("main")).toContainText("available draft and manual acknowledgement");

    await page.getByRole("button", { name: "Reconcile" }).click();
    await expect(page.locator("main")).toContainText('"matched": 1');

    await page.getByRole("button", { name: "Validate Batch" }).click();
    await expect(page.locator("main")).toContainText('"generated": 1');
    await expect(page.locator("main")).toContainText('"submitted_to_gstn": false');
  });

  test("Professional Tax, Client Portal, and CA Billing pages exercise CAFEAT 004 005 006", async ({ page, baseURL }) => {
    await installRoutes(page);

    await page.goto(`${baseURL}/dashboard/professional-tax`, { waitUntil: "domcontentloaded" });
    await expect(page.locator("main")).toContainText("Professional Tax");
    await page.getByPlaceholder("Company ID").fill("co-1");
    await page.getByPlaceholder("PT registration number").fill("PT-KA-12345");
    await page.getByRole("button", { name: /Prepare Return/ }).click();
    await expect(page.locator("main")).toContainText('"total_payable": "400.00"');

    await page.goto(`${baseURL}/dashboard/client-portal`, { waitUntil: "domcontentloaded" });
    await page.getByPlaceholder("Company ID").fill("co-1");
    await page.getByPlaceholder("Client email").fill("client@example.com");
    await page.getByRole("button", { name: /Create Invite/ }).click();
    await expect(page.getByTestId("client-portal-token")).toContainText("aocpi_testtoken");
    await page.getByPlaceholder("Document title").fill("June Compliance Pack");
    await page.getByRole("button", { name: /^Publish$/ }).click();
    await expect(page.locator("main")).toContainText("June Compliance Pack");

    await page.goto(`${baseURL}/dashboard/ca-billing`, { waitUntil: "domcontentloaded" });
    await page.getByPlaceholder("Company ID").fill("co-1");
    await page.getByRole("button", { name: /Create & Send/ }).click();
    await expect(page.locator("main")).toContainText("CA-202606-0001");
    await expect(page.locator("main")).toContainText("INR 17700.00");
  });
});
