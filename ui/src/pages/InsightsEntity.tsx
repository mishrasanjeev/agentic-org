// SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
/**
 * Insights Level 3: one agent, workflow, connector, company, person or cost centre, from
 * GET /insights/entities/{kind}/{id}. Header facts, metric cards scoped to the entity for the period, its
 * timeline from the audit log, and links to the explorers filtered to it. A kind the role may not open is a 403;
 * an entity outside the caller's tenant or domains is a 404.
 */
import { useEffect, useState } from "react";
import { Helmet } from "react-helmet-async";
import { Link, useLocation, useParams, useSearchParams } from "react-router";
import { useTranslation } from "react-i18next";
import Breadcrumbs from "@/components/insights/Breadcrumbs";
import DecisionTile from "@/components/insights/DecisionTile";
import EmptyState from "@/components/insights/EmptyState";
import SearchPalette from "@/components/insights/SearchPalette";
import { TileSkeleton } from "@/components/insights/Skeleton";
import { errorStatus, readErrorDetail } from "@/lib/insights/api";
import { isIsoDate } from "@/lib/insights/dates";
import { entitiesApi, entityPath, kindFromSegment, type EntityKind, type EntityPage } from "@/lib/insights/entities";
import { MISSING } from "@/lib/insights/format";

type State =
  | { kind: "loading" }
  | { kind: "ready"; page: EntityPage }
  | { kind: "missing" }
  | { kind: "forbidden" }
  | { kind: "error"; message: string | null };

function readCompanyId(): string {
  try {
    return localStorage.getItem("company_id") || "";
  } catch {
    return "";
  }
}

function formatDateTime(iso: string): string {
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString();
}

function factValue(value: EntityPage["facts"][number]["value"], yes: string, no: string): string {
  if (value === null || value === undefined || value === "") return MISSING;
  if (typeof value === "boolean") return value ? yes : no;
  if (typeof value === "string" && /^\d{4}-\d{2}-\d{2}T/.test(value)) return formatDateTime(value);
  return String(value);
}

