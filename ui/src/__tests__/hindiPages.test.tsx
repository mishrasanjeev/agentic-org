// SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
/**
 * Hindi for the approvals, agents, workflows and report scheduler pages: each
 * page renders its headings and labels in Hindi once the language is switched,
 * and the English and Hindi locales hold the same keys (with the same
 * placeholders) for these pages' namespaces.
 */
import { render, screen } from "@testing-library/react";
import type { ReactElement } from "react";
import { MemoryRouter } from "react-router";
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

const mockGet = vi.fn();
const mockPost = vi.fn();
const mockListAll = vi.fn();
const mockCompaniesList = vi.fn();

vi.mock("@/lib/api", () => ({
  default: {
    get: (...args: unknown[]) => mockGet(...args),
    post: (...args: unknown[]) => mockPost(...args),
    interceptors: { request: { use: vi.fn() }, response: { use: vi.fn() } },
  },
  extractApiError: (_e: unknown, fallback: string) => fallback,
  agentsApi: {
    listAll: (...args: unknown[]) => mockListAll(...args),
    importCsv: vi.fn(),
  },
  companiesApi: {
    list: (...args: unknown[]) => mockCompaniesList(...args),
  },
}));

vi.mock("@/contexts/AuthContext", () => ({
  useAuth: () => ({
    user: { user_id: "u-admin", email: "admin@example.com", name: "Admin", role: "admin", domain: "all", tenant_id: "t1" },
    isAuthenticated: true,
  }),
  useOptionalRole: () => "admin",
}));

import i18n from "@/i18n";
import en from "../locales/en.json";
import hi from "../locales/hi.json";
import Agents from "@/pages/Agents";
import Approvals from "@/pages/Approvals";
import ReportScheduler from "@/pages/ReportScheduler";
import Workflows from "@/pages/Workflows";

const NAMESPACES = ["approvals", "agentsList", "workflowsList", "reportSchedules"] as const;

function leaves(node: unknown, prefix = ""): Record<string, string> {
  if (typeof node === "string") return { [prefix]: node };
  const out: Record<string, string> = {};
  for (const [key, value] of Object.entries(node as Record<string, unknown>)) {
    Object.assign(out, leaves(value, prefix ? `${prefix}.${key}` : key));
  }
  return out;
}

function placeholders(text: string): string[] {
  return Array.from(text.matchAll(/\{\{\s*(\w+)\s*\}\}/g), (m) => m[1]).sort();
}

const originalFetch = globalThis.fetch;

beforeAll(async () => {
  await i18n.changeLanguage("hi");
});

afterAll(async () => {
  await i18n.changeLanguage("en");
});

beforeEach(() => {
  localStorage.clear();
  mockGet.mockReset();
  mockPost.mockReset();
  mockListAll.mockReset();
  mockCompaniesList.mockReset();
  mockGet.mockResolvedValue({ data: { items: [], total: 0, pages: 1 } });
  mockPost.mockResolvedValue({ data: {} });
  mockListAll.mockResolvedValue([]);
  mockCompaniesList.mockResolvedValue({ data: [] });
  globalThis.fetch = vi.fn().mockResolvedValue({ ok: true, status: 200, json: async () => [] }) as typeof fetch;
});

afterEach(() => {
  globalThis.fetch = originalFetch;
});

function renderAt(path: string, page: ReactElement) {
  return render(<MemoryRouter initialEntries={[path]}>{page}</MemoryRouter>);
}

describe("Hindi pages", () => {
  it("shows the approval queue in Hindi", async () => {
    renderAt("/dashboard/approvals", <Approvals />);
    expect(await screen.findByText("कोई लंबित अनुमोदन नहीं।")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "अनुमोदन कतार" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "एजेंट अनुमोदन" })).toBeInTheDocument();
    expect(screen.getByRole("option", { name: "सभी प्राथमिकताएं" })).toBeInTheDocument();
  });

  it("shows an approval card's actions in Hindi", async () => {
    mockGet.mockResolvedValue({
      data: {
        items: [
          {
            id: "h1",
            title: "Approve INV-1",
            trigger_type: "low_confidence",
            priority: "normal",
            status: "pending",
            assignee_role: "finance",
            decision_options: { options: ["approve", "reject"] },
            context: {},
            created_at: "2026-11-01T04:00:00Z",
            expires_at: null,
          },
        ],
        total: 1,
        pages: 1,
      },
    });
    renderAt("/dashboard/approvals", <Approvals />);
    expect(await screen.findByRole("button", { name: "स्वीकृत करें" })).toBeInTheDocument();
    expect(screen.getByText("तर्क का विवरण")).toBeInTheDocument();
    expect(screen.getByText("ट्रिगर: low_confidence | भूमिका: finance")).toBeInTheDocument();
  });

  it("shows the agents list in Hindi", async () => {
    renderAt("/dashboard/agents", <Agents />);
    expect(await screen.findByText("कोई एजेंट नहीं मिला।")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "एजेंट समूह" })).toBeInTheDocument();
    expect(screen.getByPlaceholderText("एजेंट खोजें...")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "CSV आयात करें" })).toBeInTheDocument();
  });

  it("shows the workflows list in Hindi", async () => {
    renderAt("/dashboard/workflows", <Workflows />);
    expect(await screen.findByText("अभी तक कोई वर्कफ़्लो कॉन्फ़िगर नहीं किया गया है।")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "वर्कफ़्लो" })).toBeInTheDocument();
    expect(screen.getByTestId("tab-my-workflows")).toHaveTextContent("मेरे वर्कफ़्लो");
  });

  it("shows the report scheduler in Hindi", async () => {
    renderAt("/dashboard/report-schedules", <ReportScheduler />);
    expect(await screen.findByText(/अभी तक कोई रिपोर्ट शेड्यूल नहीं है/)).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "रिपोर्ट शेड्यूल" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "+ नया शेड्यूल" })).toBeInTheDocument();
  });

  it("holds the same keys and placeholders in English and Hindi for these pages", () => {
    for (const ns of NAMESPACES) {
      const enLeaves = leaves((en as Record<string, unknown>)[ns]);
      const hiLeaves = leaves((hi as Record<string, unknown>)[ns]);
      expect(Object.keys(hiLeaves), ns).toEqual(Object.keys(enLeaves));
      for (const [key, value] of Object.entries(enLeaves)) {
        expect(hiLeaves[key].trim(), `${ns}.${key}`).not.toBe("");
        expect(placeholders(hiLeaves[key]), `${ns}.${key}`).toEqual(placeholders(value));
      }
    }
  });
});
