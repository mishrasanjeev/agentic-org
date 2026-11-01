// SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
/**
 * Insights BI access: a tenant administrator's read-only database logins for
 * a BI tool (Metabase and the like) on the read replica.
 *
 * Lists the tenant's logins and how a BI tool connects (GET
 * /insights/bi-access), creates one (POST, the password is shown once with a
 * copy button and dropped from memory when the administrator confirms it is
 * saved) and revokes one after a confirmation (DELETE). The backend decides
 * who may do this and whether BI access is on; each login reads only this
 * tenant's rows, enforced in the database.
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { Helmet } from "react-helmet-async";
import { Link } from "react-router";
import { useTranslation } from "react-i18next";
import EmptyState from "@/components/insights/EmptyState";
import { errorStatus, readErrorDetail } from "@/lib/insights/api";
import { biAccessApi, errorCode, type BiAccessList, type BiCreatedLogin, type BiLogin } from "@/lib/insights/biAccess";

type ListState =
  | { kind: "loading" }
  | { kind: "ready"; data: BiAccessList }
  | { kind: "disabled" }
  | { kind: "forbidden" }
  | { kind: "error"; message: string | null };

const BUTTON = "h-8 rounded-md border px-3 text-sm font-medium hover:bg-muted disabled:cursor-not-allowed disabled:opacity-50";
const PRIMARY =
  "h-8 rounded-md bg-primary px-3 text-sm font-medium text-primary-foreground hover:bg-primary/90 disabled:cursor-not-allowed disabled:opacity-50";
const DANGER =
  "h-8 rounded-md bg-destructive px-3 text-sm font-medium text-destructive-foreground hover:bg-destructive/90 disabled:opacity-50";

function when(value: string | null): string {
  if (!value) return "";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString();
}

export default function InsightsBiAccess() {
  const { t } = useTranslation();
  const [state, setState] = useState<ListState>({ kind: "loading" });
  const [reloadKey, setReloadKey] = useState(0);
  const [created, setCreated] = useState<BiCreatedLogin | null>(null);
  const [creating, setCreating] = useState(false);
  const [confirming, setConfirming] = useState<string | null>(null);
  const [revoking, setRevoking] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let live = true;
    setState({ kind: "loading" });
    biAccessApi
      .list()
      .then((data) => {
        if (live) setState({ kind: "ready", data });
      })
      .catch(async (e) => {
        if (!live) return;
        const code = errorStatus(e);
        if (code === 404) return setState({ kind: "disabled" });
        if (code === 403) return setState({ kind: "forbidden" });
        const message = await readErrorDetail(e);
        if (live) setState({ kind: "error", message });
      });
    return () => {
      live = false;
    };
  }, [reloadKey]);

  const reload = () => setReloadKey((k) => k + 1);

  const create = async () => {
    setError(null);
    setNotice(null);
    setCreating(true);
    try {
      setCreated(await biAccessApi.create());
      reload();
    } catch (e) {
      if (errorStatus(e) === 409 || errorCode(e) === "insights_bi_limit_reached") setError(t("insightsBi.limitReached"));
      else setError((await readErrorDetail(e)) || t("insightsBi.createFailed"));
    } finally {
      setCreating(false);
    }
  };

  const revoke = async (roleName: string) => {
    setError(null);
    setNotice(null);
    setRevoking(roleName);
    try {
      await biAccessApi.revoke(roleName);
      setNotice(t("insightsBi.revoked", { role: roleName }));
      reload();
    } catch (e) {
      setError(errorStatus(e) === 404 ? t("insightsBi.notFound") : (await readErrorDetail(e)) || t("insightsBi.revokeFailed"));
    } finally {
      setRevoking(null);
      setConfirming(null);
    }
  };

  const data = state.kind === "ready" ? state.data : null;
  const atLimit = data ? data.max_logins > 0 && data.active_logins >= data.max_logins : true;

  let content: React.ReactNode;
  if (state.kind === "loading") {
    content = (
      <p role="status" className="text-sm text-muted-foreground" data-testid="bi-loading">
        {t("insightsBi.loading")}
      </p>
    );
  } else if (state.kind === "disabled") {
    content = <EmptyState title={t("insightsBi.disabled.title")} hint={t("insightsBi.disabled.body")} />;
  } else if (state.kind === "forbidden") {
    content = <EmptyState title={t("insightsBi.forbidden.title")} hint={t("insightsBi.forbidden.body")} />;
  } else if (state.kind === "error") {
    content = (
      <EmptyState
        title={t("insightsBi.loadFailed")}
        hint={state.message}
        action={
          <button type="button" className={BUTTON} onClick={reload}>
            {t("insights.error.retry")}
          </button>
        }
      />
    );
  } else if (data) {
    content = (
      <>
        {created ? <OneTimeSecret login={created} onDismiss={() => setCreated(null)} /> : null}

        <section aria-labelledby="bi-connection" className="rounded-md border p-4">
          <h2 id="bi-connection" className="text-base font-semibold">
            {t("insightsBi.connection.title")}
          </h2>
          <p className="mt-1 text-sm text-muted-foreground">{t("insightsBi.connection.body")}</p>
          <dl className="mt-3 grid grid-cols-1 gap-x-6 gap-y-2 text-sm sm:grid-cols-[max-content_1fr]">
            <dt className="text-muted-foreground">{t("insightsBi.connection.host")}</dt>
            <dd className="font-mono" data-testid="bi-host">
              {data.connection.host}
            </dd>
            <dt className="text-muted-foreground">{t("insightsBi.connection.port")}</dt>
            <dd className="font-mono">{data.connection.port}</dd>
            <dt className="text-muted-foreground">{t("insightsBi.connection.database")}</dt>
            <dd className="font-mono">{data.connection.database}</dd>
            <dt className="text-muted-foreground">{t("insightsBi.connection.sslmode")}</dt>
            <dd className="font-mono">{data.connection.sslmode}</dd>
            <dt className="text-muted-foreground">{t("insightsBi.connection.views")}</dt>
            <dd>
              <ul className="flex flex-wrap gap-2">
                {data.connection.views.map((view) => (
                  <li key={view} className="rounded bg-muted px-2 py-0.5 font-mono text-xs">
                    {view}
                  </li>
                ))}
              </ul>
            </dd>
          </dl>
          <p className="mt-3 text-xs text-muted-foreground">{t("insightsBi.connection.isolation")}</p>
        </section>

        <section aria-labelledby="bi-logins" className="flex flex-col gap-3">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div>
              <h2 id="bi-logins" className="text-base font-semibold">
                {t("insightsBi.logins.title")}
              </h2>
              <p className="text-sm text-muted-foreground" data-testid="bi-usage">
                {t("insightsBi.logins.usage", { active: data.active_logins, max: data.max_logins })}
              </p>
            </div>
            <button type="button" className={PRIMARY} onClick={create} disabled={creating || atLimit || created !== null}>
              {creating ? t("insightsBi.creating") : t("insightsBi.create")}
            </button>
          </div>
          {atLimit ? <p className="text-xs text-muted-foreground">{t("insightsBi.limitHint")}</p> : null}
          {data.logins.length === 0 ? (
            <EmptyState compact title={t("insightsBi.logins.emptyTitle")} hint={t("insightsBi.logins.emptyBody")} />
          ) : (
            <div className="overflow-x-auto rounded-md border">
              <table className="w-full text-sm" data-testid="bi-logins">
                <caption className="sr-only">{t("insightsBi.logins.title")}</caption>
                <thead className="bg-muted/50 text-left text-xs uppercase text-muted-foreground">
                  <tr>
                    <th scope="col" className="px-3 py-2">
                      {t("insightsBi.logins.role")}
                    </th>
                    <th scope="col" className="px-3 py-2">
                      {t("insightsBi.logins.created")}
                    </th>
                    <th scope="col" className="px-3 py-2">
                      {t("insightsBi.logins.status")}
                    </th>
                    <th scope="col" className="px-3 py-2">
                      <span className="sr-only">{t("insightsBi.logins.actions")}</span>
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {data.logins.map((login) => (
                    <LoginRow
                      key={login.role_name}
                      login={login}
                      confirming={confirming === login.role_name}
                      busy={revoking === login.role_name}
                      onAsk={() => setConfirming(login.role_name)}
                      onCancel={() => setConfirming(null)}
                      onConfirm={() => revoke(login.role_name)}
                    />
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>
      </>
    );
  }

  return (
    <div className="flex flex-col gap-4" data-testid="insights-bi-access">
      <Helmet>
        <title>{`${t("insightsBi.title")} — AgenticOrg`}</title>
      </Helmet>
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-xl font-bold md:text-2xl">{t("insightsBi.title")}</h1>
          <p className="text-sm text-muted-foreground">{t("insightsBi.subtitle")}</p>
        </div>
        <Link to="/dashboard/insights" className="text-sm font-medium text-primary hover:underline">
          {t("insightsBi.back")}
        </Link>
      </div>
      {error ? (
        <div role="alert" className="rounded-md border border-destructive/30 bg-destructive/10 px-4 py-3 text-sm text-destructive">
          {error}
        </div>
      ) : null}
      {notice ? (
        <p role="status" className="text-sm text-muted-foreground">
          {notice}
        </p>
      ) : null}
      {content}
    </div>
  );
}

function LoginRow({
  login,
  confirming,
  busy,
  onAsk,
  onCancel,
  onConfirm,
}: {
  login: BiLogin;
  confirming: boolean;
  busy: boolean;
  onAsk: () => void;
  onCancel: () => void;
  onConfirm: () => void;
}) {
  const { t } = useTranslation();
  const active = !login.revoked_at;
  return (
    <tr className="border-t align-top" data-testid={`bi-login-${login.role_name}`}>
      <td className="px-3 py-2 font-mono">{login.role_name}</td>
      <td className="px-3 py-2">{when(login.created_at)}</td>
      <td className="px-3 py-2">
        {active ? t("insightsBi.logins.active") : t("insightsBi.logins.revokedAt", { time: when(login.revoked_at) })}
      </td>
      <td className="px-3 py-2 text-right">
        {active && !confirming ? (
          <button type="button" className={BUTTON} onClick={onAsk} aria-label={t("insightsBi.revokeLabel", { role: login.role_name })}>
            {t("insightsBi.revoke")}
          </button>
        ) : null}
        {active && confirming ? (
          <div role="alertdialog" aria-label={t("insightsBi.confirmTitle")} className="flex flex-col items-end gap-2 text-left">
            <p className="max-w-xs text-xs text-muted-foreground">{t("insightsBi.confirmBody", { role: login.role_name })}</p>
            <div className="flex gap-2">
              <button type="button" className={BUTTON} onClick={onCancel} disabled={busy} autoFocus>
                {t("insightsBi.cancel")}
              </button>
              <button type="button" className={DANGER} onClick={onConfirm} disabled={busy}>
                {busy ? t("insightsBi.revoking") : t("insightsBi.confirmRevoke")}
              </button>
            </div>
          </div>
        ) : null}
      </td>
    </tr>
  );
}

function OneTimeSecret({ login, onDismiss }: { login: BiCreatedLogin; onDismiss: () => void }) {
  const { t } = useTranslation();
  const [copied, setCopied] = useState<"password" | "connection" | null>(null);
  const [copyFailed, setCopyFailed] = useState(false);
  const headingRef = useRef<HTMLHeadingElement | null>(null);

  useEffect(() => {
    headingRef.current?.focus();
  }, []);

  const copy = useCallback(async (what: "password" | "connection", value: string) => {
    setCopyFailed(false);
    try {
      await navigator.clipboard.writeText(value);
      setCopied(what);
    } catch {
      setCopyFailed(true);
    }
  }, []);

  return (
    <section
      aria-labelledby="bi-secret"
      className="rounded-md border border-amber-500/40 bg-amber-500/5 p-4"
      data-testid="bi-secret"
    >
      <h2 id="bi-secret" ref={headingRef} tabIndex={-1} className="text-base font-semibold">
        {t("insightsBi.secret.title")}
      </h2>
      <p className="mt-1 text-sm text-muted-foreground">{t("insightsBi.secret.body")}</p>
      <dl className="mt-3 flex flex-col gap-3 text-sm">
        <div>
          <dt className="text-muted-foreground">{t("insightsBi.secret.user")}</dt>
          <dd className="font-mono">{login.role_name}</dd>
        </div>
        <div>
          <dt className="text-muted-foreground">{t("insightsBi.secret.password")}</dt>
          <dd className="flex flex-wrap items-center gap-2">
            <code className="break-all rounded bg-muted px-2 py-1" data-testid="bi-password">
              {login.password}
            </code>
            <button type="button" className={BUTTON} onClick={() => copy("password", login.password)}>
              {copied === "password" ? t("insightsBi.secret.copied") : t("insightsBi.secret.copyPassword")}
            </button>
          </dd>
        </div>
        <div>
          <dt className="text-muted-foreground">{t("insightsBi.secret.connectionString")}</dt>
          <dd className="flex flex-wrap items-center gap-2">
            <code className="break-all rounded bg-muted px-2 py-1">{login.connection_string}</code>
            <button type="button" className={BUTTON} onClick={() => copy("connection", login.connection_string)}>
              {copied === "connection" ? t("insightsBi.secret.copied") : t("insightsBi.secret.copyConnection")}
            </button>
          </dd>
        </div>
      </dl>
      {copyFailed ? (
        <p role="alert" className="mt-2 text-xs text-destructive">
          {t("insightsBi.secret.copyFailed")}
        </p>
      ) : null}
      <div className="mt-4">
        <button type="button" className={PRIMARY} onClick={onDismiss}>
          {t("insightsBi.secret.saved")}
        </button>
      </div>
    </section>
  );
}
