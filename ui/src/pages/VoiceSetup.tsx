import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { Card, CardHeader, CardTitle, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import api from "@/lib/api";

/* ------------------------------------------------------------------ */
/*  Types                                                              */
/* ------------------------------------------------------------------ */

type SipProvider = "twilio" | "vonage" | "custom" | "";
type SttEngine = "whisper_local" | "deepgram";
type TtsEngine = "piper_local" | "google";

interface VoiceConfig {
  sip_provider: SipProvider;
  credentials: { account_sid: string; auth_token: string; custom_url: string };
  phone_number: string;
  stt_engine: SttEngine;
  tts_engine: TtsEngine;
  stt_api_key: string;
  tts_api_key: string;
}

const STEPS = [
  "Choose SIP Provider",
  "Enter Credentials",
  "Phone Number",
  "STT & TTS",
  "Review & Save",
];

const PROVIDERS = [
  { id: "twilio" as const, name: "Twilio", desc: "Enterprise-grade SIP trunking" },
  { id: "vonage" as const, name: "Vonage", desc: "Global voice API platform" },
  { id: "custom" as const, name: "Custom SIP", desc: "Bring your own SIP provider" },
];

const EMPTY_CREDS = { account_sid: "", auth_token: "", custom_url: "" };

// Session 5 TC-007 / TC-009: reject anything that isn't a plausible SIP URI.
// Matches sip: / sips: followed by optional user@, a host, and optional port
// and parameters. Rejects bare words like "invalid_sip_url" and spaces / <>.
const SIP_URI_RE = /^sips?:(?:[A-Za-z0-9._!~*'()&=+$,;?/%-]+@)?[A-Za-z0-9.-]+(?::\d+)?(?:[;?][A-Za-z0-9._!~*'()&=+$,;?/%-]*)?$/;

// Session 5 TC-012: E.164 — optional leading +, 1–15 digits, nothing else.
const PHONE_E164_RE = /^\+?\d{1,15}$/;

/* ------------------------------------------------------------------ */
/*  Component                                                          */
/* ------------------------------------------------------------------ */

export default function VoiceSetup() {
  const navigate = useNavigate();
  const [step, setStep] = useState(0);
  const [config, setConfig] = useState<VoiceConfig>({
    sip_provider: "",
    credentials: { ...EMPTY_CREDS },
    phone_number: "",
    stt_engine: "whisper_local",
    tts_engine: "piper_local",
    stt_api_key: "",
    tts_api_key: "",
  });
  const [testResult, setTestResult] = useState<{ ok: boolean; msg: string } | null>(null);
  const [testing, setTesting] = useState(false);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [fieldErrors, setFieldErrors] = useState<Record<string, string>>({});

  // TC-008: switching SIP provider must clear all credentials so the new
  // provider's inputs start empty and stale tokens cannot leak across
  // providers. Also reset the Test Connection state.
  const selectProvider = (id: SipProvider) => {
    setConfig((c) => ({ ...c, sip_provider: id, credentials: { ...EMPTY_CREDS } }));
    setTestResult(null);
    setFieldErrors({});
  };

  const trimmedPhone = config.phone_number.replace(/\s+/g, "");

  const validateStep = (s: number): Record<string, string> => {
    const errs: Record<string, string> = {};
    if (s === 0 && !config.sip_provider) {
      errs.sip_provider = "Select a SIP provider to continue";
    }
    if (s === 1) {
      if (config.sip_provider === "custom") {
        const v = config.credentials.custom_url.trim();
        if (!v) errs.custom_url = "SIP Trunk URL is required";
        else if (!SIP_URI_RE.test(v))
          errs.custom_url = "Invalid SIP endpoint format — use sip:user@host or sips:user@host";
      } else {
        if (!config.credentials.account_sid.trim()) errs.account_sid = "Account SID is required";
        if (!config.credentials.auth_token.trim()) errs.auth_token = "Auth Token is required";
      }
    }
    if (s === 2) {
      if (!trimmedPhone) errs.phone_number = "Phone number is required";
      else if (!PHONE_E164_RE.test(trimmedPhone))
        errs.phone_number = "Invalid phone number — use E.164 (digits only, optional leading +)";
    }
    if (s === 3) {
      // TC-011: Google TTS / Deepgram STT need their own API key.
      if (config.tts_engine === "google" && !config.tts_api_key.trim()) {
        errs.tts_api_key = "Google TTS requires an API key";
      }
      if (config.stt_engine === "deepgram" && !config.stt_api_key.trim()) {
        errs.stt_api_key = "Deepgram STT requires an API key";
      }
    }
    return errs;
  };

  const canNext = (): boolean => Object.keys(validateStep(step)).length === 0;

  const attemptNext = () => {
    const errs = validateStep(step);
    setFieldErrors(errs);
    if (Object.keys(errs).length === 0) {
      setStep((s) => s + 1);
    }
  };

  const handleTestConnection = async () => {
    const errs = validateStep(1);
    if (Object.keys(errs).length > 0) {
      setFieldErrors(errs);
      return;
    }
    setTesting(true);
    setTestResult(null);
    try {
      const res = await api.post("/voice/test-connection", {
        provider: config.sip_provider,
        credentials: config.credentials,
      });
      const ok = res.data?.status === "ok";
      setTestResult({ ok, msg: res.data?.message || (ok ? "Connection successful" : "Connection failed") });
    } catch (e: unknown) {
      const resp = (e as { response?: { data?: { detail?: string } } })?.response;
      setTestResult({ ok: false, msg: resp?.data?.detail || "Connection test failed — check network and credentials." });
    } finally {
      setTesting(false);
    }
  };

  const handleSave = async () => {
    // TC-010: require a successful test connection before allowing save
    // unless the user explicitly chose to skip. We default to strict.
    if (!testResult?.ok) {
      setSaveError(
        "Please run Test Connection successfully before saving, or go back to Step 2 to test.",
      );
      return;
    }
    // Re-validate everything — the wizard guarded forward navigation, but
    // a user can still hit Save after toggling TTS/STT on the review step.
    const aggregate: Record<string, string> = {
      ...validateStep(0),
      ...validateStep(1),
      ...validateStep(2),
      ...validateStep(3),
    };
    if (Object.keys(aggregate).length > 0) {
      setFieldErrors(aggregate);
      setSaveError("Fix the highlighted fields before saving.");
      return;
    }
    setSaveError(null);
    setSaving(true);
    try {
      await api.post("/voice/config", {
        sip_provider: config.sip_provider,
        credentials: config.credentials,
        phone_number: trimmedPhone,
        stt_engine: config.stt_engine,
        tts_engine: config.tts_engine,
        stt_api_key: config.stt_api_key || null,
        tts_api_key: config.tts_api_key || null,
      });
      setSaved(true);
    } catch (e: unknown) {
      const resp = (e as { response?: { data?: { detail?: string } } })?.response;
      setSaveError(resp?.data?.detail || "Failed to save voice configuration.");
    } finally {
      setSaving(false);
    }
  };

  /* ---- Progress bar ---- */

  const progressPct = ((step + 1) / STEPS.length) * 100;

  const fieldClass = (key: string) =>
    `w-full border rounded px-3 py-2 text-sm ${fieldErrors[key] ? "border-red-500 focus:ring-red-500" : ""}`;

  return (
    <div className="max-w-3xl mx-auto space-y-6">
      <h2 className="text-2xl font-bold">Voice Agent Setup</h2>

      {/* Progress */}
      <div>
        <div className="flex justify-between text-xs text-muted-foreground mb-1">
          {STEPS.map((s, i) => (
            <span key={i} className={i === step ? "text-primary font-semibold" : ""}>{s}</span>
          ))}
        </div>
        <div className="w-full h-2 bg-muted rounded-full overflow-hidden">
          <div
            className="h-full rounded-full bg-primary transition-all"
            style={{ width: `${progressPct}%` }}
          />
        </div>
      </div>

      {/* Step content */}
      <Card>
        <CardHeader>
          <CardTitle className="text-lg">Step {step + 1}: {STEPS[step]}</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">

          {/* Step 1: Provider */}
          {step === 0 && (
            <div className="grid grid-cols-3 gap-4">
              {PROVIDERS.map((p) => (
                <button
                  key={p.id}
                  onClick={() => selectProvider(p.id)}
                  className={`border rounded-lg p-4 text-left transition-colors ${
                    config.sip_provider === p.id
                      ? "border-primary ring-2 ring-primary/20 bg-primary/5"
                      : "hover:bg-muted"
                  }`}
                >
                  <div className="w-10 h-10 rounded-lg bg-gradient-to-br from-blue-500 to-purple-600 flex items-center justify-center text-white font-bold text-lg mb-2">
                    {p.name[0]}
                  </div>
                  <p className="font-medium text-sm">{p.name}</p>
                  <p className="text-xs text-muted-foreground mt-1">{p.desc}</p>
                </button>
              ))}
            </div>
          )}

          {/* Step 2: Credentials */}
          {step === 1 && (
            <div className="space-y-3">
              {config.sip_provider === "custom" ? (
                <div>
                  <label className="block text-sm font-medium mb-1">SIP Trunk URL</label>
                  <input
                    type="text"
                    placeholder="sip:user@trunk.example.com"
                    value={config.credentials.custom_url}
                    onChange={(e) =>
                      setConfig((c) => ({ ...c, credentials: { ...c.credentials, custom_url: e.target.value } }))
                    }
                    className={fieldClass("custom_url")}
                  />
                  {fieldErrors.custom_url && (
                    <p className="text-xs text-red-600 mt-1">{fieldErrors.custom_url}</p>
                  )}
                </div>
              ) : (
                <>
                  <div>
                    <label className="block text-sm font-medium mb-1">Account SID</label>
                    <input
                      type="password"
                      placeholder="Enter Account SID"
                      value={config.credentials.account_sid}
                      onChange={(e) =>
                        setConfig((c) => ({ ...c, credentials: { ...c.credentials, account_sid: e.target.value } }))
                      }
                      className={fieldClass("account_sid")}
                    />
                    {fieldErrors.account_sid && (
                      <p className="text-xs text-red-600 mt-1">{fieldErrors.account_sid}</p>
                    )}
                  </div>
                  <div>
                    <label className="block text-sm font-medium mb-1">Auth Token</label>
                    <input
                      type="password"
                      placeholder="Enter Auth Token"
                      value={config.credentials.auth_token}
                      onChange={(e) =>
                        setConfig((c) => ({ ...c, credentials: { ...c.credentials, auth_token: e.target.value } }))
                      }
                      className={fieldClass("auth_token")}
                    />
                    {fieldErrors.auth_token && (
                      <p className="text-xs text-red-600 mt-1">{fieldErrors.auth_token}</p>
                    )}
                  </div>
                </>
              )}
              <div className="flex items-center gap-3">
                <Button variant="outline" size="sm" onClick={handleTestConnection} disabled={testing}>
                  {testing ? "Testing..." : "Test Connection"}
                </Button>
                {testResult && (
                  <span className={`text-sm ${testResult.ok ? "text-green-600" : "text-red-600"}`}>
                    {testResult.msg}
                  </span>
                )}
              </div>
            </div>
          )}

          {/* Step 3: Phone number */}
          {step === 2 && (
            <div>
              <label className="block text-sm font-medium mb-1">Phone Number</label>
              <input
                type="tel"
                inputMode="tel"
                placeholder="+919876543210"
                value={config.phone_number}
                onChange={(e) => setConfig((c) => ({ ...c, phone_number: e.target.value }))}
                className={fieldClass("phone_number")}
              />
              {fieldErrors.phone_number ? (
                <p className="text-xs text-red-600 mt-1">{fieldErrors.phone_number}</p>
              ) : (
                <p className="text-xs text-muted-foreground mt-1">
                  Enter the phone number in E.164 format (digits only, optional leading +).
                </p>
              )}
            </div>
          )}

          {/* Step 4: STT & TTS */}
          {step === 3 && (
            <div className="grid grid-cols-2 gap-6">
              <div>
                <label className="block text-sm font-medium mb-2">Speech-to-Text (STT)</label>
                {(["whisper_local", "deepgram"] as const).map((engine) => (
                  <label key={engine} className="flex items-center gap-2 mb-2 cursor-pointer">
                    <input
                      type="radio"
                      name="stt"
                      checked={config.stt_engine === engine}
                      onChange={() => setConfig((c) => ({ ...c, stt_engine: engine }))}
                    />
                    <span className="text-sm">
                      {engine === "whisper_local" ? "Whisper Local (default)" : "Deepgram (cloud)"}
                    </span>
                    {engine === "whisper_local" && <Badge variant="success">Open Source</Badge>}
                  </label>
                ))}
                {config.stt_engine === "deepgram" && (
                  <div className="mt-3">
                    <label className="block text-sm font-medium mb-1">Deepgram API Key</label>
                    <input
                      type="password"
                      placeholder="Enter Deepgram API key"
                      value={config.stt_api_key}
                      onChange={(e) => setConfig((c) => ({ ...c, stt_api_key: e.target.value }))}
                      className={fieldClass("stt_api_key")}
                    />
                    {fieldErrors.stt_api_key && (
                      <p className="text-xs text-red-600 mt-1">{fieldErrors.stt_api_key}</p>
                    )}
                  </div>
                )}
              </div>
              <div>
                <label className="block text-sm font-medium mb-2">Text-to-Speech (TTS)</label>
                {(["piper_local", "google"] as const).map((engine) => (
                  <label key={engine} className="flex items-center gap-2 mb-2 cursor-pointer">
                    <input
                      type="radio"
                      name="tts"
                      checked={config.tts_engine === engine}
                      onChange={() => setConfig((c) => ({ ...c, tts_engine: engine }))}
                    />
                    <span className="text-sm">
                      {engine === "piper_local" ? "Piper Local (default)" : "Google TTS (cloud)"}
                    </span>
                    {engine === "piper_local" && <Badge variant="success">Open Source</Badge>}
                  </label>
                ))}
                {/* TC-011: Google TTS requires an API key field. */}
                {config.tts_engine === "google" && (
                  <div className="mt-3">
                    <label className="block text-sm font-medium mb-1">Google TTS API Key</label>
                    <input
                      type="password"
                      placeholder="Enter Google Cloud API key"
                      value={config.tts_api_key}
                      onChange={(e) => setConfig((c) => ({ ...c, tts_api_key: e.target.value }))}
                      className={fieldClass("tts_api_key")}
                    />
                    {fieldErrors.tts_api_key && (
                      <p className="text-xs text-red-600 mt-1">{fieldErrors.tts_api_key}</p>
                    )}
                  </div>
                )}
              </div>
            </div>
          )}

          {/* Step 5: Review & Save */}
          {step === 4 && (
            <div className="space-y-3">
              <div className="border rounded-lg p-4 space-y-2 text-sm">
                <div className="flex justify-between">
                  <span className="text-muted-foreground">SIP Provider</span>
                  <span className="font-medium capitalize">{config.sip_provider || "—"}</span>
                </div>
                <div className="flex justify-between">
                  <span className="text-muted-foreground">Phone Number</span>
                  <span className="font-medium">{trimmedPhone || "—"}</span>
                </div>
                <div className="flex justify-between">
                  <span className="text-muted-foreground">STT Engine</span>
                  <span className="font-medium">{config.stt_engine === "whisper_local" ? "Whisper Local" : "Deepgram"}</span>
                </div>
                <div className="flex justify-between">
                  <span className="text-muted-foreground">TTS Engine</span>
                  <span className="font-medium">{config.tts_engine === "piper_local" ? "Piper Local" : "Google TTS"}</span>
                </div>
                <div className="flex justify-between">
                  <span className="text-muted-foreground">Test Connection</span>
                  <span className={testResult?.ok ? "text-green-600 font-medium" : "text-red-600 font-medium"}>
                    {testResult?.ok ? "Verified" : "Not verified"}
                  </span>
                </div>
              </div>
              {saveError && (
                <div className="rounded border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">
                  {saveError}
                </div>
              )}
              {saved ? (
                <Badge variant="success">Configuration saved</Badge>
              ) : (
                <Button onClick={handleSave} disabled={saving || !testResult?.ok}>
                  {saving ? "Saving..." : "Save Configuration"}
                </Button>
              )}
            </div>
          )}
        </CardContent>
      </Card>

      {/* Navigation */}
      <div className="flex justify-between">
        <Button variant="outline" onClick={() => step === 0 ? navigate(-1) : setStep((s) => s - 1)}>
          Back
        </Button>
        {step < STEPS.length - 1 && (
          <Button onClick={attemptNext} disabled={!canNext()}>
            Next
          </Button>
        )}
      </div>
    </div>
  );
}
