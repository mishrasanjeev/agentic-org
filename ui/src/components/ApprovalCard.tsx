import { Card, CardHeader, CardTitle, CardContent } from "./ui/card";
import { Button } from "./ui/button";
import { Badge } from "./ui/badge";
import type { HITLItem } from "@/types";

interface Props { item: HITLItem; onDecide: (id: string, decision: string, notes: string) => void; }

export default function ApprovalCard({ item, onDecide }: Props) {
  return (
    <Card>
      <CardHeader>
        <div className="flex justify-between items-start">
          <CardTitle className="text-lg">{item.title}</CardTitle>
          <Badge variant={item.priority === "critical" ? "destructive" : "warning"}>{item.priority}</Badge>
        </div>
        <p className="text-sm text-muted-foreground">Trigger: {item.trigger_type} | Role: {item.assignee_role}</p>
      </CardHeader>
      <CardContent>
        <details className="mb-4"><summary className="cursor-pointer text-sm font-medium">Reasoning Trace</summary>
          <pre className="mt-2 text-xs bg-muted p-3 rounded overflow-auto max-h-40">{JSON.stringify(item.context, null, 2)}</pre>
        </details>
        <div className="flex gap-2">
          <Button variant="default" onClick={() => onDecide(item.id, "approve", "")}>Approve</Button>
          <Button variant="destructive" onClick={() => onDecide(item.id, "reject", "")}>Reject</Button>
          <Button variant="outline" onClick={() => onDecide(item.id, "defer", "")}>Defer</Button>
        </div>
      </CardContent>
    </Card>
  );
}
