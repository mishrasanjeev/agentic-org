// SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
import { useCallback, useEffect, useState } from "react";
import { CreditCard } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { extractApiError, paymentsApi } from "../../lib/api";

type Mode = "shopify_cart" | "plural_hosted" | "off";

interface PaymentsView {
  merchant_id: string;
  mode: Mode;
  shopify: { shop_domain: string | null; ready: boolean };
  plural: {
    configured: boolean;
    environment?: "sandbox" | "production";
    webhook_secret_set?: boolean;
    webhook_url?: string | null;
    last_test?: { ok: boolean; code: string; at: string } | null;
  };
  links_available: boolean;
}

const MODES: { value: Mode; title: string; detail: string }[] = [
  {
    value: "shopify_cart",
    title: "My Shopify checkout",
    detail: "Buyers get a link to your own Shopify cart and pay there. The order appears in your Shopify admin.",
  },
  {
    value: "plural_hosted",
    title: "My Plural account",
    detail: "Buyers pay on a Plural payment page with your Plural account. The order appears on the Orders page.",
  },
  { value: "off", title: "Off", detail: "Buyers get no payment link; they finish with you directly." },
];

function errorDetail(e: unknown): string {
  const detail = (e as any)?.response?.data?.detail;
  if (detail && typeof detail === "object" && typeof detail.message === "string") return detail.message;
  return extractApiError(e, "The request failed. Try again.");
}

