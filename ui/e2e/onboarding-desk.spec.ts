// SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
/**
 * The merchant onboarding desk, end to end on the development stack
 * (`make e2e-onboarding-desk`, docs/onboarding-desk/).
 *
 * Nothing in the desk is stubbed: the partner's signed webhook, the consent
 * ledger, the contact rules, the applicant page and its document reading, the
 * case the desk opens, the checks against the mock verification provider, the
 * approval on the Grantex auth service's own page, the signed governed-case
 * push, the signed evidence pack and the partner report are the stack's own.
 * The suite plays the partner (its form and its push receiver), the applicant
 * and the reviewer.
 *
 * It runs in two parts with a seed step between them
 * (scripts/seed_onboarding_desk.py), because the checks need the case agents'
 * development root grant, which only a seed process holds:
 *
 * - `@applicant`: a lead arrives with its consent, gets a link, applies in the
 *   browser and submits; the desk opens the governed case.
 * - (seed step) the checks run and the case reaches `awaiting_decision`.
 * - `@decision`: a person approves on the issuer's page, the console records
 *   the decision, the partner receives the signed push, and the application's
 *   evidence pack is signed with the key the desk publishes.
 *
 * `make e2e-onboarding-desk` then checks the saved pack with the desk's own
 * offline verifier (scripts/verify_onboarding_evidence.py).
 */
import { createHmac, createPublicKey, verify as verifySignature } from "node:crypto";
import { existsSync, readFileSync, writeFileSync } from "node:fs";
import { createServer, type IncomingHttpHeaders } from "node:http";
import path from "node:path";
import { expect, test, type APIRequestContext, type Page } from "@playwright/test";
import {
  allowListApproverIdp,
  APPROVER_ONE,
  approveOnTheIssuer,
  consoleHeaders,
  refreshStatus,
  requestDecisionInTheConsole,
} from "./helpers/decision-grants";
import { signIn } from "./helpers/governed-cases";

// Playwright runs from `ui/`, so the repository root is its parent.
const REPO_ROOT = path.resolve(process.cwd(), "..");
const SEED_PATH = path.resolve(REPO_ROOT, process.env.ONBOARDING_DESK_SEED || "");
const RUN_PATH = path.resolve(REPO_ROOT, process.env.ONBOARDING_DESK_RUN || "");
// What `make e2e-onboarding-desk` hands to the desk's offline verifier after this suite.
const EVIDENCE_PACK_PATH = path.resolve(REPO_ROOT, "ui/test-results/onboarding-desk-evidence-pack.json");
const EVIDENCE_KEY_PATH = path.resolve(REPO_ROOT, "ui/test-results/onboarding-desk-evidence-key.txt");
const CERTIFICATE = path.resolve(process.cwd(), "e2e/fixtures/onboarding-desk/certificate-of-incorporation.pdf");
const DESK = "/api/v1/onboarding-desk";
const PUSH_PORT = 3099;

interface Seed {
  tenant_id: string;
  partner_name: string;
  lead_source: { id: string; webhook_path: string; signing_secret: string };
  push: { url: string; key_id: string; secret: string };
}

interface Run {
  tenant_id: string;
  external_ref: string;
  lead_id: string;
  application_id: string;
  case_ref: string;
  investigation?: { state: string; tier?: string };
}

function seed(): Seed {
  if (!existsSync(SEED_PATH)) throw new Error(`no onboarding desk seed at ${SEED_PATH}: run \`make e2e-onboarding-desk\``);
  return JSON.parse(readFileSync(SEED_PATH, "utf-8")) as Seed;
}

/** The partner form's signature: `t=<unix seconds>,v1=<hex HMAC-SHA256 of "<t>." + body>` (foundations.md). */
function webhookSignature(secret: string, body: string): string {
  const t = Math.floor(Date.now() / 1000);
  return `t=${t},v1=${createHmac("sha256", secret).update(`${t}.${body}`).digest("hex")}`;
}

