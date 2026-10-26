// SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
/**
 * The onboarding desk's applicant element: the link exchange, the partner's name and colour,
 * every item with its reason, saving answers, refused answers, owners, documents and the
 * values they suggest, talking to a person, submitting, French, and that nothing from the
 * server is interpreted as markup.
 *
 * The element is a dependency-free script served from ui/public, so the test loads its
 * source as a string and evaluates it against the jsdom window rather than importing a module.
 */
import { beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

const SOURCE = (
  import.meta.glob("../../public/onboarding-desk/apply.js", {
    query: "?raw",
    import: "default",
    eager: true,
  }) as Record<string, string>
)["../../public/onboarding-desk/apply.js"];

type JsonResponse = { ok: boolean; status: number; statusText?: string; json: () => Promise<unknown> };
type Item = Record<string, unknown>;

declare global {
  interface Window {
    AgenticOrgOnboarding: {
      createClient: (
        apiBase: string,
        fetchImpl?: (url: string, init: RequestInit) => Promise<JsonResponse>
      ) => Record<string, (...args: unknown[]) => Promise<unknown>>;
      linkTokenFrom: (location: Location, history: History) => string;
      STRINGS: Record<string, Record<string, string>>;
      version: string;
    };
  }
}

function json(body: unknown, status = 200): JsonResponse {
  return { ok: status < 400, status, statusText: "", json: async () => body };
}

function flush(): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, 0));
}

const ITEMS: Item[] = [
  { id: "legal_name", kind: "text", required: true, label: "Legal business name", reason: "We match it.", answer: null },
  {
    id: "business_structure",
    kind: "choice",
    required: true,
    label: "Business structure",
    reason: "It decides the registry.",
    answer: null,
    choices: [
      { value: "corporation", label: "Corporation" },
      { value: "llc", label: "Limited liability company" },
    ],
  },
  { id: "registered_address", kind: "address", required: true, label: "Registered address", reason: "Compared.", answer: null },
  { id: "owners", kind: "owners", required: true, label: "Owners", reason: "The law requires it.", answer: null },
  { id: "formation_document", kind: "document", required: true, label: "Formation document", reason: "Confirms.", documents: [] },
  { id: "website", kind: "url", required: false, label: "Website", reason: "Helps.", answer: null },
];

function application(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    id: "app1",
    status: "in_progress",
    locale: "en",
    items: ITEMS,
    missing: ["legal_name", "business_structure", "registered_address", "owners", "formation_document"],
    ready: false,
    person_requested: false,
    ...overrides,
  };
}

function withAnswer(id: string, answer: unknown, extra: Record<string, unknown> = {}): Record<string, unknown> {
  return application({
    items: ITEMS.map((item) => (item.id === id ? { ...item, answer } : item)),
    missing: ["business_structure", "registered_address", "owners", "formation_document"],
    ...extra,
  });
}

const SESSION = {
  session: "session-token",
  expires_at: "2026-10-27T19:00:00Z",
  partner_name: "Northwind <Payments>",
  brand_color: "#1A73E8",
  privacy_url: "https://northwind.example/privacy",
  application: application(),
};

type Mounted = HTMLElement & { fetchImpl?: unknown };

