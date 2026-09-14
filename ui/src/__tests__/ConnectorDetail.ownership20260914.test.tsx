/**
 * Bug sheet 2026-09-14 rows 17-19/22 — connector ownership controls.
 *
 * PUT/DELETE/test/health/OAuth are allowed for admins and the connector
 * owner. Other connector roles can open the page but see no edit, test,
 * health or reconnect controls; the list hides health/archive likewise.
 */
import { render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router";
import { beforeEach, describe, expect, it, vi } from "vitest";

const mockGet = vi.fn();

let mockUser: Record<string, unknown> = {};

vi.mock("@/contexts/AuthContext", () => ({
  useAuth: () => ({ user: mockUser, isAuthenticated: true }),
}));

vi.mock("@/lib/api", () => ({
  default: {
    get: (...args: unknown[]) => mockGet(...args),
    post: vi.fn(),
    put: vi.fn(),
    delete: vi.fn(),
    interceptors: { request: { use: vi.fn() }, response: { use: vi.fn() } },
  },
  extractApiError: (_e: unknown, fallback: string) => fallback,
}));

import ConnectorDetail from "@/pages/ConnectorDetail";
import Connectors from "@/pages/Connectors";

const OWNER_ID = "u-cfo-owner";

const PERSONAL_CONNECTOR = {
  connector_id: "c1",
  name: "cfo_gmail",
  category: "comms",
  description: null,
  base_url: "https://gmail.googleapis.com",
  auth_type: "oauth2",
  tool_functions: [],
  data_schema_ref: null,
  rate_limit_rpm: 60,
  timeout_ms: 10000,
  status: "active",
  health_check_at: null,
  created_at: "2026-09-14T00:00:00Z",
  owner_user_id: OWNER_ID,
  visibility: "personal",
};

const SHARED_CONNECTOR = {
  ...PERSONAL_CONNECTOR,
  connector_id: "c2",
  name: "tenant_slack",
  owner_user_id: null,
  visibility: "shared",
};

function setUser(userId: string, role: string) {
  mockUser = { user_id: userId, email: `${role}@example.com`, name: role, role, domain: "finance", tenant_id: "t1" };
}

function renderDetail() {
  return render(
    <MemoryRouter initialEntries={["/connectors/c1"]}>
      <Routes>
        <Route path="/connectors/:id" element={<ConnectorDetail />} />
      </Routes>
    </MemoryRouter>,
  );
}

describe("Connector ownership controls (bug sheet 2026-09-14)", () => {
  beforeEach(() => {
    mockGet.mockReset();
    mockGet.mockImplementation((url: string) => {
      if (url === "/connectors/c1") return Promise.resolve({ data: PERSONAL_CONNECTOR });
      if (url === "/connectors") return Promise.resolve({ data: { items: [PERSONAL_CONNECTOR, SHARED_CONNECTOR] } });
      if (url === "/connectors/registry") return Promise.resolve({ data: { items: [], total: 0 } });
      return Promise.resolve({ data: {} });
    });
  });

  it("non-owner: edit, test, health and reconnect are hidden", async () => {
    setUser("u-chro", "chro");
    renderDetail();
    await screen.findByRole("heading", { name: "cfo_gmail" });

    expect(screen.getByTestId("connector-visibility-badge")).toHaveTextContent(/^Personal$/);
    expect(screen.queryByRole("button", { name: "Edit" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Test Connection" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Health Check" })).not.toBeInTheDocument();
    expect(screen.queryByTestId("connector-reconnect")).not.toBeInTheDocument();
    expect(screen.getByTestId("connector-manage-hint")).toHaveTextContent("Only the connector owner or a tenant admin");
  });

  it("owner: edit, test, health and reconnect are available", async () => {
    setUser(OWNER_ID, "cfo");
    renderDetail();
    await screen.findByRole("heading", { name: "cfo_gmail" });

    expect(screen.getByTestId("connector-visibility-badge")).toHaveTextContent("Personal · owned by you");
    expect(screen.getByRole("button", { name: "Edit" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Test Connection" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Health Check" })).toBeInTheDocument();
    expect(screen.getByTestId("connector-reconnect")).toBeInTheDocument();
  });

  it("list: non-admin gets health/archive only on connectors they own", async () => {
    setUser(OWNER_ID, "cfo");
    render(
      <MemoryRouter>
        <Connectors />
      </MemoryRouter>,
    );
    await screen.findByText("cfo_gmail");

    expect(screen.getAllByTestId("connector-visibility-badge").map((b) => b.textContent)).toEqual(["Personal", "Shared"]);
    expect(screen.getByTestId("connector-edit-cfo_gmail")).toHaveTextContent("Edit");
    expect(screen.getByTestId("connector-edit-tenant_slack")).toHaveTextContent("View");
    expect(screen.getAllByRole("button", { name: "Health Check" })).toHaveLength(1);
    expect(screen.getAllByRole("button", { name: "Archive" })).toHaveLength(1);
    expect(screen.queryByRole("button", { name: "CMO Sandbox Setup" })).not.toBeInTheDocument();
  });
});
