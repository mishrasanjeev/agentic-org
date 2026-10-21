// SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
import { fireEvent, render, screen, within } from "@testing-library/react";
import { HelmetProvider } from "react-helmet-async";
import { MemoryRouter, Route, Routes } from "react-router";
import { beforeEach, describe, expect, it, vi } from "vitest";
import SpendPipelineMap from "@/components/docs/SpendPipelineMap";
import SpendReadersMap from "@/components/docs/SpendReadersMap";
import Documentation from "@/pages/Documentation";
import manual from "@/content/userDocs.generated.json";

function openDocs(path: string) {
  return render(
    <HelmetProvider>
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route path="/docs" element={<Documentation />} />
          <Route path="/docs/:slug" element={<Documentation />} />
        </Routes>
      </MemoryRouter>
    </HelmetProvider>,
  );
}

beforeEach(() => {
  vi.spyOn(window, "scrollTo").mockImplementation(() => {});
  Element.prototype.scrollIntoView = vi.fn();
});

describe("AI spend pipeline map", () => {
  it("lists nine stages in three phases and starts at the billable event", () => {
    render(<SpendPipelineMap />);
    const map = screen.getByRole("region", { name: "AI spend pipeline map" });
    for (const phase of ["Meter stages", "Price and record stages", "Reconcile stages"])
      expect(within(map).getByRole("group", { name: phase })).toBeInTheDocument();
    const stages = within(map).getAllByRole("button", { name: /^Stage \d of 9:/ });
    expect(stages).toHaveLength(9);
    expect(stages[0]).toHaveAttribute("aria-current", "step");
    expect(stages[0]).toHaveAttribute("tabindex", "0");
    expect(stages.slice(1).every((stage) => stage.getAttribute("tabindex") === "-1")).toBe(true);
    expect(map).toHaveTextContent("Never a prompt, a response, a document or customer data");
    expect(within(map).getByRole("button", { name: "Previous stage" })).toHaveAttribute("aria-disabled", "true");
    expect(within(map).getByRole("button", { name: "Next stage" })).toHaveAttribute("aria-disabled", "false");
    expect(within(map).getByRole("status")).toHaveTextContent("Stage 1 of 9: Billable event");
  });

  it("selects a stage by click and explains reconciliation and the gate", () => {
    render(<SpendPipelineMap />);
    const map = screen.getByRole("region", { name: "AI spend pipeline map" });
    fireEvent.click(within(map).getByRole("button", { name: "Stage 8 of 9: Reconciliation" }));
    expect(within(map).getByRole("heading", { level: 3, name: "Reconciliation" })).toBeInTheDocument();
    expect(map).toHaveTextContent("An item passes only when both figures are within 1%");
    expect(map).toHaveTextContent("never the importer");
    fireEvent.click(within(map).getByRole("button", { name: "Next stage" }));
    expect(within(map).getByRole("heading", { level: 3, name: "Gate 1" })).toBeInTheDocument();
    expect(map).toHaveTextContent("at least 98%");
    expect(map).toHaveTextContent("An accepted difference does not meet the gate");
    expect(map).toHaveTextContent("GET /api/v1/spend/gate?period=YYYY-MM");
    expect(within(map).getByRole("button", { name: "Next stage" })).toHaveAttribute("aria-disabled", "true");
    expect(within(map).getByRole("status")).toHaveTextContent("Stage 9 of 9: Gate 1");
  });

  it("keeps focus on the stepper buttons at either end", () => {
    render(<SpendPipelineMap />);
    const map = screen.getByRole("region", { name: "AI spend pipeline map" });
    const previous = within(map).getByRole("button", { name: "Previous stage" });
    previous.focus();
    fireEvent.click(previous);
    expect(previous).toHaveFocus();
    expect(within(map).getByRole("heading", { level: 3, name: "Billable event" })).toBeInTheDocument();
    fireEvent.click(within(map).getByRole("button", { name: "Stage 9 of 9: Gate 1" }));
    const next = within(map).getByRole("button", { name: "Next stage" });
    next.focus();
    fireEvent.click(next);
    expect(next).toHaveFocus();
    expect(next).not.toBeDisabled();
    expect(within(map).getByRole("heading", { level: 3, name: "Gate 1" })).toBeInTheDocument();
  });

  it("scrolls the detail panel into view when a stage is chosen on a narrow screen", () => {
    const original = window.matchMedia;
    const scroll = vi.fn();
    Element.prototype.scrollIntoView = scroll;
    try {
      window.matchMedia = ((query: string) => ({ matches: query === "(max-width: 640px)" })) as typeof window.matchMedia;
      render(<SpendPipelineMap />);
      const map = screen.getByRole("region", { name: "AI spend pipeline map" });
      fireEvent.click(within(map).getByRole("button", { name: "Stage 6 of 9: Rollups and coverage" }));
      expect(scroll).toHaveBeenCalledTimes(1);
      expect(scroll.mock.contexts[0]).toBe(map.querySelector("#docs-spend-detail"));
      fireEvent.keyDown(within(map).getByRole("button", { name: "Stage 6 of 9: Rollups and coverage" }), { key: "ArrowRight" });
      expect(scroll).toHaveBeenCalledTimes(1);
    } finally {
      window.matchMedia = original;
    }
  });

  it("does not scroll on a wide screen", () => {
    const original = window.matchMedia;
    const scroll = vi.fn();
    Element.prototype.scrollIntoView = scroll;
    try {
      window.matchMedia = (() => ({ matches: false })) as unknown as typeof window.matchMedia;
      render(<SpendPipelineMap />);
      const map = screen.getByRole("region", { name: "AI spend pipeline map" });
      fireEvent.click(within(map).getByRole("button", { name: "Stage 6 of 9: Rollups and coverage" }));
      expect(scroll).not.toHaveBeenCalled();
    } finally {
      window.matchMedia = original;
    }
  });

  it("moves selection and focus with the arrow, Home and End keys", () => {
    render(<SpendPipelineMap />);
    const map = screen.getByRole("region", { name: "AI spend pipeline map" });
    const first = within(map).getByRole("button", { name: "Stage 1 of 9: Billable event" });
    first.focus();
    fireEvent.keyDown(first, { key: "ArrowRight" });
    const meter = within(map).getByRole("button", { name: "Stage 2 of 9: Meter" });
    expect(meter).toHaveFocus();
    expect(meter).toHaveAttribute("aria-current", "step");
    expect(map).toHaveTextContent("the call carries on unchanged");
    fireEvent.keyDown(meter, { key: "End" });
    const gate = within(map).getByRole("button", { name: "Stage 9 of 9: Gate 1" });
    expect(gate).toHaveFocus();
    expect(gate).toHaveAttribute("tabindex", "0");
    fireEvent.keyDown(gate, { key: "ArrowDown" });
    expect(gate).toHaveFocus();
    fireEvent.keyDown(gate, { key: "ArrowUp" });
    expect(within(map).getByRole("button", { name: "Stage 8 of 9: Reconciliation" })).toHaveFocus();
    fireEvent.keyDown(document.activeElement as Element, { key: "Home" });
    expect(first).toHaveFocus();
    expect(first).toHaveAttribute("aria-current", "step");
    fireEvent.keyDown(first, { key: "ArrowLeft" });
    expect(first).toHaveFocus();
    fireEvent.keyDown(first, { key: "Enter" });
    expect(first).toHaveAttribute("aria-current", "step");
  });

  it("states pricing boundaries without claiming a live view", () => {
    render(<SpendPipelineMap />);
    const map = screen.getByRole("region", { name: "AI spend pipeline map" });
    fireEvent.click(within(map).getByRole("button", { name: "Stage 4 of 9: Priced in rupees" }));
    expect(map).toHaveTextContent("contract before list");
    expect(map).toHaveTextContent("no amount, never zero");
    expect(map).toHaveTextContent("fx_estimated");
    expect(map).toHaveTextContent("not a live view of your spend");
    expect(map).toHaveTextContent("there is no spend screen in the app");
  });
});

