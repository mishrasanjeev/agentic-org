import type { Config } from "tailwindcss";
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  darkMode: "class",
  theme: {
    extend: {
      colors: {
        border: "hsl(var(--border))", background: "hsl(var(--background))",
        foreground: "hsl(var(--foreground))", primary: { DEFAULT: "hsl(var(--primary))", foreground: "hsl(var(--primary-foreground))" },
        muted: { DEFAULT: "hsl(var(--muted))", foreground: "hsl(var(--muted-foreground))" },
        accent: { DEFAULT: "hsl(var(--accent))", foreground: "hsl(var(--accent-foreground))" },
        destructive: { DEFAULT: "hsl(var(--destructive))", foreground: "hsl(var(--destructive-foreground))" },
      },
      borderRadius: { lg: "var(--radius)", md: "calc(var(--radius) - 2px)", sm: "calc(var(--radius) - 4px)" },
      /* Motion design per impeccable/motion-design.md:
         avoid `ease` (bland), bounce, elastic. Exponential curves
         mimic real-world friction and read as confident. Use via
         `ease-out-quart`, `ease-out-quint`, `ease-out-expo`. */
      transitionTimingFunction: {
        "out-quart": "cubic-bezier(0.25, 1, 0.5, 1)",
        "out-quint": "cubic-bezier(0.22, 1, 0.36, 1)",
        "out-expo":  "cubic-bezier(0.16, 1, 0.3, 1)",
        "in-quart":  "cubic-bezier(0.5, 0, 0.75, 0)",
        "in-out-quart": "cubic-bezier(0.76, 0, 0.24, 1)",
      },
    },
  },
  plugins: [],
} satisfies Config;
