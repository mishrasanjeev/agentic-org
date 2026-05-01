import { useEffect, useState } from "react";
import { useSearchParams, useNavigate } from "react-router-dom";
import { useAuth } from "@/contexts/AuthContext";

const API = import.meta.env.VITE_API_URL ?? "";

export default function BillingCallback() {
  const [searchParams] = useSearchParams();
  const navigate = useNavigate();
  const { isAuthenticated } = useAuth();
  const [status, setStatus] = useState<"loading" | "success" | "failed" | "pending">("loading");
  const [orderDetails, setOrderDetails] = useState<Record<string, string>>({});
  const [countdown, setCountdown] = useState(5);
  // SEC-002 (PR-F): cookie-first. ``isAuthenticated`` reflects the
  // /auth/me hydration result; the HttpOnly session cookie is the
  // browser auth carrier and is shipped automatically on the
  // billing/order-status fetch via ``credentials: "include"``.
  const isLoggedIn = isAuthenticated;

  const paymentStatus = searchParams.get("payment") || searchParams.get("status") || "";
  const orderId = searchParams.get("order_id") || searchParams.get("plural_order_id") || "";
  const sessionId = searchParams.get("session_id") || "";
  const provider = searchParams.get("provider") || (sessionId ? "stripe" : "plural");
  const plan = searchParams.get("plan") || "";

  const billingDest = isLoggedIn
    ? "/dashboard/billing"
    : `/login?next=${encodeURIComponent("/dashboard/billing")}`;

  useEffect(() => {
    setOrderDetails({ orderId: orderId || sessionId, plan, provider });

    if (orderId && provider === "plural") {
      fetch(`${API}/api/v1/billing/order-status`, {
        method: "POST",
        credentials: "include",
        headers: {
          "Content-Type": "application/json",
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
      fallbackToParam();
    }

    function fallbackToParam() {
      if (paymentStatus === "success") setStatus("success");
      else if (paymentStatus === "failed") setStatus("failed");
      else setStatus("pending");
    }
  }, [paymentStatus, orderId, sessionId, provider, plan]);

  // Auto-redirect to billing page on success after countdown
  useEffect(() => {
    if (status !== "success" || !isLoggedIn) return;
    if (countdown <= 0) {
      navigate("/dashboard/billing", { replace: true });
      return;
    }
    const timer = setTimeout(() => setCountdown((c) => c - 1), 1000);
    return () => clearTimeout(timer);
  }, [status, isLoggedIn, countdown, navigate]);

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

        {/* Auto-redirect countdown for successful logged-in users */}
        {status === "success" && isLoggedIn && (
          <p className="text-sm text-muted-foreground">
            Redirecting to billing in {countdown}s...
          </p>
        )}

        {/* Session expired notice */}
        {!isLoggedIn && status === "success" && (
          <p className="text-sm text-amber-700 bg-amber-50 border border-amber-200 rounded-lg px-3 py-2">
            Your sign-in session expired during the payment. Sign in again to
            view your active subscription.
          </p>
        )}

        <div className="flex gap-3 justify-center pt-4">
          <button
            onClick={() => navigate(billingDest)}
            className="px-6 py-2 rounded bg-primary text-primary-foreground text-sm font-medium hover:opacity-90"
          >
            {isLoggedIn ? "Go to Billing" : "Sign in to view billing"}
          </button>
          {status === "failed" && (
            <button
              onClick={() => navigate(billingDest)}
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
