import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { HelmetProvider } from "react-helmet-async";
import { AuthProvider } from "./contexts/AuthContext";
import { BrandingProvider } from "./contexts/BrandingContext";
import App from "./App";
import ErrorBoundary from "./components/ErrorBoundary";
// Self-hosted fonts — loaded via Vite bundler so there is no external
// CSS request that a strict CSP (style-src) would block. See the
// impeccable pass 1 notes in docs/impeccable-applied.md.
import "@fontsource/geist/400.css";
import "@fontsource/geist/500.css";
import "@fontsource/geist/600.css";
import "@fontsource/geist/700.css";
import "@fontsource/jetbrains-mono/400.css";
import "@fontsource/jetbrains-mono/500.css";
import "./globals.css";

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 0,          // Always treat data as stale so it refetches on mount
      refetchOnMount: true,   // Refetch when component mounts
      refetchOnWindowFocus: false, // Avoid double-fetches on tab switch
      retry: 1,               // One retry on transient failures
    },
  },
});

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <HelmetProvider>
      <ErrorBoundary>
        <QueryClientProvider client={queryClient}>
          <BrowserRouter>
            <BrandingProvider>
              <AuthProvider>
                <App />
              </AuthProvider>
            </BrandingProvider>
          </BrowserRouter>
        </QueryClientProvider>
      </ErrorBoundary>
    </HelmetProvider>
  </React.StrictMode>
);
