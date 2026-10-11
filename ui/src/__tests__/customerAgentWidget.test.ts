// SPDX-License-Identifier: Apache-2.0
/**
 * The embeddable customer agent element: the disclosure gate, turns, prepared-action
 * cards with one-tap confirm and cancel, the step-up event, and the "why this" panel.
 *
 * The widget is a dependency-free script served from ui/public, so the test loads
 * its source as a string and evaluates it against the jsdom window rather than importing a module.
 */
import { beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

// The widget source as a string through Vite, so the test needs no Node types.
const WIDGET_SOURCE = (
  import.meta.glob("../../public/customer-agent/widget.js", {
    query: "?raw",
    import: "default",
    eager: true,
  }) as Record<string, string>
)["../../public/customer-agent/widget.js"];

type JsonResponse = { ok: boolean; status: number; statusText?: string; json: () => Promise<unknown> };

declare global {
  interface Window {
    AgenticOrgCustomerAgent: {
      createClient: (
        apiBase: string,
        token: string,
        fetchImpl?: (url: string, init: RequestInit) => Promise<JsonResponse>
      ) => Record<string, (...args: unknown[]) => Promise<unknown>>;
      Element: CustomElementConstructor;
      version: string;
    };
  }
}

function json(body: unknown, status = 200): JsonResponse {
  return { ok: status < 400, status, statusText: "", json: async () => body };
}

const SESSION = {
  session_id: "s1",
  customer_ref: "c-1",
  channel: "web",
  disclosure: "You are talking to the bank's AI assistant.",
  disclosure_acknowledged: false,
};

const CARD_TURN = {
  answer: "Transfer Rs 5,000 to Ravi. Reply yes to confirm or no to cancel.",
  outcome: { kind: "confirm", intent: "fund_transfer" },
  card: {
    type: "prepared_action",
    action_id: "a1",
    intent: "fund_transfer",
    title: "Fund transfer",
    summary: "Rs 5,000 to Ravi",
    parameters: { amount: 5000, payee: "Ravi" },
    requires: "confirm",
    level: 2,
    status: "prepared",
  },
  explanation: {
    rule: "Autonomy level 2 (prepare): the action waits for one tap.",
    alternatives: ["Pay a different amount"],
    bank_earns: "No fee or commission is recorded for this.",
    challenge: "Say 'talk to a person' at any time.",
    data_used: { amount: 5000 },
  },
  approval_required: false,
};

function flush(): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, 0));
}

