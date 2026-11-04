import { useTranslation } from "react-i18next";
import { Card, CardHeader, CardTitle, CardContent } from "./ui/card";
import { Button } from "./ui/button";
import { Badge } from "./ui/badge";
import type { HITLItem } from "@/types";

interface Props { item: HITLItem; onDecide: (id: string, decision: string, notes: string) => void; readonly?: boolean; }

export default function ApprovalCard({ item, onDecide, readonly }: Props) {
  const { t } = useTranslation();
  const outcome = item.decision || item.status;
  return (
    <Card>
      <CardHeader>
        <div className="flex justify-between items-start">
          <CardTitle className="text-lg">{item.title}</CardTitle>
          <Badge variant={item.priority === "critical" ? "destructive" : "warning"}>{t(`approvals.priorityBadge.${item.priority}`, { defaultValue: item.priority })}</Badge>
        </div>
        <p className="text-sm text-muted-foreground">{t("approvals.triggerAndRole", { trigger: item.trigger_type, role: item.assignee_role })}</p>
      </CardHeader>
      <CardContent>
        <details className="mb-4"><summary className="cursor-pointer text-sm font-medium">{t("approvals.reasoningTrace")}</summary>
          <pre className="mt-2 text-xs bg-muted p-3 rounded overflow-auto max-h-40">{JSON.stringify(item.context, null, 2)}</pre>
        </details>
        {readonly ? (
          <div className="flex items-center gap-2">
            <span className="text-sm text-muted-foreground">{t("approvals.decisionLabel")}</span>
            <Badge variant={item.decision === "approve" ? "default" : item.decision === "reject" ? "destructive" : "secondary"}>
              {t(`approvals.outcome.${outcome}`, { defaultValue: outcome.charAt(0).toUpperCase() + outcome.slice(1) })}
            </Badge>
            {item.decision_at && <span className="text-xs text-muted-foreground ml-2">{new Date(item.decision_at).toLocaleString()}</span>}
          </div>
        ) : (
          <div className="flex gap-2">
            <Button variant="default" onClick={() => onDecide(item.id, "approve", "")}>{t("approvals.approve")}</Button>
            <Button variant="destructive" onClick={() => onDecide(item.id, "reject", "")}>{t("approvals.reject")}</Button>
            <Button variant="outline" onClick={() => onDecide(item.id, "defer", "")}>{t("approvals.defer")}</Button>
          </div>
        )}
      </CardContent>
    </Card>
  );
}
