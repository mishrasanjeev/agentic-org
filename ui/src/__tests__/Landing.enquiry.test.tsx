// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { BrowserFrame, DemoModal } from "@/pages/Landing";

const trackEvent = vi.hoisted(() => vi.fn());
vi.mock("@/components/Analytics", () => ({ trackEvent }));

function completeForm() {
  fireEvent.change(screen.getByLabelText(/Name/), { target: { value: "Test Visitor" } });
  fireEvent.change(screen.getByLabelText(/Work Email/), { target: { value: "visitor@example.test" } });
  fireEvent.change(screen.getByLabelText("Company"), { target: { value: "Private Company" } });
  fireEvent.change(screen.getByLabelText("Role"), { target: { value: "CFO" } });
}

beforeEach(() => vi.clearAllMocks());
afterEach(() => { vi.unstubAllGlobals(); vi.useRealTimers(); });

describe("public demo enquiry", () => {
  it("reserves the actual screenshot dimensions before lazy media loads", () => {
    render(<BrowserFrame src="/screenshots/agents.webp" title="Synthetic preview" />);
    const image = screen.getByRole("img", { name: "Synthetic preview" });
    expect(image).toHaveAttribute("width", "1920");
    expect(image).toHaveAttribute("height", "1080");
    expect(image).toHaveAttribute("loading", "lazy");
  });
  it("sends contact details to intake, not analytics", async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, status: 201 });
    vi.stubGlobal("fetch", fetchMock);
    render(<DemoModal onClose={vi.fn()} />);
    completeForm();
    fireEvent.click(screen.getByRole("button", { name: "Request Demo" }));
    await screen.findByText("Thanks!");
    await waitFor(() => expect(screen.getAllByRole("button", { name: "Close" })[0]).toHaveFocus());
    expect(JSON.parse(fetchMock.mock.calls[0][1].body)).toMatchObject({
      email: "visitor@example.test", company: "Private Company", role: "CFO",
    });
    await waitFor(() => expect(trackEvent).toHaveBeenCalledWith("demo_request", {
      source: "landing", audience: "general",
    }));
    expect(JSON.stringify(trackEvent.mock.calls)).not.toMatch(/Private Company|visitor@|CFO/);
  });

  it("retains the enquiry and provides a safe recovery path on provider failure", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: false, status: 503 }));
    render(<DemoModal onClose={vi.fn()} />);
    completeForm();
    fireEvent.click(screen.getByRole("button", { name: "Request Demo" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(/not confirm receipt/);
    expect(screen.getByLabelText(/Work Email/)).toHaveValue("visitor@example.test");
    expect(screen.getByRole("link", { name: "sanjeev@orchestrum.in" })).toHaveAttribute("href", "mailto:sanjeev@orchestrum.in");
    expect(trackEvent).not.toHaveBeenCalled();
  });

  it("times out a stalled request, retaining the contact details", async () => {
    vi.useFakeTimers();
    vi.stubGlobal("fetch", vi.fn((_: string, options: RequestInit) => new Promise((_, reject) => {
      options.signal?.addEventListener("abort", () => reject(new Error("Transport stopped")));
    })));
    render(<DemoModal onClose={vi.fn()} />);
    completeForm();
    fireEvent.click(screen.getByRole("button", { name: "Request Demo" }));
    await act(() => vi.advanceTimersByTimeAsync(15000));
    expect(screen.getByRole("alert")).toHaveTextContent(/not confirm receipt/);
    expect(screen.getByLabelText(/Work Email/)).toHaveValue("visitor@example.test");
    expect(screen.getByRole("button", { name: "Request Demo" })).toBeEnabled();
  });

  it("does not create a second pending request and preserves bounded audience context", async () => {
    let finish!: (value: { ok: boolean; status: number }) => void;
    const fetchMock = vi.fn(() => new Promise(resolve => { finish = resolve; }));
    vi.stubGlobal("fetch", fetchMock);
    render(<DemoModal audience="banks" onClose={vi.fn()} />);
    completeForm();
    const form = screen.getByRole("button", { name: "Request Demo" }).closest("form")!;
    fireEvent.submit(form);
    fireEvent.submit(form);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    await act(async () => finish({ ok: true, status: 201 }));
    await waitFor(() => expect(trackEvent).toHaveBeenCalledWith("demo_request", { source: "landing", audience: "banks" }));
  });
});
