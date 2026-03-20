import React from "react";
import { cn } from "@/lib/utils";
interface BadgeProps extends React.HTMLAttributes<HTMLDivElement> { variant?: "default" | "success" | "warning" | "destructive"; }
export function Badge({ className, variant = "default", ...props }: BadgeProps) {
  const variants = { default: "bg-primary/10 text-primary", success: "bg-green-100 text-green-800", warning: "bg-yellow-100 text-yellow-800", destructive: "bg-red-100 text-red-800" };
  return <div className={cn("inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-semibold", variants[variant], className)} {...props} />;
}
