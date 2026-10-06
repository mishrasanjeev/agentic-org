// SPDX-License-Identifier: Apache-2.0
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { beforeEach, expect, it, vi } from "vitest";
import { useState } from "react";

const get = vi.fn(); const post = vi.fn(); const put = vi.fn();
vi.mock("@/lib/api", () => ({ default: { get: (...args: unknown[]) => get(...args), post: (...args: unknown[]) => post(...args), put: (...args: unknown[]) => put(...args) },
  extractApiError: (_error: unknown, fallback: string) => fallback }));
import RemoteMCP from "@/pages/RemoteMCP";
import RemoteMCPToolPicker from "@/components/RemoteMCPToolPicker";

const item = { id: "c1", name: "mcp_voice", base_url: "https://tools.example.test/mcp", status: "active", can_manage: true,
  tools: [{ name: "transcribe", ref: "mcp_voice__transcribe", permission: "read", read_only_hint: true, description: "Synthetic read", schema_hash: "hash1", inputSchema: {} },
    { name: "voice_reply", ref: "mcp_voice__voice_reply", permission: "write", read_only_hint: false, description: "Synthetic write", schema_hash: "hash2", inputSchema: {} }] };
beforeEach(() => { vi.clearAllMocks(); get.mockResolvedValue({ data: { items: [item] } }); post.mockResolvedValue({ data: { result: { content: [] } } }); put.mockResolvedValue({ data: item }); });

it("saves a schema-bound review and does not offer writes as reads", async () => {
  render(<MemoryRouter><RemoteMCP /></MemoryRouter>);
  expect(await screen.findByLabelText("Approve read-only voice_reply")).toBeDisabled();
  fireEvent.click(screen.getByRole("button", { name: "Save read-only review" }));
  await waitFor(() => expect(put).toHaveBeenCalledWith("/connectors/mcp/c1/permissions", { read_only_tools: ["transcribe"], schema_hashes: { transcribe: "hash1", voice_reply: "hash2" } }));
});

it("uses the dedicated discovery endpoint and clears submitted secrets", async () => {
  render(<MemoryRouter><RemoteMCP /></MemoryRouter>);
  fireEvent.change(screen.getByLabelText("Connection name"), { target: { value: "mcp_new" } });
  fireEvent.change(screen.getByLabelText("HTTPS MCP endpoint"), { target: { value: "https://tools.example.test/mcp" } });
  fireEvent.change(screen.getByLabelText("Bearer token"), { target: { value: "synthetic-bearer" } });
  fireEvent.click(screen.getByRole("button", { name: "Connect and discover tools" }));
  await waitFor(() => expect(post).toHaveBeenCalledWith("/connectors/mcp", { name: "mcp_new", url: "https://tools.example.test/mcp", access_token: "synthetic-bearer" }));
  await waitFor(() => expect(screen.getByLabelText("Bearer token")).toHaveValue(""));
});

it("preserves native selections and removes only tools of an unlinked remote connector", async () => {
  function Host() {
    const [ids, setIds] = useState(["gmail", "mcp_voice"]);
    const [tools, setTools] = useState(["read_inbox", "mcp_voice__transcribe"]);
    return <><RemoteMCPToolPicker connectors={ids} tools={tools} onChange={(nextIds, nextTools) => { setIds(nextIds); setTools(nextTools); }} /><output>{JSON.stringify({ ids, tools })}</output></>;
  }
  render(<Host />);
  fireEvent.click(await screen.findByLabelText("mcp_voice"));
  expect(screen.getByRole("status")).toHaveTextContent('{"ids":["gmail"],"tools":["read_inbox"]}');
});