describe("customer agent widget", () => {
  let fetchMock: ReturnType<typeof vi.fn>;

  beforeAll(() => {
    expect(WIDGET_SOURCE).toContain("agenticorg-customer-agent");
    new Function(WIDGET_SOURCE)();
  });

  beforeEach(() => {
    fetchMock = vi.fn();
    document.body.innerHTML = "";
  });

  function mount(): HTMLElement & { fetchImpl?: unknown; send: (t: string) => Promise<unknown>; approve: (id: string, a: unknown) => Promise<unknown> } {
    const element = document.createElement("agenticorg-customer-agent") as HTMLElement & {
      fetchImpl?: unknown;
      send: (t: string) => Promise<unknown>;
      approve: (id: string, a: unknown) => Promise<unknown>;
    };
    element.fetchImpl = fetchMock;
    element.setAttribute("api-base", "https://api.bank.test/api/v1/");
    element.setAttribute("session-token", "cas_test_token_value_1234567890");
    document.body.appendChild(element);
    return element;
  }

  it("registers the element and exposes a client with the session token as a bearer", async () => {
    expect(window.customElements.get("agenticorg-customer-agent")).toBeTruthy();
    fetchMock.mockResolvedValueOnce(json(SESSION));
    const client = window.AgenticOrgCustomerAgent.createClient("https://api.bank.test/api/v1", "cas_x", fetchMock as never);
    await client.session();
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("https://api.bank.test/api/v1/customer-agent/session");
    expect((init.headers as Record<string, string>).Authorization).toBe("Bearer cas_x");
  });

  it("shows the disclosure until it is acknowledged, then opens the composer", async () => {
    fetchMock.mockResolvedValueOnce(json(SESSION)).mockResolvedValueOnce(json({ ...SESSION, disclosure_acknowledged: true }));
    const element = mount();
    await flush();
    const shadow = element.shadowRoot!;
    const disclosure = shadow.querySelector('[data-role="disclosure"]')!;
    expect(disclosure.classList.contains("hidden")).toBe(false);
    expect(disclosure.querySelector("p")!.textContent).toBe(SESSION.disclosure);
    expect(shadow.querySelector("form")!.classList.contains("hidden")).toBe(true);
    (shadow.querySelector('[data-role="acknowledge"]') as HTMLButtonElement).click();
    await flush();
    expect(fetchMock.mock.calls[1][0]).toBe("https://api.bank.test/api/v1/customer-agent/session/acknowledge");
    expect(disclosure.classList.contains("hidden")).toBe(true);
    expect(shadow.querySelector("form")!.classList.contains("hidden")).toBe(false);
  });

  it("renders a prepared-action card and confirms it with one tap", async () => {
    fetchMock
      .mockResolvedValueOnce(json({ ...SESSION, disclosure_acknowledged: true }))
      .mockResolvedValueOnce(json(CARD_TURN))
      .mockResolvedValueOnce(
        json({
          answer: "Done. Reference TXN1.",
          outcome: { kind: "execute", intent: "fund_transfer" },
          card: { ...CARD_TURN.card, status: "executed" },
          explanation: CARD_TURN.explanation,
        })
      );
    const element = mount();
    await flush();
    await element.send("transfer 5000 to Ravi");
    const shadow = element.shadowRoot!;
    const card = shadow.querySelector('[data-action-id="a1"]')!;
    expect(card.querySelector("h2")!.textContent).toBe("Fund transfer");
    expect(card.querySelector(".why")).toBeTruthy();
    const buttons = Array.from(card.querySelectorAll("button")).map((b) => b.textContent);
    expect(buttons).toEqual(["Confirm", "Cancel"]);
    (card.querySelector("button") as HTMLButtonElement).click();
    await flush();
    await flush();
    const [url, init] = fetchMock.mock.calls[2] as [string, RequestInit];
    expect(url).toBe("https://api.bank.test/api/v1/customer-agent/actions/a1/confirm");
    expect(init.method).toBe("POST");
    expect(card.querySelector(".state")!.textContent).toBe("executed");
    expect(card.querySelectorAll("button").length).toBe(0);
    const messages = Array.from(shadow.querySelectorAll(".msg.agent")).map((m) => m.textContent);
    expect(messages[messages.length - 1]).toBe("Done. Reference TXN1.");
  });

  it("leaves a step-up to the host: fires approval-required and sends the approval it is handed", async () => {
    const stepUp = {
      ...CARD_TURN,
      card: { ...CARD_TURN.card, action_id: "a2", requires: "step_up", level: 3 },
      approval_required: true,
    };
    fetchMock
      .mockResolvedValueOnce(json({ ...SESSION, disclosure_acknowledged: true }))
      .mockResolvedValueOnce(json(stepUp))
      .mockResolvedValueOnce(json({ answer: "Done.", outcome: { kind: "execute" }, card: { ...stepUp.card, status: "executed" } }));
    const element = mount();
    await flush();
    const events: CustomEvent[] = [];
    element.addEventListener("customer-agent:approval-required", (event) => events.push(event as CustomEvent));
    await element.send("transfer 50000 to Ravi");
    const card = element.shadowRoot!.querySelector('[data-action-id="a2"]')!;
    (card.querySelector("button") as HTMLButtonElement).click();
    expect(events).toHaveLength(1);
    expect(events[0].detail.action_id).toBe("a2");
    expect(fetchMock.mock.calls).toHaveLength(2);
    await element.approve("a2", { method: "passkey", assertion: "eyJ.signed.approval" });
    const [url, init] = fetchMock.mock.calls[2] as [string, RequestInit];
    expect(url).toBe("https://api.bank.test/api/v1/customer-agent/actions/a2/confirm");
    expect(JSON.parse(init.body as string)).toEqual({ approval: { method: "passkey", assertion: "eyJ.signed.approval" } });
  });

  it("never interprets server text as markup", async () => {
    fetchMock
      .mockResolvedValueOnce(json({ ...SESSION, disclosure_acknowledged: true }))
      .mockResolvedValueOnce(json({ answer: "<img src=x onerror=alert(1)>", outcome: { kind: "answer" } }));
    const element = mount();
    await flush();
    await element.send("<b>hi</b>");
    const shadow = element.shadowRoot!;
    expect(shadow.querySelector("img")).toBeNull();
    expect(shadow.querySelector("b")).toBeNull();
    expect(shadow.querySelector(".msg.agent")!.textContent).toBe("<img src=x onerror=alert(1)>");
  });

  it("reports an ended session instead of failing silently", async () => {
    fetchMock.mockResolvedValueOnce(json({ detail: { error: "customer_session_invalid" } }, 401));
    const element = mount();
    await flush();
    expect(element.shadowRoot!.querySelector(".msg.system")!.textContent).toContain("session has ended");
  });
});
