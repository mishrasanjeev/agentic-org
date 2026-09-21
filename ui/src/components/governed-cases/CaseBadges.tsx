// SPDX-License-Identifier: Apache-2.0
import { Badge } from "@/components/ui/badge";
import {
  RECOMMENDATION_LABELS,
  STATE_LABELS,
  TIER_LABELS,
  type CaseState,
  type Recommendation,
  type RiskTier,
  type SectionStatus,
  type Severity,
} from "@/lib/governedCases";

type Variant = "default" | "success" | "warning" | "destructive" | "secondary" | "outline";

const STATE_VARIANTS: Record<CaseState, Variant> = {
  submitted: "secondary",
  in_progress: "default",
  awaiting_decision: "warning",
  decided: "success",
  withdrawn: "outline",
  failed: "destructive",
};

const TIER_VARIANTS: Record<RiskTier, Variant> = {
  low: "success",
  medium: "warning",
  high: "destructive",
  blocked: "destructive",
};

const SEVERITY_VARIANTS: Record<Severity, Variant> = {
  info: "secondary",
  low: "outline",
  medium: "warning",
  high: "destructive",
};

const SECTION_STATUS: Record<SectionStatus, { label: string; variant: Variant }> = {
  complete: { label: "Complete", variant: "success" },
  partial: { label: "Partial", variant: "warning" },
  not_available: { label: "Not available", variant: "secondary" },
  error: { label: "Provider error", variant: "destructive" },
};

export function StateBadge({ state }: { state: CaseState }) {
  return (
    <Badge variant={STATE_VARIANTS[state] ?? "outline"} data-testid="case-state">
      {STATE_LABELS[state] ?? state}
    </Badge>
  );
}

/** Tier text is always shown: colour alone never carries the risk level. */
export function TierBadge({ tier }: { tier: RiskTier | null }) {
  if (!tier) return <span className="text-muted-foreground">No tier</span>;
  return (
    <Badge variant={TIER_VARIANTS[tier] ?? "outline"} data-testid="case-tier">
      {TIER_LABELS[tier] ?? tier} risk
    </Badge>
  );
}

export function RecommendationText({ recommendation }: { recommendation: Recommendation | null }) {
  if (!recommendation) return <span className="text-muted-foreground">No memo yet</span>;
  return <span>{RECOMMENDATION_LABELS[recommendation] ?? recommendation}</span>;
}

export function SeverityBadge({ severity }: { severity: Severity }) {
  return <Badge variant={SEVERITY_VARIANTS[severity] ?? "outline"}>{severity} severity</Badge>;
}

export function SectionStatusBadge({ status }: { status: SectionStatus }) {
  const view = SECTION_STATUS[status] ?? { label: status, variant: "outline" as Variant };
  return (
    <Badge variant={view.variant} data-testid="section-status">
      {view.label}
    </Badge>
  );
}