describe("who reads spend figures", () => {
  it("shows every figure to an administrator and refuses commercial reads to others", () => {
    render(<SpendReadersMap />);
    const view = screen.getByRole("region", { name: "Who reads spend figures" });
    const admin = within(view).getByRole("button", { name: /Administrator/ });
    expect(admin).toHaveAttribute("aria-pressed", "true");
    expect(within(view).getByRole("list", { name: "What administrator reads" })).toHaveTextContent(
      "The whole tenant",
    );
    fireEvent.click(within(view).getByRole("button", { name: /Domain role/ }));
    expect(admin).toHaveAttribute("aria-pressed", "false");
    const list = within(view).getByRole("list", { name: "What domain role reads" });
    expect(list).toHaveTextContent("tenant_wide_read_refused");
    expect(list).toHaveTextContent("commercial_read_refused");
    expect(list).toHaveTextContent("Contract terms and user ids hidden");
    expect(within(list).getAllByText("Refused")).toHaveLength(4);
    expect(within(list).getAllByText("Filtered")).toHaveLength(1);
    expect(within(view).getByRole("status")).toHaveTextContent("Domain role: 0 read, 1 filtered, 4 refused");
    expect(within(view).getByRole("button", { name: /such as the CFO role/ })).toHaveAttribute("aria-pressed", "true");
  });

  it("explains machine credentials and auditors, and moves with the arrow keys", () => {
    render(<SpendReadersMap />);
    const view = screen.getByRole("region", { name: "Who reads spend figures" });
    const admin = within(view).getByRole("button", { name: /Administrator/ });
    admin.focus();
    fireEvent.keyDown(admin, { key: "ArrowRight" });
    const auditor = within(view).getByRole("button", { name: /Auditor/ });
    expect(auditor).toHaveFocus();
    expect(auditor).toHaveAttribute("aria-pressed", "true");
    expect(within(view).getByRole("list", { name: "What auditor reads" })).toHaveTextContent(
      "changes need a tenant administrator",
    );
    fireEvent.keyDown(auditor, { key: "End" });
    const machine = within(view).getByRole("button", { name: /API key or agent token/ });
    expect(machine).toHaveFocus();
    const list = within(view).getByRole("list", { name: "What api key or agent token reads" });
    expect(list).toHaveTextContent("Refused whatever its scopes");
    expect(list).toHaveTextContent("changes need a person, not a credential");
    fireEvent.keyDown(machine, { key: "ArrowRight" });
    expect(admin).toHaveFocus();
    fireEvent.keyDown(admin, { key: "ArrowLeft" });
    expect(machine).toHaveFocus();
  });
});

