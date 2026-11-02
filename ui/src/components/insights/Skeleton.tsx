// SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
import { cn } from "@/lib/utils";

/** Placeholder block shown while a view loads; never a spinner over stale numbers. */
export function SkeletonBlock({ className }: { className?: string }) {
  return <div aria-hidden="true" className={cn("animate-pulse rounded-md bg-muted", className)} />;
}

export function TileSkeleton() {
  return (
    <div className="rounded-lg border bg-background p-4" data-testid="tile-skeleton">
      <SkeletonBlock className="h-3 w-28" />
      <SkeletonBlock className="mt-3 h-7 w-24" />
      <SkeletonBlock className="mt-3 h-6 w-full" />
    </div>
  );
}

export function ChartSkeleton() {
  return (
    <div className="rounded-lg border bg-background p-4" data-testid="chart-skeleton">
      <SkeletonBlock className="h-4 w-40" />
      <SkeletonBlock className="mt-4 h-48 w-full" />
    </div>
  );
}