async function okJson<T>(response: Awaited<ReturnType<APIRequestContext["get"]>>): Promise<T> {
  expect(response.ok(), `${response.url()} answered ${response.status()}: ${await response.text()}`).toBeTruthy();
  return (await response.json()) as T;
}

/** The answer card for one application item on the applicant page (inside its shadow root). */
function card(page: Page, item: string) {
  return page.locator(`agenticorg-onboarding section[data-item="${item}"]`);
}

async function save(page: Page, item: string): Promise<void> {
  await card(page, item).getByRole("button", { name: "Save", exact: true }).click();
}

/** A delivery the receiver took: the raw body and the headers the signature covers. */
interface Delivery {
  headers: IncomingHttpHeaders;
  body: string;
}

/**
 * The governed-case push signature: each `v1=<key id>:<hex>` entry is HMAC-SHA256, keyed by that
 * key's secret, of `"<event id>.<timestamp>."` followed by the raw body (case-hand-off.md).
 */
function pushSignatureValid(delivery: Delivery, keyId: string, secret: string): boolean {
  const eventId = String(delivery.headers["agenticorg-event-id"] || "");
  const timestamp = String(delivery.headers["agenticorg-timestamp"] || "");
  const entries = String(delivery.headers["agenticorg-signature"] || "").split(",").map((s) => s.trim());
  const expected = createHmac("sha256", secret).update(`${eventId}.${timestamp}.`).update(delivery.body).digest("hex");
  return entries.some((entry) => entry === `v1=${keyId}:${expected}`);
}

/** Python's `json.dumps(sort_keys=True, separators=(",", ":"), ensure_ascii=False)` for a manifest of strings. */
function canonical(value: unknown): string {
  if (value === null || typeof value !== "object") return JSON.stringify(value);
  if (Array.isArray(value)) return `[${value.map(canonical).join(",")}]`;
  const entries = Object.keys(value as Record<string, unknown>)
    .sort()
    .map((key) => `${JSON.stringify(key)}:${canonical((value as Record<string, unknown>)[key])}`);
  return `{${entries.join(",")}}`;
}