export default function InsightsEntity() {
  const { t } = useTranslation();
  const { id = "" } = useParams();
  // /dashboard/insights/<segment>/<id>: the segment names the kind.
  const segment = useLocation().pathname.split("/")[3] ?? "";
  const [searchParams] = useSearchParams();
  const kind: EntityKind | null = kindFromSegment(segment);
  const [state, setState] = useState<State>({ kind: "loading" });
  const from = searchParams.get("from");
  const to = searchParams.get("to");

  useEffect(() => {
    if (!kind) {
      setState({ kind: "missing" });
      return;
    }
    let live = true;
    setState({ kind: "loading" });
    entitiesApi
      .get(kind, id, {
        from: isIsoDate(from) ? from : undefined,
        to: isIsoDate(to) ? to : undefined,
        company_id: kind === "company" ? undefined : readCompanyId() || undefined,
      })
      .then((page) => {
        if (live) setState({ kind: "ready", page });
      })
      .catch(async (e) => {
        if (!live) return;
        const status = errorStatus(e);
        if (status === 404) setState({ kind: "missing" });
        else if (status === 403) setState({ kind: "forbidden" });
        else setState({ kind: "error", message: await readErrorDetail(e) });
      });
    return () => {
      live = false;
    };
  }, [kind, id, from, to]);

  const kindLabel = kind ? t(`insightsEntity.kinds.${kind}`) : "";
  const title = state.kind === "ready" ? state.page.title : kindLabel;

  let body: React.ReactNode;
  if (state.kind === "loading") {
    body = (
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4" aria-busy="true">
        {[0, 1, 2, 3].map((i) => (
          <TileSkeleton key={i} />
        ))}
      </div>
    );
  } else if (state.kind === "missing") {
    body = <EmptyState title={t("insightsEntity.missing")} hint={t("insightsEntity.missingHint")} />;
  } else if (state.kind === "forbidden") {
    body = <EmptyState title={t("insightsEntity.forbidden")} hint={t("insightsEntity.forbiddenHint")} />;
  } else if (state.kind === "error") {
    body = (
      <p role="alert" className="text-sm text-destructive">
        {state.message || t("insightsEntity.error")}
      </p>
    );
  } else {
    const page = state.page;
    body = (
      <>
        <section aria-labelledby="entity-facts" className="rounded-lg border p-4">
          <h2 id="entity-facts" className="sr-only">
            {t("insightsEntity.facts.title")}
          </h2>
          <dl className="grid gap-3 sm:grid-cols-3" data-testid="entity-facts">
            {page.facts.map((fact) => (
              <div key={fact.key}>
                <dt className="text-xs text-muted-foreground">
                  {t(`insightsEntity.facts.${fact.key}`, { defaultValue: fact.key })}
                </dt>
                <dd className="text-sm font-medium">{factValue(fact.value, t("insightsEntity.yes"), t("insightsEntity.no"))}</dd>
              </div>
            ))}
          </dl>
        </section>
        <section aria-labelledby="entity-cards" className="flex flex-col gap-2">
          <h2 id="entity-cards" className="text-sm font-semibold">
            {t("insightsEntity.cards", { from: page.period.from, to: page.period.to })}
          </h2>
          {page.cards.length === 0 ? (
            <p className="text-xs text-muted-foreground">{page.hint || t("insightsEntity.noCards")}</p>
          ) : (
            <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
              {page.cards.map((card) => (
                <DecisionTile key={card.key} metric={card} action={null} />
              ))}
            </div>
          )}
        </section>
        {page.agents && page.agents.length > 0 ? (
          <section aria-labelledby="entity-agents" className="flex flex-col gap-2">
            <h2 id="entity-agents" className="text-sm font-semibold">
              {t("insightsEntity.agents")}
            </h2>
            <ul className="flex flex-wrap gap-2 text-sm">
              {page.agents.map((agent) => (
                <li key={agent.id}>
                  <Link to={entityPath("agent", agent.id)} className="text-primary hover:underline">
                    {agent.name}
                  </Link>
                </li>
              ))}
            </ul>
          </section>
        ) : null}
        <section aria-labelledby="entity-links" className="flex flex-wrap gap-2">
          <h2 id="entity-links" className="sr-only">
            {t("insightsEntity.linksTitle")}
          </h2>
          {Object.entries(page.links).map(([name, to]) =>
            typeof to === "string" && to.startsWith("/") ? (
              <Link key={name} to={to} className="rounded-md border px-3 py-1.5 text-xs hover:bg-muted">
                {t(`insightsEntity.links.${name}`, { defaultValue: name })}
              </Link>
            ) : null,
          )}
        </section>
        <section aria-labelledby="entity-timeline" className="flex flex-col gap-2">
          <h2 id="entity-timeline" className="text-sm font-semibold">
            {t("insightsEntity.timeline")}{" "}
            <span className="font-normal text-muted-foreground">({page.timeline.length})</span>
          </h2>
          {page.timeline.length === 0 ? (
            <p className="text-xs text-muted-foreground">{t("insightsEntity.noTimeline")}</p>
          ) : (
            <div className="overflow-x-auto rounded-md border">
              <table className="w-full text-sm">
                <caption className="sr-only">{t("insightsEntity.timeline")}</caption>
                <thead className="bg-muted/50 text-left text-xs text-muted-foreground">
                  <tr>
                    <th scope="col" className="px-3 py-2 font-medium">{t("insightsEntity.columns.at")}</th>
                    <th scope="col" className="px-3 py-2 font-medium">{t("insightsEntity.columns.event")}</th>
                    <th scope="col" className="px-3 py-2 font-medium">{t("insightsEntity.columns.action")}</th>
                    <th scope="col" className="px-3 py-2 font-medium">{t("insightsEntity.columns.outcome")}</th>
                    <th scope="col" className="px-3 py-2 font-medium">{t("insightsEntity.columns.actor")}</th>
                  </tr>
                </thead>
                <tbody>
                  {page.timeline.map((row, i) => (
                    <tr key={i} className="border-t">
                      <td className="whitespace-nowrap px-3 py-2">{row.at ? formatDateTime(row.at) : MISSING}</td>
                      <td className="whitespace-nowrap px-3 py-2">{row.event}</td>
                      <td className="px-3 py-2">{row.action}</td>
                      <td className="whitespace-nowrap px-3 py-2">{row.outcome}</td>
                      <td className="whitespace-nowrap px-3 py-2 text-xs text-muted-foreground">
                        {row.actor_type}: {row.actor}
                      </td>
                    </tr>
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
    <div className="flex flex-col gap-4">
      <Helmet>
        <title>{`${title} · ${t("insights.title")}`}</title>
      </Helmet>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <Breadcrumbs items={[{ label: kindLabel }, { label: title }]} />
        <SearchPalette />
      </div>
      <header>
        <h1 className="text-xl font-semibold" data-testid="entity-title">
          {title}
        </h1>
        {state.kind === "ready" && state.page.subtitle ? (
          <p className="text-sm text-muted-foreground">
            {kindLabel} · {state.page.subtitle}
          </p>
        ) : null}
      </header>
      {body}
    </div>
  );
}
