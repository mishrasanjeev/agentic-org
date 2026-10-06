// SPDX-License-Identifier: Apache-2.0
import { useEffect, useState } from "react";
import api, { extractApiError } from "@/lib/api";

export interface RemoteTool {
  name: string; ref: string; description: string; permission: "read" | "write";
  read_only_hint: boolean; schema_hash: string; inputSchema: Record<string, unknown>;
}
export interface RemoteConnector {
  id: string; name: string; base_url: string; status: string; tools: RemoteTool[]; can_manage: boolean;
}

export default function RemoteMCPToolPicker({ connectors, tools, onChange }: {
  connectors: string[]; tools: string[]; onChange: (connectors: string[], tools: string[]) => void;
}) {
  const [items, setItems] = useState<RemoteConnector[]>([]);
  const [error, setError] = useState("");
  useEffect(() => {
    let active = true;
    api.get<{ items: RemoteConnector[] }>("/connectors/mcp")
      .then(({ data }) => { if (active) setItems(Array.isArray(data.items) ? data.items : []); })
      .catch((err: unknown) => { if (active) setError(extractApiError(err, "Could not load remote MCP tools")); });
    return () => { active = false; };
  }, []);
  return <fieldset className="space-y-3 border-t pt-4">
    <legend className="text-sm font-semibold">Remote MCP connectors and tools</legend>
    {error && <p role="alert" className="text-sm text-destructive">{error}</p>}
    {items.length === 0 && !error && <p className="text-sm text-muted-foreground">No remote MCP connectors registered.</p>}
    {items.map((item) => {
      const aliases = [item.name, item.id, `registry-${item.name}`];
      const linked = connectors.some((id) => aliases.includes(id));
      return <div key={item.id} className="space-y-2">
        <label className="flex items-center gap-2 text-sm font-medium">
          <input type="checkbox" checked={linked} onChange={(event) => {
            const remaining = connectors.filter((id) => !aliases.includes(id));
            onChange(event.target.checked ? [...remaining, item.name] : remaining,
              event.target.checked ? tools : tools.filter((ref) => !ref.startsWith(`${item.name}__`)));
          }} />{item.name}
        </label>
        {linked && item.tools.map((tool) => <label key={tool.ref} className="flex items-start gap-2 pl-6 text-sm">
          <input type="checkbox" className="mt-1" checked={tools.includes(tool.ref)} onChange={(event) => {
            onChange(connectors, event.target.checked ? [...tools, tool.ref] : tools.filter((ref) => ref !== tool.ref));
          }} />
          <span className="min-w-0 break-words">{tool.name} <span className="text-xs text-muted-foreground">
            {tool.permission === "read" ? "Reviewed read" : "Write / approval required"}
          </span></span>
        </label>)}
      </div>;
    })}
  </fieldset>;
}