describe("onboarding applicant element", () => {
  let fetchMock: ReturnType<typeof vi.fn>;

  beforeAll(() => {
    expect(SOURCE).toContain("agenticorg-onboarding");
    new Function(SOURCE)();
  });

  beforeEach(() => {
    fetchMock = vi.fn();
    document.body.innerHTML = "";
  });

  function mount(token = "link-token"): Mounted {
    const element = document.createElement("agenticorg-onboarding") as Mounted;
    element.fetchImpl = fetchMock;
    element.setAttribute("api-base", "https://app.example.test/api/v1/");
    if (token) element.setAttribute("link-token", token);
    document.body.appendChild(element);
    return element;
  }

  function card(element: Mounted, id: string): HTMLElement {
    return element.shadowRoot!.querySelector(`[data-item="${id}"]`) as HTMLElement;
  }

  function lastCall(): [string, RequestInit] {
    return fetchMock.mock.calls[fetchMock.mock.calls.length - 1] as [string, RequestInit];
  }

  it("exchanges the link for a session and sends it as a header, never as a cookie", async () => {
    fetchMock.mockResolvedValueOnce(json(SESSION)).mockResolvedValueOnce(json(application()));
    const client = window.AgenticOrgOnboarding.createClient("https://app.example.test/api/v1/", fetchMock as never);
    await client.open("link-token");
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("https://app.example.test/api/v1/onboarding-desk/apply/session");
    expect(JSON.parse(String(init.body))).toEqual({ t: "link-token" });
    expect(init.credentials).toBe("omit");
    await client.application();
    const [, second] = fetchMock.mock.calls[1] as [string, RequestInit];
    expect((second.headers as Record<string, string>)["X-Applicant-Session"]).toBe("session-token");
  });

  it("takes the link token from the address bar and removes it", () => {
    const replace = vi.fn();
    const token = window.AgenticOrgOnboarding.linkTokenFrom(
      { search: "?t=abc&utm=x", pathname: "/apply", hash: "#top" } as unknown as Location,
      { state: null, replaceState: replace } as unknown as History
    );
    expect(token).toBe("abc");
    expect(replace).toHaveBeenCalledWith(null, "", "/apply?utm=x#top");
    expect(window.AgenticOrgOnboarding.linkTokenFrom({ search: "" } as Location, {} as History)).toBe("");
  });

  it("renders the partner, the disclosure, the privacy link and every item with its reason", async () => {
    fetchMock.mockResolvedValueOnce(json(SESSION));
    const element = mount();
    await flush();
    const shadow = element.shadowRoot!;
    expect(shadow.querySelector("h1")!.textContent).toBe("Apply with Northwind <Payments>");
    expect(shadow.querySelector("h1")!.children.length).toBe(0);
    expect(shadow.querySelector(".intro")!.textContent).toContain("An AI assistant helps");
    const privacy = shadow.querySelector("a.privacy") as HTMLAnchorElement;
    expect(privacy.href).toBe("https://northwind.example/privacy");
    expect(privacy.rel).toBe("noopener noreferrer");
    expect(element.style.getPropertyValue("--brand")).toBe("#1A73E8");
    expect(shadow.querySelectorAll("section.item").length).toBe(ITEMS.length);
    expect(card(element, "legal_name").querySelector(".reason")!.textContent).toBe("We match it.");
    expect(card(element, "website").querySelector(".tag")!.textContent).toBe("Optional");
    expect(shadow.querySelector(".progress")!.textContent).toContain("0 of 5 required items done");
    expect((shadow.querySelector('[data-role="submit"]') as HTMLButtonElement).disabled).toBe(true);
  });

  it("refuses an unsafe colour and privacy link", async () => {
    fetchMock.mockResolvedValueOnce(
      json({ ...SESSION, brand_color: "red;background:url(x)", privacy_url: "javascript:alert(1)" })
    );
    const element = mount();
    await flush();
    expect(element.style.getPropertyValue("--brand")).toBe("");
    expect(element.shadowRoot!.querySelector("a.privacy")).toBeNull();
  });

  it("saves an answer and shows a refused one with the server's message", async () => {
    fetchMock
      .mockResolvedValueOnce(json(SESSION))
      .mockResolvedValueOnce(
        json({ application: application(), errors: { legal_name: { code: "required", message: "Please give an answer." } } })
      )
      .mockResolvedValueOnce(json({ application: withAnswer("legal_name", "Northwind Coffee LLC"), errors: {} }));
    const element = mount();
    await flush();
    const legal = card(element, "legal_name");
    const field = legal.querySelector("input") as HTMLInputElement;
    field.value = "x";
    (legal.querySelector("button") as HTMLButtonElement).click();
    await flush();
    expect(JSON.parse(String(lastCall()[1].body))).toEqual({ answers: { legal_name: "x" } });
    expect(lastCall()[1].method).toBe("PUT");
    expect(legal.querySelector(".error")!.textContent).toBe("Please give an answer.");
    field.value = "Northwind Coffee LLC";
    (legal.querySelector("button") as HTMLButtonElement).click();
    await flush();
    const fresh = card(element, "legal_name");
    expect((fresh.querySelector("input") as HTMLInputElement).value).toBe("Northwind Coffee LLC");
    expect(fresh.querySelector(".tag")!.textContent).toBe("Saved");
    expect(fresh.querySelector(".note")!.textContent).toBe("Saved");
    expect(element.shadowRoot!.querySelector(".progress")!.textContent).toContain("1 of 5");
  });

  it("keeps what the applicant typed in other items when one item is saved", async () => {
    fetchMock
      .mockResolvedValueOnce(json(SESSION))
      .mockResolvedValueOnce(json({ application: withAnswer("legal_name", "Acme Co"), errors: {} }));
    const element = mount();
    await flush();
    (card(element, "website").querySelector("input") as HTMLInputElement).value = "draft.example";
    const legal = card(element, "legal_name");
    (legal.querySelector("input") as HTMLInputElement).value = "Acme Co";
    (legal.querySelector("button") as HTMLButtonElement).click();
    await flush();
    expect((card(element, "website").querySelector("input") as HTMLInputElement).value).toBe("draft.example");
  });

  it("sends choices, addresses and owners in the shapes the server validates", async () => {
    fetchMock.mockResolvedValueOnce(json(SESSION)).mockResolvedValue(json({ application: application(), errors: {} }));
    const element = mount();
    await flush();
    const structure = card(element, "business_structure");
    (structure.querySelector("select") as HTMLSelectElement).value = "llc";
    (structure.querySelector("button") as HTMLButtonElement).click();
    await flush();
    expect(JSON.parse(String(lastCall()[1].body))).toEqual({ answers: { business_structure: "llc" } });

    const address = card(element, "registered_address");
    const inputs = address.querySelectorAll("input");
    (inputs[0] as HTMLInputElement).value = "1 Market St";
    (inputs[2] as HTMLInputElement).value = "San Francisco";
    (inputs[3] as HTMLInputElement).value = "ca";
    (inputs[4] as HTMLInputElement).value = "94105";
    (address.querySelector("button") as HTMLButtonElement).click();
    await flush();
    expect(JSON.parse(String(lastCall()[1].body)).answers.registered_address).toEqual({
      lines: ["1 Market St"],
      locality: "San Francisco",
      region: "ca",
      postal_code: "94105",
    });

    const owners = card(element, "owners");
    const add = Array.from(owners.querySelectorAll("button")).find((b) => b.textContent === "Add another person or business")!;
    add.click();
    const boxes = owners.querySelectorAll(".owner");
    expect(boxes.length).toBe(2);
    const first = boxes[0].querySelectorAll("input");
    (first[0] as HTMLInputElement).value = "Avery Example";
    (first[1] as HTMLInputElement).value = "60";
    (first[2] as HTMLInputElement).value = "1980-04";
    const second = boxes[1];
    (second.querySelectorAll("input")[0] as HTMLInputElement).value = "Holdco Inc";
    (second.querySelectorAll("select")[0] as HTMLSelectElement).value = "business";
    (second.querySelectorAll("input")[2] as HTMLInputElement).value = "1999-01-01";
    const save = Array.from(owners.querySelectorAll("button")).find((b) => b.textContent === "Save")!;
    save.click();
    await flush();
    expect(JSON.parse(String(lastCall()[1].body)).answers.owners).toEqual([
      { name: "Avery Example", kind: "person", role: "owner", ownership_pct: 60, date_of_birth: "1980-04" },
      { name: "Holdco Inc", kind: "business", role: "owner" },
    ]);
  });

  it("uploads a document and saves only the suggested values the applicant keeps", async () => {
    const uploaded = application({
      items: ITEMS.map((item) =>
        item.id === "formation_document"
          ? { ...item, documents: [{ id: "d1", filename: "articles.pdf", extraction_status: "suggested" }] }
          : item
      ),
    });
    fetchMock
      .mockResolvedValueOnce(json(SESSION))
      .mockResolvedValueOnce(
        json({
          id: "d1",
          suggestions: [
            { item: "legal_name", value: "Northwind Coffee LLC" },
            { item: "website", value: "https://<b>x</b>.example" },
          ],
        })
      )
      .mockResolvedValueOnce(json(uploaded))
      .mockResolvedValueOnce(json({ application: withAnswer("legal_name", "Northwind Coffee LLC"), errors: {} }));
    const element = mount();
    await flush();
    const docs = card(element, "formation_document");
    const file = docs.querySelector('input[type="file"]') as HTMLInputElement;
    const pdf = new File(["%PDF-1.7"], "articles.pdf", { type: "application/pdf" });
    Object.defineProperty(file, "files", { value: [pdf] });
    (Array.from(docs.querySelectorAll("button")).find((b) => b.textContent === "Upload") as HTMLButtonElement).click();
    await flush();
    await flush();
    const [url, init] = fetchMock.mock.calls[1] as [string, RequestInit];
    expect(url).toBe("https://app.example.test/api/v1/onboarding-desk/apply/documents");
    expect(init.body).toBeInstanceOf(FormData);
    expect((init.body as FormData).get("item")).toBe("formation_document");
    expect((init.headers as Record<string, string>)["Content-Type"]).toBeUndefined();
    const fresh = card(element, "formation_document");
    expect(fresh.querySelector(".docs")!.textContent).toContain("articles.pdf");
    const box = fresh.querySelector(".suggest")!;
    expect(box.classList.contains("hidden")).toBe(false);
    const checks = box.querySelectorAll('input[type="checkbox"]');
    expect(checks.length).toBe(2);
    expect(box.querySelectorAll("b").length).toBe(0);
    expect(box.textContent).toContain("Website: https://<b>x</b>.example");
    (checks[1] as HTMLInputElement).checked = false;
    (box.querySelector("button") as HTMLButtonElement).click();
    await flush();
    expect(lastCall()[0]).toBe("https://app.example.test/api/v1/onboarding-desk/apply/documents/d1/confirm");
    expect(JSON.parse(String(lastCall()[1].body))).toEqual({ items: ["legal_name"] });
    expect((card(element, "legal_name").querySelector("input") as HTMLInputElement).value).toBe("Northwind Coffee LLC");
  });

  it("says so when a document gave nothing, and removes an upload", async () => {
    const withDoc = application({
      items: ITEMS.map((item) =>
        item.id === "formation_document" ? { ...item, documents: [{ id: "d2", filename: "scan.png" }] } : item
      ),
    });
    fetchMock
      .mockResolvedValueOnce(json(SESSION))
      .mockResolvedValueOnce(json({ id: "d2", suggestions: [] }))
      .mockResolvedValueOnce(json(withDoc))
      .mockResolvedValueOnce(json(application()));
    const element = mount();
    await flush();
    const docs = card(element, "formation_document");
    const file = docs.querySelector('input[type="file"]') as HTMLInputElement;
    Object.defineProperty(file, "files", { value: [new File(["x"], "scan.png", { type: "image/png" })] });
    (Array.from(docs.querySelectorAll("button")).find((b) => b.textContent === "Upload") as HTMLButtonElement).click();
    await flush();
    await flush();
    const fresh = card(element, "formation_document");
    expect(fresh.querySelector(".suggest")!.textContent).toContain("couldn't read any details");
    (Array.from(fresh.querySelectorAll("button")).find((b) => b.textContent === "Remove") as HTMLButtonElement).click();
    await flush();
    expect(lastCall()[1].method).toBe("DELETE");
    expect(lastCall()[0]).toBe("https://app.example.test/api/v1/onboarding-desk/apply/documents/d2");
    expect(card(element, "formation_document").querySelector(".docs")!.children.length).toBe(0);
  });

  it("asks for a person with a message, and submits once everything is in", async () => {
    const ready = application({ missing: [], ready: true });
    fetchMock
      .mockResolvedValueOnce(json({ ...SESSION, application: ready }))
      .mockResolvedValueOnce(json({ ...ready, person_requested: true }))
      .mockResolvedValueOnce(json({ ...ready, status: "submitted", ready: false }));
    const events: string[] = [];
    const element = mount();
    element.addEventListener("onboarding:person-requested", () => events.push("person"));
    element.addEventListener("onboarding:submitted", () => events.push("submitted"));
    await flush();
    const footer = element.shadowRoot!.querySelector(".footer")!;
    const buttons = footer.querySelectorAll("button");
    (buttons[0] as HTMLButtonElement).click();
    (footer.querySelector("textarea") as HTMLTextAreaElement).value = "Please call me";
    (buttons[1] as HTMLButtonElement).click();
    await flush();
    expect(JSON.parse(String(lastCall()[1].body))).toEqual({ message: "Please call me" });
    expect(footer.querySelector(".note")!.textContent).toBe("Thanks. A person will contact you soon.");
    const submit = buttons[2] as HTMLButtonElement;
    expect(submit.disabled).toBe(false);
    submit.click();
    await flush();
    expect(lastCall()[0]).toBe("https://app.example.test/api/v1/onboarding-desk/apply/submit");
    expect(element.shadowRoot!.querySelector(".message")!.textContent).toBe(
      "Your application is submitted. Northwind <Payments> will be in touch."
    );
    expect(events).toEqual(["person", "submitted"]);
  });

  it("speaks French when the application is in French", async () => {
    fetchMock.mockResolvedValueOnce(json({ ...SESSION, application: application({ locale: "fr" }) }));
    const element = mount();
    await flush();
    const shadow = element.shadowRoot!;
    expect(shadow.querySelector("h1")!.textContent).toBe("Demande auprès de Northwind <Payments>");
    expect(shadow.querySelector(".intro")!.textContent).toContain("Un assistant IA");
    expect(card(element, "website").querySelector(".tag")!.textContent).toBe("Facultatif");
    expect(shadow.querySelector(".progress")!.textContent).toContain("0 sur 5");
  });

  it("has every string in English and French", () => {
    const { en, fr } = window.AgenticOrgOnboarding.STRINGS;
    expect(Object.keys(fr).sort()).toEqual(Object.keys(en).sort());
    for (const key of Object.keys(en)) expect(fr[key].trim()).not.toBe("");
  });

  it("explains an expired link, an ended session, a closed application and no link", async () => {
    fetchMock.mockResolvedValueOnce(json({ detail: { error: "link_invalid", message: "x" } }, 401));
    let element = mount();
    await flush();
    expect(element.shadowRoot!.querySelector(".message")!.textContent).toContain("This link has expired");

    document.body.innerHTML = "";
    fetchMock.mockResolvedValueOnce(json({ detail: { error: "lead_closed", message: "x" } }, 410));
    element = mount();
    await flush();
    expect(element.shadowRoot!.querySelector(".message")!.textContent).toBe("This application is no longer open.");

    document.body.innerHTML = "";
    fetchMock.mockRejectedValueOnce(new TypeError("network"));
    element = mount();
    await flush();
    expect(element.shadowRoot!.querySelector(".message")!.textContent).toBe(
      "We couldn't reach the server. Please try again."
    );

    document.body.innerHTML = "";
    fetchMock
      .mockResolvedValueOnce(json(SESSION))
      .mockResolvedValueOnce(json({ detail: { error: "session_invalid", message: "x" } }, 401));
    element = mount();
    await flush();
    const legal = card(element, "legal_name");
    (legal.querySelector("input") as HTMLInputElement).value = "Acme Co";
    (legal.querySelector("button") as HTMLButtonElement).click();
    await flush();
    expect(element.shadowRoot!.querySelector(".message")!.textContent).toContain("Your session ended");

    document.body.innerHTML = "";
    element = mount("");
    await flush();
    expect(element.shadowRoot!.querySelector(".message")!.textContent).toContain("Open the link from your message");
    expect(fetchMock).toHaveBeenCalledTimes(5);
  });
});
