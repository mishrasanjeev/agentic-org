import { Card, CardHeader, CardTitle, CardContent } from "@/components/ui/card";
import LiveFeed from "@/components/LiveFeed";

export default function Dashboard() {
  return (
    <div className="space-y-6">
      <h2 className="text-2xl font-bold">Dashboard</h2>
      <div className="grid grid-cols-4 gap-4">
        {[["Active Agents", "24"], ["STP Rate", "94.2%"], ["Pending HITL", "3"], ["Workflows Today", "142"]].map(([label, value]) => (
          <Card key={label}><CardHeader><CardTitle className="text-sm text-muted-foreground">{label}</CardTitle></CardHeader>
            <CardContent><p className="text-3xl font-bold">{value}</p></CardContent></Card>
        ))}
      </div>
      <LiveFeed tenantId="default" />
    </div>
  );
}
