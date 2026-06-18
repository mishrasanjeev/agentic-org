const READABLE_KEYS = [
  "text",
  "content",
  "answer",
  "response",
  "message",
  "summary",
  "result",
  "raw_output",
] as const;

const INTERNAL_KEYS = new Set([
  "status",
  "confidence",
  "signature",
  "extras",
  "metadata",
  "debug",
  "debug_info",
  "debugging_information",
  "trace",
  "reasoning_trace",
  "tool_calls",
  "tool_calls_log",
  "tool_outputs",
  "tool_results",
  "correlation_id",
  "request_id",
  "trace_id",
  "thread_id",
  "tenant_id",
  "agent_id",
  "internal_id",
  "internal_ids",
  "token",
  "access_token",
  "refresh_token",
  "api_key",
  "secret",
  "password",
]);

function pythonReprToJson(value: string): string | null {
  if (value.includes('"') || value.includes("\\")) return null;
  return value
    .replace(/'/g, '"')
    .replace(/\bTrue\b/g, "true")
    .replace(/\bFalse\b/g, "false")
    .replace(/\bNone\b/g, "null");
}

function parseStructuredString(value: string): unknown | null {
  const trimmed = value.trim();
  if (!(trimmed.startsWith("{") || trimmed.startsWith("["))) return null;

  try {
    return JSON.parse(trimmed);
  } catch {
    const candidate = pythonReprToJson(trimmed);
    if (!candidate) return null;
    try {
      return JSON.parse(candidate);
    } catch {
      return null;
    }
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return value != null && typeof value === "object" && !Array.isArray(value);
}

function labelFor(key: string): string {
  return key
    .replace(/_/g, " ")
    .replace(/\b\w/g, (char) => char.toUpperCase());
}

function extractReadable(value: unknown, depth = 0): string | null {
  if (depth > 8 || value == null) return null;

  if (typeof value === "string") {
    const trimmed = value.trim();
    if (!trimmed) return null;
    const parsed = parseStructuredString(trimmed);
    if (parsed != null) {
      const extracted = extractReadable(parsed, depth + 1);
      if (extracted) return extracted;
    }
    return trimmed;
  }

  if (typeof value === "number" || typeof value === "boolean") {
    return String(value);
  }

  if (Array.isArray(value)) {
    const parts = value
      .map((item) => extractReadable(item, depth + 1))
      .filter((item): item is string => Boolean(item));
    return parts.length > 0 ? parts.join("\n") : null;
  }

  if (isRecord(value)) {
    for (const key of READABLE_KEYS) {
      if (Object.prototype.hasOwnProperty.call(value, key)) {
        const extracted = extractReadable(value[key], depth + 1);
        if (extracted) return extracted;
      }
    }

    const lines = Object.entries(value).flatMap(([key, val]) => {
      if (INTERNAL_KEYS.has(key)) return [];
      if (val == null || typeof val === "object") return [];
      const text = extractReadable(val, depth + 1);
      return text ? [`${labelFor(key)}: ${text}`] : [];
    });
    return lines.length > 0 ? lines.join("\n") : null;
  }

  return null;
}

export function extractReadableAgentOutput(
  value: unknown,
  fallback = "Task completed successfully.",
): string {
  return extractReadable(value) || fallback;
}
