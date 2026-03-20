import React from "react";
import { cn } from "@/lib/utils";

interface ButtonProps extends React.ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: "default" | "destructive" | "outline" | "ghost";
  size?: "default" | "sm" | "lg";
}

export const Button = React.forwardRef<HTMLButtonElement, ButtonProps>(
  ({ className, variant = "default", size = "default", ...props }, ref) => {
    const variants = {
      default: "bg-primary text-primary-foreground hover:bg-primary/90",
      destructive: "bg-destructive text-destructive-foreground hover:bg-destructive/90",
      outline: "border border-border bg-background hover:bg-accent",
      ghost: "hover:bg-accent hover:text-accent-foreground",
    };
    const sizes = { default: "h-10 px-4 py-2", sm: "h-9 px-3 text-sm", lg: "h-11 px-8" };
    return <button ref={ref} className={cn("inline-flex items-center justify-center rounded-md font-medium transition-colors disabled:opacity-50", variants[variant], sizes[size], className)} {...props} />;
  }
);
Button.displayName = "Button";
