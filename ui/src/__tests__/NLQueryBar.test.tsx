/**
 * NLQueryBar Component Tests
 *
 * Tests search input rendering, keyboard shortcuts (Cmd+K),
 * query submission, response display with agent attribution,
 * empty query prevention, and dropdown behavior.
 */
import { render, screen, waitFor, fireEvent, act } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import _userEvent from "@testing-library/user-event";
import { vi, describe, it, expect, beforeEach, afterEach } from "vitest";

void _userEvent; // suppress unused import warning

// ---------------------------------------------------------------------------
// Mock API module
// ---------------------------------------------------------------------------

const mockPost = vi.fn();

vi.mock("../lib/api", () => ({
  default: {
    get: vi.fn(),
    post: (...args: unknown[]) => mockPost(...args),
    interceptors: {
      request: { use: vi.fn() },
      response: { use: vi.fn() },
    },
  },
}));

// ---------------------------------------------------------------------------
// Import component under test
// ---------------------------------------------------------------------------

import NLQueryBar from "@/components/NLQueryBar";

// ---------------------------------------------------------------------------
// Test data
// ---------------------------------------------------------------------------

const MOCK_FINANCE_RESPONSE = {
  answer: "Current cash runway is 18 months based on the last 3 months of burn rate.",
  agent: "fpa-analyst",
  confidence: 0.92,
  domain: "finance",
};

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function renderNLQuery(props: { onOpenChat?: () => void } = {}) {
  return render(<MemoryRouter><NLQueryBar {...props} /></MemoryRouter>);
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe("NLQueryBar", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.useFakeTimers({ shouldAdvanceTime: true });
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  // ── Rendering ──────────────────────────────────────────────────────────

  it("renders search input with placeholder", () => {
    renderNLQuery();

    const input = screen.getByPlaceholderText(/Ask anything/i);
    expect(input).toBeInTheDocument();
    expect(input).toHaveAttribute("type", "text");
  });

  it("renders the search icon SVG", () => {
    renderNLQuery();

    // SVG search icon is present
    const svg = document.querySelector("svg");
    expect(svg).toBeInTheDocument();
  });

  // ── Keyboard Shortcut: Cmd+K ───────────────────────────────────────────

  it("Cmd+K focuses the input", () => {
    renderNLQuery();

    const input = screen.getByPlaceholderText(/Ask anything/i);
    expect(document.activeElement).not.toBe(input);

    fireEvent.keyDown(window, { key: "k", metaKey: true });

    expect(document.activeElement).toBe(input);
  });

  it("Ctrl+K focuses the input", () => {
    renderNLQuery();

    const input = screen.getByPlaceholderText(/Ask anything/i);

    fireEvent.keyDown(window, { key: "k", ctrlKey: true });

    expect(document.activeElement).toBe(input);
  });

  // ── Query Submission ───────────────────────────────────────────────────

  it("submitting a query calls API with correct payload", async () => {
    mockPost.mockResolvedValue({ data: MOCK_FINANCE_RESPONSE });

    renderNLQuery();

    const input = screen.getByPlaceholderText(/Ask anything/i);

    // Type a query and submit the form
    await act(async () => {
      fireEvent.change(input, {
        target: { value: "What is our cash runway?" },
      });
    });

    await act(async () => {
      fireEvent.submit(input.closest("form")!);
    });

    await waitFor(() => {
      expect(mockPost).toHaveBeenCalledWith("/chat/query", {
        query: "What is our cash runway?",
        company_id: "",
      });
    });
  });

  it("shows response with agent attribution after query", async () => {
    mockPost.mockResolvedValue({ data: MOCK_FINANCE_RESPONSE });

    renderNLQuery();

    const input = screen.getByPlaceholderText(/Ask anything/i);

    await act(async () => {
      fireEvent.change(input, {
        target: { value: "What is our cash runway?" },
      });
    });

    await act(async () => {
      fireEvent.submit(input.closest("form")!);
    });

    await waitFor(() => {
      expect(screen.getByText("fpa-analyst")).toBeInTheDocument();
    });

    expect(screen.getByText("finance")).toBeInTheDocument();
    expect(screen.getByText("92% confidence")).toBeInTheDocument();
    expect(
      screen.getByText(/Current cash runway is 18 months/),
    ).toBeInTheDocument();
  });

  it("shows loading spinner during API call", async () => {
    // Make API call hang
    mockPost.mockReturnValue(new Promise(() => {}));

    renderNLQuery();

    const input = screen.getByPlaceholderText(/Ask anything/i);

    await act(async () => {
      fireEvent.change(input, {
        target: { value: "What is our cash runway?" },
      });
    });

    await act(async () => {
      fireEvent.submit(input.closest("form")!);
    });

    // The loading spinner is a div with animate-spin class
    const spinner = document.querySelector(".animate-spin");
    expect(spinner).toBeInTheDocument();
  });

  // ── Empty Query Prevention ─────────────────────────────────────────────

  it("empty query does not trigger API call on submit", async () => {
    renderNLQuery();

    const input = screen.getByPlaceholderText(/Ask anything/i);

    await act(async () => {
      fireEvent.submit(input.closest("form")!);
    });

    expect(mockPost).not.toHaveBeenCalled();
  });

  it("whitespace-only query does not trigger API call", async () => {
    renderNLQuery();

    const input = screen.getByPlaceholderText(/Ask anything/i);

    await act(async () => {
      fireEvent.change(input, { target: { value: "   " } });
    });

    await act(async () => {
      fireEvent.submit(input.closest("form")!);
    });

    expect(mockPost).not.toHaveBeenCalled();
  });

  // ── Debounce Behavior ──────────────────────────────────────────────────

  it("debounces typing before making API call", async () => {
    mockPost.mockResolvedValue({ data: MOCK_FINANCE_RESPONSE });

    renderNLQuery();

    const input = screen.getByPlaceholderText(/Ask anything/i);

    // Type more than 2 characters to trigger debounce
    await act(async () => {
      fireEvent.change(input, { target: { value: "cash" } });
    });

    // API should not be called immediately
    expect(mockPost).not.toHaveBeenCalled();

    // Advance past debounce timer (300ms)
    await act(async () => {
      vi.advanceTimersByTime(350);
    });

    await waitFor(() => {
      expect(mockPost).toHaveBeenCalled();
    });
  });

  it("does not trigger debounce for queries with 2 or fewer characters", async () => {
    renderNLQuery();

    const input = screen.getByPlaceholderText(/Ask anything/i);

    await act(async () => {
      fireEvent.change(input, { target: { value: "ab" } });
    });

    await act(async () => {
      vi.advanceTimersByTime(500);
    });

    expect(mockPost).not.toHaveBeenCalled();
  });

  // ── Dropdown Behavior ──────────────────────────────────────────────────

  it("Open Chat button calls onOpenChat callback", async () => {
    mockPost.mockResolvedValue({ data: MOCK_FINANCE_RESPONSE });
    const onOpenChat = vi.fn();

    renderNLQuery({ onOpenChat });

    const input = screen.getByPlaceholderText(/Ask anything/i);

    await act(async () => {
      fireEvent.change(input, {
        target: { value: "What is our cash runway?" },
      });
    });

    await act(async () => {
      fireEvent.submit(input.closest("form")!);
    });

    await waitFor(() => {
      expect(screen.getByText("fpa-analyst")).toBeInTheDocument();
    });

    // Click "Open Chat"
    const openChatBtn = screen.getByText(/Open Chat/);
    await act(async () => {
      fireEvent.click(openChatBtn);
    });

    expect(onOpenChat).toHaveBeenCalledTimes(1);
  });

  it("Dismiss button closes the dropdown", async () => {
    mockPost.mockResolvedValue({ data: MOCK_FINANCE_RESPONSE });

    renderNLQuery();

    const input = screen.getByPlaceholderText(/Ask anything/i);

    await act(async () => {
      fireEvent.change(input, {
        target: { value: "What is our cash runway?" },
      });
    });

    await act(async () => {
      fireEvent.submit(input.closest("form")!);
    });

    await waitFor(() => {
      expect(screen.getByText("fpa-analyst")).toBeInTheDocument();
    });

    // Click "Dismiss"
    const dismissBtn = screen.getByText("Dismiss");
    await act(async () => {
      fireEvent.click(dismissBtn);
    });

    // Dropdown should no longer be visible
    expect(screen.queryByText("fpa-analyst")).not.toBeInTheDocument();
  });

  it("clicking outside the wrapper closes the dropdown", async () => {
    mockPost.mockResolvedValue({ data: MOCK_FINANCE_RESPONSE });

    const { container: _container } = renderNLQuery();

    const input = screen.getByPlaceholderText(/Ask anything/i);

    await act(async () => {
      fireEvent.change(input, {
        target: { value: "What is our cash runway?" },
      });
    });

    await act(async () => {
      fireEvent.submit(input.closest("form")!);
    });

    await waitFor(() => {
      expect(screen.getByText("fpa-analyst")).toBeInTheDocument();
    });

    // Click outside the wrapper
    await act(async () => {
      fireEvent.mouseDown(document.body);
    });

    expect(screen.queryByText("fpa-analyst")).not.toBeInTheDocument();
  });

  // ── Error Handling ─────────────────────────────────────────────────────

  it("handles API error gracefully without crashing", async () => {
    mockPost.mockRejectedValue(new Error("Network Error"));

    renderNLQuery();

    const input = screen.getByPlaceholderText(/Ask anything/i);

    await act(async () => {
      fireEvent.change(input, {
        target: { value: "What is our cash runway?" },
      });
    });

    await act(async () => {
      fireEvent.submit(input.closest("form")!);
    });

    // Should not crash, and dropdown should not open with stale data
    await waitFor(() => {
      const spinner = document.querySelector(".animate-spin");
      expect(spinner).not.toBeInTheDocument();
    });
  });
});
