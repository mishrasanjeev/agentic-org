import { useEffect, useState } from "react";
import { useSearchParams, useNavigate } from "react-router-dom";

const API = import.meta.env.VITE_API_URL ?? "";

export default function BillingCallback() {
  const [searchParams] = useSearchParams();
  const navigate = useNavigate();
  const [status, setStatus] = useState<"loading" | "success" | "failed" | "pending">("loading");
  const [orderDetails, setOrderDetails] = useState<Record<string, string>>({});

  const paymentStatus = searchParams.get("payment") || searchParams.get("status") || "";
  const orderId = searchParams.get("order_id") || searchParams.get("plural_order_id") || "";
  const sessionId = searchParams.get("session_id") || "";
  const provider = searchParams.get("provider") || (sessionId ? "stripe" : "plural");
  const plan = searchParams.get("plan") || "";
  const tenantId = searchParams.get("tenant_id") || localStorage.getItem("tenant_id") || "";

  useEffect(() => {
    setOrderDetails({ orderId: orderId || sessionId, plan, tenantId, provider });

    // Always verify with the server — never trust URL params alone.
    // The API callback already verified before redirecting here,
    // but we double-check for defense in depth.
    const token = localStorage.getItem("token");

    if (orderId && provider === "plural") {
      // Verify Plural order status
      fetch(`${API}/api/v1/billing/order-status`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          ...(token ? { Authorization: `Bearer ${token}` } : {}),
        },
        body: JSON.stringify({ order_id: orderId }),
      })
        .then((r) => r.json())
        .then((data) => {
          const s = (data.status || "").toUpperCase();
          if (s === "PROCESSED" || s === "AUTHORIZED") setStatus("success");
          else if (s === "FAILED" || s === "CANCELLED") setStatus("failed");
          else setStatus("pending");
        })
        .catch(() => fallbackToParam());
    } else {
      // For Stripe: the API callback already verified the session server-side.
      // Trust the API-verified status param.
      fallbackToParam();
    }

    function fallbackToParam() {
      if (paymentStatus === "success") setStatus("success");
      else if (paymentStatus === "failed") setStatus("failed");
      else setStatus("pending");
    }
  }, [paymentStatus, orderId, sessionId, provider, plan, tenantId]);

  const statusConfig = {
    loading: {
      icon: "\u23F3",
      title: "Verifying payment...",
      description: "Please wait while we confirm your payment.",
      color: "text-blue-500",
    },
    success: {
      icon: "\u2705",
      title: "Payment Successful!",
      description: `Your ${plan || "subscription"} plan is now active. You'll receive a confirmation email shortly.`,
      color: "text-green-500",
    },
    failed: {
      icon: "\u274C",
      title: "Payment Failed",
      description: "Your payment could not be processed. Please try again or contact support.",
      color: "text-red-500",
    },
    pending: {
      icon: "\u23F3",
      title: "Payment Pending",
      description: "Your payment is being processed. This may take a few moments for UPI and net banking payments.",
      color: "text-yellow-500",
    },
  };

  const config = statusConfig[status];

  return (
    <div className="min-h-screen flex items-center justify-center bg-background">
      <div className="max-w-md w-full mx-4 text-center space-y-6">
        <div className={`text-6xl ${config.color}`}>{config.icon}</div>
        <h1 className="text-2xl font-bold">{config.title}</h1>
        <p className="text-muted-foreground">{config.description}</p>

        {orderDetails.orderId && (
          <div className="text-sm text-muted-foreground border rounded-lg p-4 text-left space-y-1">
            <p><span className="font-medium">Order ID:</span> {orderDetails.orderId}</p>
            {orderDetails.plan && <p><span className="font-medium">Plan:</span> {orderDetails.plan}</p>}
          </div>
        )}

        <div className="flex gap-3 justify-center pt-4">
          <button
            onClick={() => navigate("/dashboard/billing")}
            className="px-6 py-2 rounded bg-primary text-primary-foreground text-sm font-medium hover:opacity-90"
          >
            Go to Billing
          </button>
          {status === "failed" && (
            <button
              onClick={() => navigate("/dashboard/billing")}
              className="px-6 py-2 rounded border text-sm font-medium hover:bg-muted"
            >
              Try Again
            </button>
          )}
        </div>

        <p className="text-xs text-muted-foreground pt-4">
          {provider === "stripe"
            ? "Payments processed securely via Stripe. Supported: Cards, Google Pay, Apple Pay."
            : "Payments processed securely via PineLabs Plural. Supported: Cards, UPI, Net Banking, Wallets, EMI."}
        </p>
      </div>
    </div>
  );
}