test.describe("the merchant onboarding desk, end to end", () => {
  test("@applicant a consented lead applies on the partner's page and the desk opens the case", async ({
    browser,
    page,
    request,
  }) => {
    const seeded = seed();
    const stamp = Date.now();
    const externalRef = `e2e-desk-${stamp}`;

    // 1. The partner's web form posts the lead, with the consent it collected, signed.
    const lead = JSON.stringify({
      business_name: "Maple Leaf Example Bakery Inc.",
      contact_name: "Maria Example",
      email: `maria.${stamp}@example.com`,
      phone: "+1 416 555 0142",
      country: "CA",
      region: "ON",
      locale: "en",
      external_ref: externalRef,
      consents: ["sms", "email"].map((channel) => ({
        channel,
        wording: "Yes, Northwind Payments may contact me about accepting card payments. Reply STOP to opt out.",
        method: "web_form",
        source_ref: "https://northwind.example.com/apply",
        captured_at: new Date(stamp - 60_000).toISOString(),
      })),
    });
    const unsigned = await request.post(seeded.lead_source.webhook_path, {
      headers: { "content-type": "application/json" },
      data: lead,
    });
    expect(unsigned.status(), "an unsigned delivery is refused").toBe(401);
    const delivered = await request.post(seeded.lead_source.webhook_path, {
      headers: { "content-type": "application/json", "x-agenticorg-signature": webhookSignature(seeded.lead_source.signing_secret, lead) },
      data: lead,
    });
    expect(await okJson<{ created: number }>(delivered)).toMatchObject({ created: 1 });

    // 2. The partner's staff see the lead, its consents and what the contact rules allow.
    await signIn(page);
    const leads = await okJson<{ leads: { id: string; external_ref: string }[] }>(await page.request.get(`${DESK}/leads`));
    const leadId = leads.leads.find((row) => row.external_ref === externalRef)?.id;
    expect(leadId, "the webhook lead is listed").toBeTruthy();
    const view = await okJson<{ consents: { channel: string; method: string; status: string }[]; email: string }>(
      await page.request.get(`${DESK}/leads/${leadId}`),
    );
    expect(view.consents.map((c) => `${c.channel}:${c.method}:${c.status}`).sort()).toEqual([
      "email:web_form:granted",
      "sms:web_form:granted",
    ]);
    // Staff see a masked address; the full one is encrypted under the tenant key.
    expect(view.email).not.toContain(`maria.${stamp}`);
    const sms = await okJson<{ allowed: boolean; reason: string }>(
      await page.request.get(`${DESK}/leads/${leadId}/contact-decision?channel=sms`),
    );
    // Allowed inside the lead's permitted hours, held until they open outside them.
    expect(["allowed", "outside_hours"]).toContain(sms.reason);
    const whatsapp = await okJson<{ allowed: boolean; reason: string }>(
      await page.request.get(`${DESK}/leads/${leadId}/contact-decision?channel=whatsapp`),
    );
    expect(whatsapp).toMatchObject({ allowed: false, reason: "no_consent" });

    // 3. The partner hands the applicant a signed link (nothing is sent by the desk).
    const link = await okJson<{ token: string; expires_at: string }>(
      await page.request.post(`${DESK}/leads/${leadId}/apply-link`, { headers: await consoleHeaders(page) }),
    );

    // 4. The applicant, in a browser of their own, applies on the partner-branded page.
    const applicantContext = await browser.newContext();
    try {
      const applicant = await applicantContext.newPage();
      await applicant.goto(`/onboarding-desk/apply?t=${encodeURIComponent(link.token)}`);
      await expect(applicant.getByRole("heading", { name: `Apply with ${seeded.partner_name}` })).toBeVisible();
      // The link token leaves the address bar as soon as the page has read it.
      await expect.poll(() => new URL(applicant.url()).searchParams.has("t")).toBe(false);
      await expect(applicant.locator("agenticorg-onboarding")).toContainText("An AI assistant helps with this application");

      // A document is read and its values are suggested, then confirmed by the applicant.
      const formation = card(applicant, "formation_document");
      await formation.locator('input[type="file"]').setInputFiles(CERTIFICATE);
      await formation.getByRole("button", { name: "Upload", exact: true }).click();
      const suggestions = applicant.locator("agenticorg-onboarding .suggest:not(.hidden)");
      await expect(suggestions).toContainText("Maple Leaf Example Bakery Inc.", { timeout: 45_000 });
      await expect(suggestions).toContainText("000000001");
      await expect(suggestions).toContainText("0000001");
      await suggestions.getByRole("button", { name: "Use selected" }).click();
      await expect(card(applicant, "legal_name").locator("input")).toHaveValue("Maple Leaf Example Bakery Inc.");

      await card(applicant, "business_structure").locator("select").selectOption("corporation");
      await save(applicant, "business_structure");
      await card(applicant, "activity").locator("select").selectOption("food_service");
      await save(applicant, "activity");

      // An answer the desk refuses comes back with the reason, in the applicant's language.
      const address = card(applicant, "registered_address").locator("input");
      await address.nth(0).fill("100 Example Street");
      await address.nth(2).fill("Toronto");
      await address.nth(3).fill("ON");
      await address.nth(4).fill("90210");
      await save(applicant, "registered_address");
      await expect(card(applicant, "registered_address").locator(".error")).toHaveText("That postal code doesn't look right.");
      await address.nth(4).fill("A0A 0A0");
      await save(applicant, "registered_address");
      await expect(card(applicant, "registered_address").locator(".note")).toHaveText("Saved");

      const owners = card(applicant, "owners");
      await owners.locator("input").nth(0).fill("Maria Example");
      await owners.locator("select").nth(1).selectOption("owner_and_controller");
      await owners.locator("input").nth(1).fill("100");
      await owners.locator("input").nth(2).fill("1980-04");
      await save(applicant, "owners");
      await expect(owners.locator(".note")).toHaveText("Saved");

      await card(applicant, "website").locator("input").fill("https://mapleleaf-bakery.example.com");
      await save(applicant, "website");

      const submit = applicant.locator('agenticorg-onboarding [data-role="submit"]');
      await expect(submit).toBeEnabled();
      await submit.click();
      await expect(applicant.locator("agenticorg-onboarding")).toContainText(
        `Your application is submitted. ${seeded.partner_name} will be in touch.`,
      );
    } finally {
      await applicantContext.close();
    }

    // 5. The desk opens the governed case on submission (auto_open, on the worker).
    const listed = await okJson<{ applications: { id: string; lead_id: string }[] }>(await page.request.get(`${DESK}/applications`));
    const applicationId = listed.applications.find((row) => row.lead_id === leadId)?.id;
    expect(applicationId, "the application is listed for the partner").toBeTruthy();
    let caseRef = "";
    await expect
      .poll(
        async () => {
          const { applications } = await okJson<{
            applications: { id: string; case_ref: string | null; case_error: string | null }[];
          }>(await page.request.get(`${DESK}/applications`));
          const summary = applications.find((row) => row.id === applicationId);
          expect(summary, "the application is still listed").toBeTruthy();
          expect(summary!.case_error, "the case was refused").toBeNull();
          caseRef = summary!.case_ref || "";
          return caseRef;
        },
        { message: "the desk opens the governed case", timeout: 90_000, intervals: [1_000, 2_000, 5_000] },
      )
      .toMatch(/^case_/);

    const run: Run = { tenant_id: seeded.tenant_id, external_ref: externalRef, lead_id: leadId!, application_id: applicationId!, case_ref: caseRef };
    writeFileSync(RUN_PATH, `${JSON.stringify(run, null, 2)}\n`, "utf-8");
  });

  test("@decision a person approves on the issuer's page, the partner gets the signed push and the evidence is signed", async ({
    browser,
    page,
    request,
  }) => {
    const seeded = seed();
    expect(existsSync(RUN_PATH), `no run at ${RUN_PATH}: the @applicant part runs first`).toBeTruthy();
    const run = JSON.parse(readFileSync(RUN_PATH, "utf-8")) as Run;
    expect(run.investigation?.state, "the seed step's checks brought the case to a decision").toBe("awaiting_decision");

    // The partner's push receiver: it answers 2xx to everything and keeps what it was sent.
    const deliveries: Delivery[] = [];
    const receiver = createServer((req, res) => {
      const chunks: Buffer[] = [];
      req.on("data", (chunk: Buffer) => chunks.push(chunk));
      req.on("end", () => {
        if (req.url === new URL(seeded.push.url).pathname) deliveries.push({ headers: req.headers, body: Buffer.concat(chunks).toString("utf-8") });
        res.writeHead(204).end();
      });
    });
    await new Promise<void>((resolve) => receiver.listen(PUSH_PORT, "0.0.0.0", resolve));
    try {
      await allowListApproverIdp(request);

      // The reviewer asks for the decision the memo recommends; one person approves it on the issuer's page.
      await signIn(page, undefined, `/dashboard/approvals/cases/${run.case_ref}`);
      await expect(page.getByTestId("case-state")).toContainText("Awaiting decision", { timeout: 20_000 });
      const created = await requestDecisionInTheConsole(
        page,
        "approve",
        "The registry record, the Business Number and the declared owner match the application.",
      );
      expect(created.approvalsRequired).toBe(1);
      const approval = await approveOnTheIssuer(browser, created.requestId, APPROVER_ONE);
      expect(approval.title, approval.body).toBe("Approved");
      await refreshStatus(page);
      await expect(page.getByTestId("record-decision")).toBeEnabled();
      const [recorded] = await Promise.all([
        page.waitForResponse((r) => /\/decision$/.test(r.url()) && r.request().method() === "POST"),
        page.getByTestId("record-decision").click(),
      ]);
      expect(recorded.status(), await recorded.text()).toBe(200);

      // The desk shows the decided case against the application.
      const desk = await okJson<{ lead: { external_ref: string }; case: { state: string; decision: string } }>(
        await page.request.get(`${DESK}/cases/${run.case_ref}`),
      );
      expect(desk.lead.external_ref).toBe(run.external_ref);
      expect(desk.case).toMatchObject({ state: "decided", decision: "approve" });

      // The partner receives the decided case, signed with the key it holds.
      await expect
        .poll(() => deliveries.filter((d) => d.headers["agenticorg-event-type"] === "case.decided").length, {
          message: "the decided case is pushed to the partner",
          timeout: 60_000,
        })
        .toBeGreaterThan(0);
      for (const delivery of deliveries) {
        expect(pushSignatureValid(delivery, seeded.push.key_id, seeded.push.secret), "every push carries a valid signature").toBe(true);
        expect(delivery.body).toContain(run.case_ref);
      }
      const decided = deliveries.find((d) => d.headers["agenticorg-event-type"] === "case.decided")!;
      expect(JSON.parse(decided.body)).toMatchObject({ event_type: "case.decided", event_id: decided.headers["agenticorg-event-id"] });
      const outbox = await okJson<{ deliveries: { event_type: string; status: string }[] }>(
        await page.request.get(`/api/v1/governed-cases/${run.case_ref}/push-deliveries`),
      );
      expect(outbox.deliveries.map((d) => d.event_type)).toEqual(expect.arrayContaining(["case.completed", "case.decided"]));
      expect(outbox.deliveries.find((d) => d.event_type === "case.decided")?.status).toBe("delivered");

      // The evidence pack: signed with the key the desk publishes, covering every section.
      const key = await okJson<{ alg: string; key_id: string; public_key: string }>(await request.get(`${DESK}/evidence-key`));
      const pack = await okJson<{
        manifest: { schema: string; application_id: string; sections: Record<string, string> };
        signature: { alg: string; key_id: string; value: string };
        sections: Record<string, unknown> & { case: { case_ref: string; decision?: { outcome: string } } };
      }>(await page.request.get(`${DESK}/applications/${run.application_id}/evidence`));
      expect(pack.manifest.schema).toBe("agenticorg.onboarding_evidence/1");
      expect(pack.manifest.application_id).toBe(run.application_id);
      expect(Object.keys(pack.manifest.sections).sort()).toEqual(Object.keys(pack.sections).sort());
      expect(pack.signature).toMatchObject({ alg: "Ed25519", key_id: key.key_id });
      const publicKey = createPublicKey({
        key: Buffer.concat([Buffer.from("302a300506032b6570032100", "hex"), Buffer.from(key.public_key, "base64")]),
        format: "der",
        type: "spki",
      });
      expect(
        verifySignature(null, Buffer.from(canonical(pack.manifest), "utf-8"), publicKey, Buffer.from(pack.signature.value, "base64")),
        "the manifest signature verifies with the published key",
      ).toBe(true);
      expect(JSON.stringify(pack.sections.consents)).toContain("web_form");
      expect(JSON.stringify(pack.sections.documents)).toContain("certificate-of-incorporation.pdf");
      // The section digests are checked by the desk's own verifier after this run (make e2e-onboarding-desk).
      writeFileSync(EVIDENCE_PACK_PATH, JSON.stringify(pack), "utf-8");
      writeFileSync(EVIDENCE_KEY_PATH, key.public_key, "utf-8");

      // The partner report counts the lead, the application, the case and the approval.
      const report = await okJson<{
        funnel: { leads: number; applications: number; cases_opened: number; approved: number };
        consent_coverage: { any_channel: number | null };
      }>(await page.request.get(`${DESK}/reports/summary`));
      expect(report.funnel.leads).toBeGreaterThanOrEqual(1);
      expect(report.funnel.applications).toBeGreaterThanOrEqual(1);
      expect(report.funnel.cases_opened).toBeGreaterThanOrEqual(1);
      expect(report.funnel.approved).toBeGreaterThanOrEqual(1);
      expect(report.consent_coverage.any_channel).toBeGreaterThan(0);
    } finally {
      await new Promise<void>((resolve) => receiver.close(() => resolve()));
    }
  });
});
