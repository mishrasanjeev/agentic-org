import { useEffect } from "react";
import { useLocation } from "react-router-dom";

const GA_ID = import.meta.env.VITE_GA4_ID || "";

/** Fire a GA4 custom event. Safe to call even if GA is not loaded. */
export function trackEvent(
  eventName: string,
  params?: Record<string, string | number | boolean>,
) {
  if (!GA_ID || !window.gtag) return;
  window.gtag("event", eventName, params);
}

/** Set user properties for GA4 (role, plan, tenant). Call after login. */
export function identifyUser(props: {
  user_id?: string;
  role?: string;
  plan?: string;
  tenant_id?: string;
}) {
  if (!GA_ID || !window.gtag) return;
  if (props.user_id) {
    window.gtag("config", GA_ID, { user_id: props.user_id });
  }
  window.gtag("set", "user_properties", {
    role: props.role || "",
    plan: props.plan || "",
    tenant_id: props.tenant_id || "",
  });
}

export default function Analytics() {
  const location = useLocation();

  useEffect(() => {
    if (!GA_ID) return;
    // Load gtag.js once
    if (!document.getElementById("ga-script")) {
      const script = document.createElement("script");
      script.id = "ga-script";
      script.async = true;
      script.src = `https://www.googletagmanager.com/gtag/js?id=${GA_ID}`;
      document.head.appendChild(script);

      const inline = document.createElement("script");
      inline.textContent = `
        window.dataLayer = window.dataLayer || [];
        function gtag(){dataLayer.push(arguments);}
        gtag('js', new Date());
        gtag('config', '${GA_ID}', { send_page_view: false });
      `;
      document.head.appendChild(inline);
    }
  }, []);

  // Track page views on route change
  useEffect(() => {
    if (!GA_ID || !window.gtag) return;
    window.gtag("event", "page_view", {
      page_path: location.pathname,
      page_title: document.title,
    });
  }, [location]);

  return null;
}
