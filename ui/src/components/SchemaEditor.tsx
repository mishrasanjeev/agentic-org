import Editor from "@monaco-editor/react";

export default function SchemaEditor({ schema, onChange, readOnly }: { schema?: any; onChange?: (v: string) => void; readOnly?: boolean }) {
  return (
    <Editor height="400px" language="json" theme="vs-dark"
      value={schema ? JSON.stringify(schema, null, 2) : "{}"}
      onChange={(v) => onChange?.(v || "")}
      options={{ readOnly, minimap: { enabled: false } }} />
  );
}
