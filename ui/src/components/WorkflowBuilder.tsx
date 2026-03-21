import ReactFlow, { Background, Controls, MiniMap } from "reactflow";
import "reactflow/dist/style.css";

export default function WorkflowBuilder({ definition }: { definition: any; onChange?: (d: any) => void }) {
  const nodes = (definition?.steps || []).map((s: any, i: number) => ({
    id: s.id, position: { x: 100, y: i * 120 }, data: { label: `${s.type}: ${s.id}` },
  }));
  return (
    <div style={{ height: 500 }}>
      <ReactFlow nodes={nodes} edges={[]}><Background /><Controls /><MiniMap /></ReactFlow>
    </div>
  );
}