describe("AI spend guide in the documentation site", () => {
  it("publishes the guide in an existing group and keeps first-agent second", () => {
    const article = manual.articles.find((item) => item.slug === "ai-spend-intelligence");
    expect(article).toBeDefined();
    expect(article?.group).toBe("Govern And Operate");
    expect(manual.articles[1].slug).toBe("first-agent");
    expect(manual.groups).toHaveLength(5);
  });

  it("renders the pipeline and reader views only on the spend guide", () => {
    openDocs("/docs/ai-spend-intelligence");
    const main = screen.getByRole("main");
    expect(
      within(main).getByRole("heading", {
        level: 1,
        name: "AI spend intelligence and invoice reconciliation",
      }),
    ).toBeInTheDocument();
    expect(within(main).getByRole("region", { name: "AI spend pipeline map" })).toBeInTheDocument();
    expect(within(main).getByRole("region", { name: "Who reads spend figures" })).toBeInTheDocument();
    expect(within(main).getByRole("list", { name: "Process map" })).toHaveTextContent("Administrator B, not the importer");
    expect(main).toHaveTextContent("AGENTICORG_SPEND_INTELLIGENCE_ENABLED");
    expect(main).toHaveTextContent("there is no spend screen in the app");
  });

  it("does not render the spend views on other guides", () => {
    openDocs("/docs/guardrails");
    expect(screen.queryByRole("region", { name: "AI spend pipeline map" })).not.toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "Who reads spend figures" })).not.toBeInTheDocument();
  });

  it("features the guide on the documentation home", () => {
    openDocs("/docs");
    const main = screen.getByRole("main");
    expect(within(main).getByRole("link", { name: "AI spend and invoice reconciliation" })).toHaveAttribute(
      "href",
      "/docs/ai-spend-intelligence",
    );
    expect(within(main).getByRole("link", { name: "Explore BFSI process maps" })).toHaveAttribute(
      "href",
      "/docs#bfsi-playbooks",
    );
  });
});
