// SPDX-License-Identifier: Apache-2.0
/**
 * Calls: the recordings list with the overview, a call's speakers, summary, analytics and transcript, summarising.
 */
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { HelmetProvider } from "react-helmet-async";
import { beforeEach, describe, expect, it, vi } from "vitest";

const mockGet = vi.fn();
const mockPost = vi.fn();

vi.mock("@/lib/api", () => ({
  default: {
    get: (...args: unknown[]) => mockGet(...args),
    post: (...args: unknown[]) => mockPost(...args),
    interceptors: { request: { use: vi.fn() }, response: { use: vi.fn() } },
  },
  extractApiError: (_err: unknown, fallback: string) => fallback,
}));

import Calls, { clock, percent } from "@/pages/Calls";

const ROW = {
  id: "r1",
  filename: "call.wav",
  status: "transcribed",
  engine: "supplied",
  duration_seconds: 125,
  channels: 2,
  speakers: { agent: { talk_seconds: 60, turns: 3, share: 0.6 }, customer: { talk_seconds: 40, turns: 3, share: 0.4 } },
  segments: 6,
  summarised: true,
  scores: { escalation_risk: "high", empathy: 70 },
  last_error: null,
  created_at: "2026-10-07T10:00:00+00:00",
};
const DETAIL = {
  ...ROW,
  transcript: { turns: [{ speaker: "agent", start: 0.2, end: 2, text: "Good morning", confidence: 0.9 }, { speaker: "customer", start: 2.4, end: 5, text: "My transfer failed", confidence: 0.9 }], word_count: 5, confidence: 0.9 },
  summary: { method: "extractive", intent: "Fund transfer", key_points: ["customer: My transfer failed"], next_actions: ["agent: I will call you back"], outcome: "follow_up", customer_mood: "negative" },
  analytics: {
    scores: { customer_sentiment: -0.4, empathy: 70, customer_talk_share: 0.4, escalation_risk: "high" },
    sentiment: { overall: -0.4, opening: -0.5, closing: 0.2, negative_share: 0.5 },
    empathy: { score: 70, markers: { apology: 1 }, answered_with_empathy: 1, negative_customer_turns: 1 },
    interaction: { talk_ratio: { agent: 0.6, customer: 0.4 }, interruptions: [{}], silences: [] },
    signals: [{ kind: "escalation_phrase", phrase: "complaint" }],
  },
};
const OVERVIEW = { recordings: 3, analysed: 2, averages: { customer_sentiment: -0.1, empathy: 65, customer_talk_share: 0.45 }, escalation_risk: { high: 1, low: 1 }, signals: {} };

function answer(url: string) {
  if (url === "/speech/recordings") return Promise.resolve({ data: { recordings: [ROW], total: 1 } });
  if (url === "/speech/analytics") return Promise.resolve({ data: OVERVIEW });
  if (url === "/speech/recordings/r1") return Promise.resolve({ data: DETAIL });
  return Promise.reject(new Error(`unexpected ${url}`));
}

function renderCalls() {
  return render(
    <HelmetProvider>
      <Calls />
    </HelmetProvider>,
  );
}

describe("Calls", () => {
  beforeEach(() => {
    mockGet.mockReset();
    mockPost.mockReset();
    mockGet.mockImplementation((url: string) => answer(url));
  });

  it("formats clocks and percentages", () => {
    expect(clock(125)).toBe("2:05");
    expect(clock(null)).toBe("");
    expect(percent(0.456)).toBe("46%");
    expect(percent(null)).toBe("—");
  });

  it("lists the recordings with the overview and opens a call", async () => {
    renderCalls();
    const row = await screen.findByTestId("calls-row");
    expect(row.textContent).toContain("call.wav");
    expect(row.textContent).toContain("agent, customer");
    expect(screen.getByTestId("calls-overview").textContent).toContain("2 of 3 analysed");
    fireEvent.click(row);
    await screen.findByTestId("calls-detail");
    expect(screen.getByTestId("calls-summary").textContent).toContain("Fund transfer");
    expect(screen.getByTestId("calls-summary").textContent).toContain("I will call you back");
    expect(screen.getByTestId("calls-analytics").textContent).toContain("70 / 100");
    expect(screen.getByTestId("calls-analytics").textContent).toContain("agent 60% · customer 40%");
    expect(screen.getByTestId("calls-analytics").textContent).toContain("complaint");
    expect(screen.getByTestId("calls-transcript").textContent).toContain("My transfer failed");
  });

  it("requests a summary and reloads", async () => {
    mockPost.mockResolvedValue({ data: {} });
    renderCalls();
    fireEvent.click(await screen.findByTestId("calls-row"));
    await screen.findByTestId("calls-detail");
    fireEvent.click(screen.getByTestId("calls-summarise-words"));
    await waitFor(() => expect(mockPost).toHaveBeenCalledWith("/speech/recordings/r1/summary?method=extractive"));
    expect(await screen.findByTestId("calls-notice")).toHaveTextContent("Summary and analytics updated.");
    await waitFor(() => expect(mockGet.mock.calls.filter((c) => c[0] === "/speech/recordings/r1").length).toBe(2));
  });

  it("reports a failure to load", async () => {
    mockGet.mockRejectedValue(new Error("500"));
    renderCalls();
    expect(await screen.findByRole("alert")).toHaveTextContent("Failed to load the calls.");
  });
});