/** Choose how buyers pay (always on the merchant's own page) and keep the merchant's Plural credentials. */
export default function PaymentSettings({ merchantId }: { merchantId: string }) {
  const [view, setView] = useState<PaymentsView | null>(null);
  const [mode, setMode] = useState<Mode>("off");
  const [environment, setEnvironment] = useState<"sandbox" | "production">("sandbox");
  const [clientId, setClientId] = useState("");
  const [clientSecret, setClientSecret] = useState("");
  const [webhookSecret, setWebhookSecret] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const apply = (data: PaymentsView) => {
    setView(data);
    setMode(data.mode);
    if (data.plural.environment) setEnvironment(data.plural.environment);
  };

  const load = useCallback(async () => {
    if (!merchantId) return;
    setError(null);
    try {
      const { data } = await paymentsApi.get(merchantId);
      apply(data);
    } catch (e) {
      setError(errorDetail(e));
    }
  }, [merchantId]);

  useEffect(() => {
    void load();
  }, [load]);

  const run = async (action: () => Promise<void>) => {
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      await action();
    } catch (e) {
      setError(errorDetail(e));
    } finally {
      setBusy(false);
    }
  };

  const saveMode = () =>
    run(async () => {
      const { data } = await paymentsApi.setMode(merchantId, mode);
      apply(data);
      setNotice("Payment settings saved.");
    });

  const saveCredentials = () =>
    run(async () => {
      const { data } = await paymentsApi.saveCredentials(merchantId, {
        environment,
        client_id: clientId.trim(),
        ...(clientSecret.trim() ? { client_secret: clientSecret.trim() } : {}),
        ...(webhookSecret.trim() ? { webhook_secret: webhookSecret.trim() } : {}),
      });
      setClientSecret("");
      setWebhookSecret("");
      apply(data);
      setNotice("Plural credentials saved. Secrets are stored encrypted and never shown again.");
    });

  const testCredentials = () =>
    run(async () => {
      const { data } = await paymentsApi.testCredentials(merchantId);
      setNotice(data.ok ? "Plural accepted the credentials." : `Plural did not accept the credentials (${data.code}).`);
      await load();
    });

  const plural = view?.plural;
  const canSaveCredentials = clientId.trim() !== "" && (Boolean(plural?.configured) || clientSecret.trim() !== "");

  return (
    <section className="space-y-4 rounded-xl border p-5" aria-label="Getting paid">
      <div>
        <h3 className="flex items-center gap-2 text-lg font-semibold">
          <CreditCard className="h-5 w-5" /> Getting paid
        </h3>
        <p className="mt-1 max-w-3xl text-sm text-muted-foreground">
          AgenticOrg never takes your buyers&apos; money. When a checkout is ready, the buyer gets a link to your own payment
          page and pays you there.
        </p>
      </div>

      {error ? (
        <div className="rounded-lg border border-destructive/30 bg-destructive/10 px-4 py-3 text-sm text-destructive" role="alert">
          {error}
        </div>
      ) : null}
      {notice ? (
        <div className="rounded-lg border bg-muted/40 px-4 py-3 text-sm" role="status">
          {notice}
        </div>
      ) : null}

      <fieldset className="space-y-2" disabled={busy || !view}>
        <legend className="text-sm font-medium">Where buyers pay</legend>
        {MODES.map((option) => (
          <label key={option.value} className="flex items-start gap-3 rounded-lg border p-3 text-sm">
            <input
              type="radio"
              name="payment-mode"
              className="mt-1"
              value={option.value}
              checked={mode === option.value}
              onChange={() => setMode(option.value)}
            />
            <span>
              <span className="block font-medium">{option.title}</span>
              <span className="block text-muted-foreground">{option.detail}</span>
              {option.value === "shopify_cart" && view ? (
                <span className="mt-1 block">
                  {view.shopify.ready ? (
                    <Badge variant="success">Connected: {view.shopify.shop_domain}</Badge>
                  ) : (
                    <Badge variant="warning">Connect your Shopify store first</Badge>
                  )}
                </span>
              ) : null}
            </span>
          </label>
        ))}
        <Button disabled={busy || !view || mode === view?.mode} onClick={() => void saveMode()}>
          Save payment method
        </Button>
      </fieldset>

      <div className="space-y-3 rounded-lg border p-4" aria-label="Plural credentials">
        <div className="flex flex-wrap items-center gap-2">
          <p className="font-medium">Your Plural account</p>
          {plural?.configured ? <Badge variant="success">Saved</Badge> : <Badge variant="secondary">Not saved</Badge>}
          {plural?.last_test ? (
            plural.last_test.ok ? (
              <Badge variant="success">Test passed</Badge>
            ) : (
              <Badge variant="destructive">Test failed</Badge>
            )
          ) : null}
        </div>
        <div className="grid max-w-xl gap-3">
          <label className="flex flex-col gap-1 text-sm">
            Environment
            <select
              className="h-11 rounded-md border px-3 sm:h-10"
              value={environment}
              onChange={(e) => setEnvironment(e.target.value as "sandbox" | "production")}
            >
              <option value="sandbox">Sandbox (test payments)</option>
              <option value="production">Production</option>
            </select>
          </label>
          <label className="flex flex-col gap-1 text-sm">
            Client ID
            <input className="h-11 rounded-md border px-3 sm:h-10" value={clientId} onChange={(e) => setClientId(e.target.value)} autoComplete="off" />
          </label>
          <label className="flex flex-col gap-1 text-sm">
            Client secret
            <input
              type="password"
              className="h-11 rounded-md border px-3 sm:h-10"
              value={clientSecret}
              onChange={(e) => setClientSecret(e.target.value)}
              autoComplete="new-password"
              placeholder={plural?.configured ? "Leave blank to keep the saved secret" : ""}
            />
          </label>
          <label className="flex flex-col gap-1 text-sm">
            Webhook secret
            <input
              type="password"
              className="h-11 rounded-md border px-3 sm:h-10"
              value={webhookSecret}
              onChange={(e) => setWebhookSecret(e.target.value)}
              autoComplete="new-password"
              placeholder={plural?.webhook_secret_set ? "Leave blank to keep the saved secret" : ""}
            />
          </label>
        </div>
        {plural?.webhook_url ? (
          <p className="break-all text-sm">
            Add this webhook URL in your Plural dashboard: <code>{plural.webhook_url}</code>
          </p>
        ) : null}
        <div className="flex flex-wrap gap-2">
          <Button variant="outline" disabled={busy || !canSaveCredentials} onClick={() => void saveCredentials()}>
            Save Plural credentials
          </Button>
          <Button variant="outline" disabled={busy || !plural?.configured} onClick={() => void testCredentials()}>
            Test
          </Button>
        </div>
      </div>
    </section>
  );
}
