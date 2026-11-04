// SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
/**
 * Insights budgets: the organisation's spend limits (GET/POST/PATCH/DELETE
 * /insights/budgets).
 *
 * Each budget shows this period's spend from the cost ledger against its USD
 * limit, the percent used and its state. Administrators and the CFO add,
 * change and delete budgets (delete asks first); auditors read them. Alerts
 * are emailed to the organisation's own recipient and shown in Insights; a
 * tenant-wide monthly budget is also checked against the month's forecast.
 */
import { useEffect, useMemo, useState } from "react";
import { Helmet } from "react-helmet-async";
import { Link } from "react-router";
import { useTranslation } from "react-i18next";
import EmptyState from "@/components/insights/EmptyState";
import { errorStatus, readErrorDetail } from "@/lib/insights/api";
import { errorCode } from "@/lib/insights/biAccess";
import {
  budgetsApi,
  validateBudget,
  type Budget,
  type BudgetInput,
  type BudgetList,
  type BudgetPeriod,
} from "@/lib/insights/budgets";

type ListState =
  | { kind: "loading" }
  | { kind: "ready"; data: BudgetList }
  | { kind: "forbidden" }
  | { kind: "disabled" }
  | { kind: "error"; message: string | null };

const BUTTON = "h-8 rounded-md border px-3 text-sm font-medium hover:bg-muted disabled:cursor-not-allowed disabled:opacity-50";
const PRIMARY =
  "h-8 rounded-md bg-primary px-3 text-sm font-medium text-primary-foreground hover:bg-primary/90 disabled:cursor-not-allowed disabled:opacity-50";
const DANGER =
  "h-8 rounded-md bg-destructive px-3 text-sm font-medium text-destructive-foreground hover:bg-destructive/90 disabled:opacity-50";
const INPUT = "h-9 w-full rounded-md border bg-background px-2 text-sm";

const STATE_CLASS: Record<Budget["state"], string> = {
  ok: "bg-emerald-500/10 text-emerald-700 dark:text-emerald-300",
  warn: "bg-amber-500/15 text-amber-800 dark:text-amber-300",
  breached: "bg-destructive/10 text-destructive",
};

function selectedCompany(): string {
  try {
    return localStorage.getItem("company_id") || "";
  } catch {
    return "";
  }
}

function usd(value: string): string {
  const amount = Number(value);
  return Number.isFinite(amount)
    ? amount.toLocaleString(undefined, { style: "currency", currency: "USD", minimumFractionDigits: 2 })
    : value;
}

const EMPTY_FORM: BudgetInput = { name: "", period: "monthly", threshold_usd: "", warn_at_percent: 80 };

