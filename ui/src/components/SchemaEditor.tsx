import Editor from "@monaco-editor/react";
import { useEffect, useMemo, useState } from "react";

/**
 * JSON-schema editor. Monaco provides rich editing, but it lazy-loads
 * language workers from a CDN at runtime; when that fails (strict CSP,
 * offline, corporate proxy) the editor sits on "Loading..." forever.
 *
 * TC_012/TC_013 (Aishwarya 2026-04-22): "Create Schema" and "Open
 * existing schema" both stuck on "Loading..." because the Monaco
 * worker never arrived. We now render a lightweight textarea
 * fallback if Monaco hasn't mounted within 2.5s, and keep a
 * manual toggle so users can always drop back to the plain editor.
 */
export default function SchemaEditor({
  schema,
  onChange,
  readOnly,
}: {
  schema?: unknown;
  onChange?: (v: string) => void;
  readOnly?: boolean;
}) {
  const initialValue = useMemo(
    () => (schema ? JSON.stringify(schema, null, 2) : "{}"),
    [schema],
  );
  const [useFallback, setUseFallback] = useState(false);
  const [value, setValue] = useState(initialValue);
  const [monacoMounted, setMonacoMounted] = useState(false);

  // Kick the fallback after 2.5s if Monaco never mounted.
  useEffect(() => {
    const t = setTimeout(() => {
      if (!monacoMounted) setUseFallback(true);
    }, 2500);
    return () => clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  if (useFallback) {
    return (
      <div className="space-y-2">
        <p className="text-xs text-amber-700 bg-amber-50 border border-amber-200 rounded px-2 py-1">
          Advanced editor unavailable (worker failed to load). Using a
          plain JSON textarea.{" "}
          <button
            className="underline ml-1"
            onClick={() => setUseFallback(false)}
          >
            Retry advanced editor
          </button>
        </p>
        <textarea
          className="w-full h-96 border rounded p-2 font-mono text-xs"
          value={value}
          readOnly={readOnly}
          onChange={(e) => {
            setValue(e.target.value);
            onChange?.(e.target.value);
          }}
        />
      </div>
    );
  }

  return (
    <div className="space-y-1">
      <Editor
        height="400px"
        language="json"
        theme="vs-dark"
        value={value}
        onMount={() => setMonacoMounted(true)}
        onChange={(v) => {
          setValue(v || "");
          onChange?.(v || "");
        }}
        options={{ readOnly, minimap: { enabled: false } }}
      />
      <div className="text-right">
        <button
          className="text-xs text-muted-foreground underline"
          onClick={() => setUseFallback(true)}
        >
          Switch to plain editor
        </button>
      </div>
    </div>
  );
}
