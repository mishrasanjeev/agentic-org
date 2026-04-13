import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

const mockGet = vi.fn();
const mockPost = vi.fn();
const mockPatch = vi.fn();
const mockPut = vi.fn();
const mockDelete = vi.fn();

vi.mock("../lib/api", () => ({
  default: {
    get: (...args: unknown[]) => mockGet(...args),
    post: (...args: unknown[]) => mockPost(...args),
    patch: (...args: unknown[]) => mockPatch(...args),
    put: (...args: unknown[]) => mockPut(...args),
    delete: (...args: unknown[]) => mockDelete(...args),
    interceptors: {
      request: { use: vi.fn() },
      response: { use: vi.fn() },
    },
  },
  extractApiError: () => "request failed",
}));

vi.mock("react-helmet-async", () => ({
  Helmet: ({ children }: { children: React.ReactNode }) => <>{children}</>,
  HelmetProvider: ({ children }: { children: React.ReactNode }) => <>{children}</>,
}));

import CompanyDetail from "@/pages/CompanyDetail";

const COMPANY_ID = "company-001";

const companyResponse = {
  id: COMPANY_ID,
  name: "Acme Manufacturing Pvt Ltd",
  gstin: "29AABCU9603R1ZM",
  subscription_status: "trial",
  client_health_score: 92,
  gst_auto_file: true,
  document_vault_enabled: true,
};

const approvalsResponse = [
  {
    id: "approval-1",
    company_id: COMPANY_ID,
    filing_type: "GSTR-1",
    filing_period: "2026-04",
    status: "pending",
    requested_by: "partner@agenticorg.ai",
    created_at: "2026-04-13T09:00:00Z",
  },
];

const deadlinesResponse = [
  {
    id: "deadline-1",
    company_id: COMPANY_ID,
    deadline_type: "GST Filing",
    filing_period: "2026-04",
    due_date: "2099-04-20",
    filed: false,
    created_at: "2026-04-13T09:00:00Z",
  },
];

const uploadsResponse = [
  {
    id: "upload-1",
    company_id: COMPANY_ID,
    upload_type: "GSTR-2B",
    filing_period: "2026-04",
    file_name: "gstr2b-apr.json",
    status: "uploaded",
    uploaded_at: "2026-04-13T10:00:00Z",
    uploaded_by: "associate@agenticorg.ai",
    created_at: "2026-04-13T10:00:00Z",
  },
];

const rolesResponse = {
  roles: [{ user_id: "partner@agenticorg.ai", role: "partner" }],
  valid_roles: ["partner", "manager", "associate", "audit_reviewer"],
};

const credentialsResponse = [
  {
    id: "cred-1",
    company_id: COMPANY_ID,
    gstin: "29AABCU9603R1ZM",
    username: "gst-ops",
    portal_type: "gstn",
    is_active: true,
    last_verified_at: "2026-04-13T10:00:00Z",
    created_at: "2026-04-13T10:00:00Z",
  },
];

const agentsResponse = {
  items: [
    {
      id: "agent-1",
      employee_name: "GST Filing Specialist",
      domain: "finance",
      status: "active",
      designation: "GST Agent",
    },
  ],
};

const workflowsResponse = {
  items: [
    {
      id: "workflow-1",
      name: "GST Return Filing",
      description: "Prepare and file GST return",
      is_active: true,
      created_at: "2026-04-13T10:00:00Z",
    },
  ],
};

const auditResponse = {
  items: [
    {
      id: "audit-1",
      created_at: "2026-04-13T11:00:00Z",
      action: "Synchronized Chartered Accountant Firm Pack assets for Acme Manufacturing Pvt Ltd",
      actor_type: "system",
      outcome: "success",
    },
  ],
};

function renderPage() {
  return render(
    <MemoryRouter initialEntries={[`/dashboard/companies/${COMPANY_ID}`]}>
      <Routes>
        <Route path="/dashboard/companies/:id" element={<CompanyDetail />} />
      </Routes>
    </MemoryRouter>,
  );
}

describe("CompanyDetail", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockGet.mockImplementation((url: string, config?: { params?: Record<string, unknown> }) => {
      if (url === `/companies/${COMPANY_ID}`) return Promise.resolve({ data: companyResponse });
      if (url === `/companies/${COMPANY_ID}/approvals`) return Promise.resolve({ data: approvalsResponse });
      if (url === `/companies/${COMPANY_ID}/deadlines`) return Promise.resolve({ data: deadlinesResponse });
      if (url === `/companies/${COMPANY_ID}/gstn-uploads`) return Promise.resolve({ data: uploadsResponse });
      if (url === `/companies/${COMPANY_ID}/roles`) return Promise.resolve({ data: rolesResponse });
      if (url === `/companies/${COMPANY_ID}/credentials`) return Promise.resolve({ data: credentialsResponse });
      if (url === "/agents") return Promise.resolve({ data: agentsResponse });
      if (url === "/workflows") return Promise.resolve({ data: workflowsResponse });
      if (url === "/audit") return Promise.resolve({ data: auditResponse });
      throw new Error(`Unexpected GET ${url} ${JSON.stringify(config?.params || {})}`);
    });
  });

  it("renders the audit log tab and shows real audit events", async () => {
    renderPage();

    await waitFor(() => {
      expect(screen.getByText("Acme Manufacturing Pvt Ltd")).toBeInTheDocument();
    });

    const auditTab = screen.getByText("Audit Log");
    expect(auditTab).toBeInTheDocument();

    await act(async () => {
      fireEvent.click(auditTab);
    });

    expect(
      screen.getByText("Synchronized Chartered Accountant Firm Pack assets for Acme Manufacturing Pvt Ltd"),
    ).toBeInTheDocument();
    expect(screen.getByText("success")).toBeInTheDocument();
  });

  it("requests company-scoped agents, workflows, and audit records", async () => {
    renderPage();

    await waitFor(() => {
      expect(mockGet).toHaveBeenCalledWith(
        "/agents",
        expect.objectContaining({ params: expect.objectContaining({ company_id: COMPANY_ID }) }),
      );
    });

    expect(mockGet).toHaveBeenCalledWith(
      "/workflows",
      expect.objectContaining({ params: expect.objectContaining({ company_id: COMPANY_ID }) }),
    );
    expect(mockGet).toHaveBeenCalledWith(
      "/audit",
      expect.objectContaining({ params: expect.objectContaining({ company_id: COMPANY_ID }) }),
    );
  });
});