export default function InsightsBudgets() {
  const { t } = useTranslation();
  const [state, setState] = useState<ListState>({ kind: "loading" });
  const [reloadKey, setReloadKey] = useState(0);
  const [editing, setEditing] = useState<string | "new" | null>(null);
  const [form, setForm] = useState<BudgetInput>(EMPTY_FORM);
  const [forCompany, setForCompany] = useState(false);
  const [saving, setSaving] = useState(false);
  const [confirming, setConfirming] = useState<string | null>(null);
  const [deleting, setDeleting] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const company = useMemo(selectedCompany, []);

  useEffect(() => {
    let live = true;
    setState({ kind: "loading" });
    budgetsApi
      .list()
      .then((data) => {
        if (live) setState({ kind: "ready", data });
      })
      .catch(async (e) => {
        if (!live) return;
        const code = errorStatus(e);
        if (code === 403) return setState({ kind: "forbidden" });
        if (code === 404) return setState({ kind: "disabled" });
        const message = await readErrorDetail(e);
        if (live) setState({ kind: "error", message });
      });
    return () => {
      live = false;
    };
  }, [reloadKey]);

  const reload = () => setReloadKey((k) => k + 1);
  const data = state.kind === "ready" ? state.data : null;

  const startNew = () => {
    setError(null);
    setNotice(null);
    setForm(EMPTY_FORM);
    setForCompany(false);
    setEditing("new");
  };

  const startEdit = (budget: Budget) => {
    setError(null);
    setNotice(null);
    setForm({
      name: budget.name,
      period: budget.period,
      threshold_usd: budget.threshold_usd,
      warn_at_percent: budget.warn_at_percent,
    });
    setEditing(budget.id);
  };

  const refusal = async (e: unknown): Promise<string> => {
    const code = errorCode(e);
    if (code === "duplicate_name") return t("insightsBudgets.errors.duplicate");
    if (code === "insights_budget_change_needs_a_person") return t("insightsBudgets.errors.person");
    if (errorStatus(e) === 403) return t("insightsBudgets.errors.forbidden");
    return (await readErrorDetail(e)) || t("insightsBudgets.errors.saveFailed");
  };

  const save = async () => {
    if (!data || editing === null) return;
    const problem = validateBudget(form, data.max_threshold_usd);
    if (problem) {
      setError(t(`insightsBudgets.errors.${problem}`, { max: usd(data.max_threshold_usd) }));
      return;
    }
    setError(null);
    setSaving(true);
    try {
      const body: BudgetInput = { ...form, name: form.name.trim() };
      if (editing === "new") {
        await budgetsApi.create(forCompany && company ? { ...body, company_id: company } : body);
        setNotice(t("insightsBudgets.created", { name: body.name }));
      } else {
        await budgetsApi.update(editing, body);
        setNotice(t("insightsBudgets.updated", { name: body.name }));
      }
      setEditing(null);
      reload();
    } catch (e) {
      setError(await refusal(e));
    } finally {
      setSaving(false);
    }
  };

  const remove = async (budget: Budget) => {
    setError(null);
    setNotice(null);
    setDeleting(budget.id);
    try {
      await budgetsApi.remove(budget.id);
      setNotice(t("insightsBudgets.deleted", { name: budget.name }));
      reload();
    } catch (e) {
      setError(errorStatus(e) === 404 ? t("insightsBudgets.errors.notFound") : await refusal(e));
    } finally {
      setDeleting(null);
      setConfirming(null);
    }
  };

  let content: React.ReactNode;
  if (state.kind === "loading") {
    content = (
      <p role="status" className="text-sm text-muted-foreground" data-testid="budgets-loading">
        {t("insightsBudgets.loading")}
      </p>
    );
  } else if (state.kind === "forbidden") {
    content = <EmptyState title={t("insightsBudgets.forbidden.title")} hint={t("insightsBudgets.forbidden.body")} />;
  } else if (state.kind === "disabled") {
    content = <EmptyState title={t("insightsBudgets.disabled.title")} hint={t("insightsBudgets.disabled.body")} />;
  } else if (state.kind === "error") {
    content = (
      <EmptyState
        title={t("insightsBudgets.loadFailed")}
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
        {data.can_edit && editing === null ? (
          <div>
            <button type="button" className={PRIMARY} onClick={startNew} data-testid="budget-add">
              {t("insightsBudgets.add")}
            </button>
          </div>
        ) : null}
        {editing !== null ? (
          <BudgetForm
            form={form}
            isNew={editing === "new"}
            periods={data.periods}
            saving={saving}
            company={company}
            forCompany={forCompany}
            onForCompany={setForCompany}
            onChange={setForm}
            onCancel={() => setEditing(null)}
            onSave={save}
          />
        ) : null}
        {data.budgets.length === 0 ? (
          <EmptyState
            title={t("insightsBudgets.empty.title")}
            hint={data.can_edit ? t("insightsBudgets.empty.editHint") : t("insightsBudgets.empty.readHint")}
          />
        ) : (
          <div className="overflow-x-auto rounded-md border">
            <table className="w-full text-sm" data-testid="budgets-table">
              <caption className="sr-only">{t("insightsBudgets.title")}</caption>
              <thead className="bg-muted/50 text-left text-xs uppercase text-muted-foreground">
                <tr>
                  <th scope="col" className="px-3 py-2">
                    {t("insightsBudgets.columns.name")}
                  </th>
                  <th scope="col" className="px-3 py-2">
                    {t("insightsBudgets.columns.period")}
                  </th>
                  <th scope="col" className="px-3 py-2 text-right">
                    {t("insightsBudgets.columns.spend")}
                  </th>
                  <th scope="col" className="px-3 py-2 text-right">
                    {t("insightsBudgets.columns.limit")}
                  </th>
                  <th scope="col" className="px-3 py-2 text-right">
                    {t("insightsBudgets.columns.used")}
                  </th>
                  <th scope="col" className="px-3 py-2">
                    {t("insightsBudgets.columns.state")}
                  </th>
                  {data.can_edit ? (
                    <th scope="col" className="px-3 py-2">
                      <span className="sr-only">{t("insightsBudgets.columns.actions")}</span>
                    </th>
                  ) : null}
                </tr>
              </thead>
              <tbody>
                {data.budgets.map((budget) => (
                  <tr key={budget.id} className="border-t align-top" data-testid={`budget-${budget.id}`}>
                    <td className="px-3 py-2">
                      <div className="font-medium">{budget.name}</div>
                      <div className="text-xs text-muted-foreground">
                        {budget.company_id || budget.cost_center_id
                          ? t("insightsBudgets.scope.part")
                          : t("insightsBudgets.scope.organisation")}
                        {budget.forecast_alert ? ` · ${t("insightsBudgets.scope.forecast")}` : ""}
                      </div>
                    </td>
                    <td className="px-3 py-2">{t(`insightsBudgets.periods.${budget.period}`)}</td>
                    <td className="px-3 py-2 text-right tabular-nums">{usd(budget.spend_usd)}</td>
                    <td className="px-3 py-2 text-right tabular-nums">{usd(budget.threshold_usd)}</td>
                    <td className="px-3 py-2 text-right tabular-nums">
                      {budget.used_pct === null ? "—" : `${budget.used_pct}%`}
                    </td>
                    <td className="px-3 py-2">
                      <span className={`rounded px-2 py-0.5 text-xs font-medium ${STATE_CLASS[budget.state]}`}>
                        {t(`insightsBudgets.states.${budget.state}`, { percent: budget.warn_at_percent })}
                      </span>
                    </td>
                    {data.can_edit ? (
                      <td className="px-3 py-2 text-right">
                        {confirming === budget.id ? (
                          <div
                            role="alertdialog"
                            aria-label={t("insightsBudgets.confirmTitle")}
                            className="flex flex-col items-end gap-2 text-left"
                          >
                            <p className="max-w-xs text-xs text-muted-foreground">
                              {t("insightsBudgets.confirmBody", { name: budget.name })}
                            </p>
                            <div className="flex gap-2">
                              <button
                                type="button"
                                className={BUTTON}
                                onClick={() => setConfirming(null)}
                                disabled={deleting === budget.id}
                                autoFocus
                              >
                                {t("insightsBudgets.cancel")}
                              </button>
                              <button
                                type="button"
                                className={DANGER}
                                onClick={() => remove(budget)}
                                disabled={deleting === budget.id}
                              >
                                {deleting === budget.id ? t("insightsBudgets.deleting") : t("insightsBudgets.confirmDelete")}
                              </button>
                            </div>
                          </div>
                        ) : (
                          <div className="flex justify-end gap-2">
                            <button
                              type="button"
                              className={BUTTON}
                              onClick={() => startEdit(budget)}
                              aria-label={t("insightsBudgets.editLabel", { name: budget.name })}
                            >
                              {t("insightsBudgets.edit")}
                            </button>
                            <button
                              type="button"
                              className={BUTTON}
                              onClick={() => setConfirming(budget.id)}
                              aria-label={t("insightsBudgets.deleteLabel", { name: budget.name })}
                            >
                              {t("insightsBudgets.delete")}
                            </button>
                          </div>
                        )}
                      </td>
                    ) : null}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        <p className="text-xs text-muted-foreground">{t("insightsBudgets.footnote")}</p>
      </>
    );
  }

  return (
    <div className="flex flex-col gap-4" data-testid="insights-budgets">
      <Helmet>
        <title>{`${t("insightsBudgets.title")} — AgenticOrg`}</title>
      </Helmet>
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-xl font-bold md:text-2xl">{t("insightsBudgets.title")}</h1>
          <p className="text-sm text-muted-foreground">{t("insightsBudgets.subtitle")}</p>
        </div>
        <Link to="/dashboard/insights?view=finance" className="insights-target text-sm font-medium text-primary hover:underline">
          {t("insightsBudgets.back")}
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

function BudgetForm({
  form,
  isNew,
  periods,
  saving,
  company,
  forCompany,
  onForCompany,
  onChange,
  onCancel,
  onSave,
}: {
  form: BudgetInput;
  isNew: boolean;
  periods: BudgetPeriod[];
  saving: boolean;
  company: string;
  forCompany: boolean;
  onForCompany: (value: boolean) => void;
  onChange: (form: BudgetInput) => void;
  onCancel: () => void;
  onSave: () => void;
}) {
  const { t } = useTranslation();
  return (
    <form
      className="grid grid-cols-1 gap-3 rounded-md border p-4 sm:grid-cols-2 lg:grid-cols-4"
      data-testid="budget-form"
      aria-label={isNew ? t("insightsBudgets.form.newTitle") : t("insightsBudgets.form.editTitle")}
      onSubmit={(e) => {
        e.preventDefault();
        onSave();
      }}
    >
      <label className="flex flex-col gap-1 text-sm">
        <span>{t("insightsBudgets.form.name")}</span>
        <input
          className={INPUT}
          value={form.name}
          maxLength={100}
          required
          onChange={(e) => onChange({ ...form, name: e.target.value })}
          data-testid="budget-name"
        />
      </label>
      <label className="flex flex-col gap-1 text-sm">
        <span>{t("insightsBudgets.form.period")}</span>
        <select
          className={INPUT}
          value={form.period}
          onChange={(e) => onChange({ ...form, period: e.target.value as BudgetPeriod })}
          data-testid="budget-period"
        >
          {periods.map((period) => (
            <option key={period} value={period}>
              {t(`insightsBudgets.periods.${period}`)}
            </option>
          ))}
        </select>
      </label>
      <label className="flex flex-col gap-1 text-sm">
        <span>{t("insightsBudgets.form.limit")}</span>
        <input
          className={INPUT}
          inputMode="decimal"
          value={form.threshold_usd}
          required
          onChange={(e) => onChange({ ...form, threshold_usd: e.target.value })}
          data-testid="budget-limit"
        />
      </label>
      <label className="flex flex-col gap-1 text-sm">
        <span>{t("insightsBudgets.form.warnAt")}</span>
        <input
          className={INPUT}
          type="number"
          min={1}
          max={100}
          step={1}
          value={form.warn_at_percent}
          onChange={(e) => onChange({ ...form, warn_at_percent: Number(e.target.value) })}
          data-testid="budget-warn"
        />
      </label>
      {isNew && company ? (
        <label className="flex items-center gap-2 text-sm sm:col-span-2 lg:col-span-4">
          <input type="checkbox" checked={forCompany} onChange={(e) => onForCompany(e.target.checked)} />
          <span>{t("insightsBudgets.form.forCompany")}</span>
        </label>
      ) : null}
      <p className="text-xs text-muted-foreground sm:col-span-2 lg:col-span-4">{t("insightsBudgets.form.channels")}</p>
      <div className="flex gap-2 sm:col-span-2 lg:col-span-4">
        <button type="submit" className={PRIMARY} disabled={saving} data-testid="budget-save">
          {saving ? t("insightsBudgets.saving") : t("insightsBudgets.save")}
        </button>
        <button type="button" className={BUTTON} onClick={onCancel} disabled={saving}>
          {t("insightsBudgets.cancel")}
        </button>
      </div>
    </form>
  );
}
