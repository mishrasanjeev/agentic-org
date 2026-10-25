import { useCallback, useEffect, useState, FormEvent } from "react";
import { Card, CardHeader, CardTitle, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import api from "@/lib/api";
import { useAuth } from "@/contexts/AuthContext";

interface MfaStatus {
  enabled: boolean;
  enabled_at: string | null;
  recovery_codes_remaining: number;
}

interface MfaSetup {
  qr_code: string;
  secret: string;
  account: string;
  issuer: string;
}

type Mode = "idle" | "setup" | "disable" | "regenerate";

interface Member {
  id: string;
  email: string;
  name: string | null;
  role: string;
  status: string;
  mfa_enabled: boolean;
}

function errorDetail(err: any, fallback: string): string {
  const detail = err?.response?.data?.detail;
  return typeof detail === "string" && detail ? detail : fallback;
}

function CodeInput({ id, value, onChange, label }: { id: string; value: string; onChange: (v: string) => void; label: string }) {
  return (
    <div>
      <label htmlFor={id} className="block text-sm font-medium mb-1.5">{label}</label>
      <input
        id={id}
        type="text"
        inputMode="numeric"
        autoComplete="one-time-code"
        autoFocus
        maxLength={11}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder="123456"
        className="w-44 rounded-lg border border-border bg-background px-3 py-2 text-center text-lg tracking-[0.3em] focus:outline-none focus:ring-2 focus:ring-primary/50"
      />
    </div>
  );
}

/** Admins: who has two-factor sign-in, and a reset for a member who lost their phone. */
function MembersMfa({ selfEmail }: { selfEmail: string }) {
  const [members, setMembers] = useState<Member[] | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const { data } = await api.get<Member[]>("/org/members");
      setMembers(data.filter((m) => m.status === "active"));
    } catch (err) {
      setMessage(errorDetail(err, "Could not load members"));
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const resetMember = async (member: Member) => {
    if (!window.confirm(`Turn off two-factor sign-in for ${member.email}? They will be signed out everywhere and can set it up again.`)) return;
    setBusyId(member.id);
    setMessage(null);
    try {
      await api.delete(`/org/members/${member.id}/mfa`);
      setMessage(`Two-factor sign-in was reset for ${member.email}.`);
      await load();
    } catch (err) {
      setMessage(errorDetail(err, "Reset failed"));
    } finally {
      setBusyId(null);
    }
  };

  return (
    <Card data-testid="members-mfa">
      <CardHeader><CardTitle>Members&apos; two-factor sign-in</CardTitle></CardHeader>
      <CardContent className="space-y-3 text-sm">
        {message && <p className="text-muted-foreground" role="status">{message}</p>}
        {!members && !message && <p className="text-muted-foreground">Loading…</p>}
        {members && (
          <table className="w-full text-left">
            <thead className="text-xs text-muted-foreground">
              <tr><th className="py-1.5">Member</th><th>Role</th><th>Two-factor</th><th /></tr>
            </thead>
            <tbody>
              {members.map((m) => (
                <tr key={m.id} className="border-t border-border">
                  <td className="py-2">{m.name || m.email}<div className="text-xs text-muted-foreground">{m.email}</div></td>
                  <td>{m.role}</td>
                  <td>{m.mfa_enabled ? "On" : "Off"}</td>
                  <td className="text-right">
                    {m.mfa_enabled && m.email !== selfEmail && (
                      <Button size="sm" variant="outline" disabled={busyId === m.id} onClick={() => resetMember(m)}>
                        {busyId === m.id ? "Resetting…" : "Reset"}
                      </Button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </CardContent>
    </Card>
  );
}

/** Authenticator-app two-factor sign-in for the signed-in user (any role). */
export default function AccountSecurity() {
  const { user } = useAuth();
  const [status, setStatus] = useState<MfaStatus | null>(null);
  const [mode, setMode] = useState<Mode>("idle");
  const [setup, setSetup] = useState<MfaSetup | null>(null);
  const [code, setCode] = useState("");
  const [recoveryCodes, setRecoveryCodes] = useState<string[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [copied, setCopied] = useState(false);

  const loadStatus = useCallback(async () => {
    try {
      const { data } = await api.get<MfaStatus>("/auth/mfa/status");
      setStatus(data);
    } catch (err) {
      setError(errorDetail(err, "Could not load your two-factor settings"));
    }
  }, []);

  useEffect(() => {
    void loadStatus();
  }, [loadStatus]);

  const reset = () => {
    setMode("idle");
    setSetup(null);
    setCode("");
    setError(null);
  };

  const startSetup = async () => {
    setError(null);
    setRecoveryCodes(null);
    setBusy(true);
    try {
      const { data } = await api.post<MfaSetup>("/auth/mfa/setup");
      setSetup(data);
      setCode("");
      setMode("setup");
    } catch (err) {
      setError(errorDetail(err, "Could not start setup"));
    } finally {
      setBusy(false);
    }
  };

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setError(null);
    setBusy(true);
    try {
      if (mode === "setup") {
        const { data } = await api.post<{ recovery_codes: string[] }>("/auth/mfa/enable", { code });
        setRecoveryCodes(data.recovery_codes);
      } else if (mode === "regenerate") {
        const { data } = await api.post<{ recovery_codes: string[] }>("/auth/mfa/recovery-codes", { code });
        setRecoveryCodes(data.recovery_codes);
      } else if (mode === "disable") {
        await api.post("/auth/mfa/disable", { code });
        setRecoveryCodes(null);
      }
      reset();
      await loadStatus();
    } catch (err) {
      setCode("");
      setError(errorDetail(err, "That code did not work"));
    } finally {
      setBusy(false);
    }
  };

  const copyCodes = async () => {
    if (!recoveryCodes) return;
    try {
      await navigator.clipboard.writeText(recoveryCodes.join("\n"));
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      setError("Copy failed; select the codes and copy them by hand");
    }
  };

  const downloadCodes = () => {
    if (!recoveryCodes) return;
    const text = `AgenticOrg recovery codes\nEach code works once. Keep them somewhere safe.\n\n${recoveryCodes.join("\n")}\n`;
    const url = URL.createObjectURL(new Blob([text], { type: "text/plain" }));
    const link = document.createElement("a");
    link.href = url;
    link.download = "agenticorg-recovery-codes.txt";
    link.click();
    URL.revokeObjectURL(url);
  };

  return (
    <div className="space-y-6 max-w-3xl">
      <div>
        <h2 className="text-2xl font-bold">Account security</h2>
        <p className="text-sm text-muted-foreground mt-1">
          Protect your sign-in with a code from an authenticator app on your phone.
        </p>
      </div>

      {error && (
        <div role="alert" className="rounded-lg bg-destructive/10 border border-destructive/30 px-4 py-3 text-sm text-destructive">
          {error}
        </div>
      )}

      {recoveryCodes && (
        <Card data-testid="recovery-codes">
          <CardHeader><CardTitle>Save your recovery codes</CardTitle></CardHeader>
          <CardContent className="space-y-4 text-sm">
            <p className="text-muted-foreground">
              If you lose your phone, each of these codes signs you in once. This is the only time they are shown.
            </p>
            <ul className="grid grid-cols-2 gap-2 font-mono text-base">
              {recoveryCodes.map((rc) => (
                <li key={rc} className="rounded border border-border bg-muted/40 px-3 py-1.5 text-center">{rc}</li>
              ))}
            </ul>
            <div className="flex flex-wrap gap-2">
              <Button variant="outline" onClick={copyCodes}>{copied ? "Copied" : "Copy codes"}</Button>
              <Button variant="outline" onClick={downloadCodes}>Download .txt</Button>
              <Button onClick={() => setRecoveryCodes(null)}>I have saved them</Button>
            </div>
          </CardContent>
        </Card>
      )}

      <Card data-testid="mfa-card">
        <CardHeader>
          <CardTitle className="flex items-center gap-3">
            Two-factor sign-in
            {status && (
              <span
                className={`rounded-full px-2.5 py-0.5 text-xs font-medium ${
                  status.enabled ? "bg-green-500/15 text-green-600" : "bg-muted text-muted-foreground"
                }`}
              >
                {status.enabled ? "On" : "Off"}
              </span>
            )}
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-5 text-sm">
          {!status && !error && <p className="text-muted-foreground">Loading…</p>}

          {status && !status.enabled && mode === "idle" && (
            <div className="space-y-3">
              <p className="text-muted-foreground">
                After you turn this on, signing in asks for your password (or Google) and then a 6-digit code
                from your authenticator app.
              </p>
              <Button onClick={startSetup} disabled={busy}>{busy ? "Starting…" : "Set up authenticator app"}</Button>
            </div>
          )}

          {mode === "setup" && setup && (
            <form onSubmit={submit} className="space-y-6" data-testid="mfa-setup">
              <ol className="space-y-6">
                <li>
                  <p className="font-medium">1. Install an authenticator app</p>
                  <p className="text-muted-foreground mt-1">
                    Google Authenticator, Microsoft Authenticator, Authy or 1Password all work.
                  </p>
                </li>
                <li>
                  <p className="font-medium">2. Scan this QR code with the app</p>
                  <div className="mt-3 flex flex-col sm:flex-row gap-5 items-start">
                    <img
                      src={setup.qr_code}
                      alt="QR code to add AgenticOrg to your authenticator app"
                      width={200}
                      height={200}
                      className="rounded-lg border border-border bg-white p-2"
                    />
                    <div className="space-y-2">
                      <p className="text-muted-foreground">Can&apos;t scan it? Add an account by hand with this key:</p>
                      <code className="block break-all rounded bg-muted px-3 py-2 font-mono text-sm" data-testid="mfa-secret">
                        {setup.secret}
                      </code>
                      <p className="text-xs text-muted-foreground">
                        Account: {setup.account} · Issuer: {setup.issuer} · Time-based, 6 digits
                      </p>
                    </div>
                  </div>
                </li>
                <li className="space-y-3">
                  <p className="font-medium">3. Enter the 6-digit code the app shows</p>
                  <CodeInput id="mfa-enable-code" value={code} onChange={setCode} label="Code from the app" />
                </li>
              </ol>
              <div className="flex gap-2">
                <Button type="submit" disabled={busy || code.trim().length < 6}>{busy ? "Verifying…" : "Verify and turn on"}</Button>
                <Button type="button" variant="outline" onClick={reset}>Cancel</Button>
              </div>
            </form>
          )}

          {status?.enabled && mode === "idle" && (
            <div className="space-y-3">
              <p className="text-muted-foreground">
                On since {status.enabled_at ? new Date(status.enabled_at).toLocaleDateString() : "setup"}.{" "}
                {status.recovery_codes_remaining} of 10 recovery codes left.
              </p>
              <div className="flex flex-wrap gap-2">
                <Button variant="outline" onClick={() => { setError(null); setCode(""); setMode("regenerate"); }}>
                  New recovery codes
                </Button>
                <Button variant="outline" onClick={() => { setError(null); setCode(""); setMode("disable"); }}>
                  Turn off
                </Button>
              </div>
            </div>
          )}

          {(mode === "disable" || mode === "regenerate") && (
            <form onSubmit={submit} className="space-y-4">
              <p className="text-muted-foreground">
                {mode === "disable"
                  ? "Enter a code from your authenticator app (or a recovery code) to turn two-factor sign-in off."
                  : "Enter a code from your authenticator app. Your old recovery codes stop working."}
              </p>
              <CodeInput id="mfa-confirm-code" value={code} onChange={setCode} label="Code" />
              <div className="flex gap-2">
                <Button type="submit" disabled={busy || code.trim().length < 6}>
                  {busy ? "Checking…" : mode === "disable" ? "Turn off" : "Show new codes"}
                </Button>
                <Button type="button" variant="outline" onClick={reset}>Cancel</Button>
              </div>
            </form>
          )}
        </CardContent>
      </Card>

      {user?.role === "admin" && <MembersMfa selfEmail={user.email} />}

      <p className="text-xs text-muted-foreground">
        Lost your phone and your recovery codes? Ask an admin of your organization to reset two-factor sign-in for you.
        Organization single sign-on (SSO) accounts use your identity provider&apos;s own two-factor settings.
      </p>
    </div>
  );
}
