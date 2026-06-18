import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

const mockListAll = vi.fn();
const mockImportCsv = vi.fn();
const mockCompaniesList = vi.fn();

vi.mock("@/lib/api", () => ({
  default: {
    get: vi.fn(),
    post: vi.fn(),
    interceptors: {
      request: { use: vi.fn() },
      response: { use: vi.fn() },
    },
  },
  agentsApi: {
    listAll: (...args: unknown[]) => mockListAll(...args),
    importCsv: (...args: unknown[]) => mockImportCsv(...args),
  },
  companiesApi: {
    list: (...args: unknown[]) => mockCompaniesList(...args),
  },
}));

import Agents from "@/pages/Agents";

const scopedAgent = {
  id: "agent-company-b",
  name: "Company B AP Agent",
  employee_name: "Company B AP Agent",
  agent_type: "ap_processor",
  domain: "finance",
  status: "active",
  version: "1.0.0",
  confidence_floor: 0.88,
  shadow_sample_count: 0,
  shadow_accuracy_current: null,
  created_at: "2026-06-02T00:00:00Z",
};

function renderAgents() {
  return render(
    <MemoryRouter>
      <Agents />
    </MemoryRouter>,
  );
}

describe("Agents company scope", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    localStorage.clear();
    mockListAll.mockResolvedValue([scopedAgent]);
    mockImportCsv.mockResolvedValue({
      data: { imported: 1, parent_links_set: 0, skipped: 0, skip_details: [] },
    });
    mockCompaniesList.mockResolvedValue({ data: { items: [] } });
  });

  it("passes the navbar-selected company_id to the agents list endpoint", async () => {
    localStorage.setItem("company_id", "company-b");

    renderAgents();

    await waitFor(() => {
      expect(mockListAll).toHaveBeenCalledWith(
        expect.objectContaining({ company_id: "company-b" }),
      );
    });
    expect(screen.getByText("Company B AP Agent")).toBeInTheDocument();
    expect(mockCompaniesList).not.toHaveBeenCalled();
  });

  it("auto-selects the first company before fetching agents when none is stored", async () => {
    mockCompaniesList.mockResolvedValue({
      data: { items: [{ id: "company-a", name: "Company A" }] },
    });

    renderAgents();

    await waitFor(() => {
      expect(localStorage.setItem).toHaveBeenCalledWith("company_id", "company-a");
      expect(mockListAll).toHaveBeenCalledWith(
        expect.objectContaining({ company_id: "company-a" }),
      );
    });
  });

  it("passes the selected company_id when importing agents from CSV", async () => {
    localStorage.setItem("company_id", "company-b");
    const rendered = renderAgents();

    await waitFor(() => {
      expect(mockListAll).toHaveBeenCalledWith(
        expect.objectContaining({ company_id: "company-b" }),
      );
    });

    fireEvent.click(screen.getByRole("button", { name: "Import CSV" }));
    const input = rendered.container.querySelector('input[type="file"]') as HTMLInputElement;
    const file = new File(
      ["name,agent_type,domain\nCompany B Import,ap_processor,finance\n"],
      "agents.csv",
      { type: "text/csv" },
    );
    fireEvent.change(input, { target: { files: [file] } });
    fireEvent.click(screen.getByRole("button", { name: "Upload & Import" }));

    await waitFor(() => {
      expect(mockImportCsv).toHaveBeenCalledWith(file, { company_id: "company-b" });
    });
  });
});
