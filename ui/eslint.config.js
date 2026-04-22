// Codex 2026-04-22 audit: npm run lint failed because ESLint v10 uses
// a flat config (eslint.config.js) and the repo did not have one. This
// config registers @typescript-eslint/parser so TS files parse, and
// only enables correctness-class rules so the gate stays cheap.
//
// Stylistic lint is Prettier's job. Broad TS-aware rules are a
// separate initiative (they'd light up thousands of existing lines and
// make the gate useless).

import js from "@eslint/js";
import tsParser from "@typescript-eslint/parser";
import reactHooks from "eslint-plugin-react-hooks";
import globals from "globals";

export default [
  {
    ignores: [
      "dist/**",
      "node_modules/**",
      "coverage/**",
      "playwright-report/**",
      "test-results/**",
      "e2e/**",
      "tests/**",
      "scripts/**",
      "**/*.cjs",
      "**/*.mjs",
      "**/*.json",
      "**/*.md",
    ],
  },
  {
    ...js.configs.recommended,
    files: ["src/**/*.{ts,tsx,js,jsx}"],
    languageOptions: {
      parser: tsParser,
      ecmaVersion: 2022,
      sourceType: "module",
      parserOptions: {
        ecmaFeatures: { jsx: true },
      },
      globals: {
        ...globals.browser,
        ...globals.node,
      },
    },
    plugins: {
      "react-hooks": reactHooks,
    },
    rules: {
      // Base correctness from js/recommended, plus a few silencings
      // where the TS compiler is authoritative.
      ...js.configs.recommended.rules,
      "no-undef": "off",
      "no-unused-vars": "off",
      "no-empty": ["warn", { allowEmptyCatch: true }],
      "no-constant-binary-expression": "warn",
      "no-debugger": "warn",
      // JSX / TSX-specific tolerances.
      "no-redeclare": "off", // allow TS function overload signatures
      "no-useless-escape": "off", // regex escaping is fine as-is
      "no-extra-boolean-cast": "warn",
      // react-hooks rules — register so legitimate hooks bugs
      // surface, and inline disable directives are recognized.
      "react-hooks/rules-of-hooks": "error",
      "react-hooks/exhaustive-deps": "warn",
    },
  },
];
