// SPDX-License-Identifier: Apache-2.0
import { useEffect, useRef, useState } from "react";
import { Helmet } from "react-helmet-async";
import { Link, useLocation, useParams } from "react-router";
import {
  ArrowLeft,
  ArrowRight,
  BookOpen,
  Check,
  Copy,
  ExternalLink,
  Menu,
  Printer,
  Search,
  X,
} from "lucide-react";
import manual from "../content/userDocs.generated.json";
import publicSite from "../content/publicSite.json";
import ProductOwnership from "../components/ProductOwnership";
import CommerceA2AJourney from "../components/docs/CommerceA2AJourney";
import CommerceA2ATransaction from "../components/docs/CommerceA2ATransaction";
import { buildDocumentationSchema } from "../lib/documentation-schema.mjs";

type Guide = (typeof manual.articles)[number];

export function searchGuides(
  query: string,
  guides: Guide[] = manual.articles,
): Guide[] {
  const terms = query.trim().toLowerCase().split(/\s+/).filter(Boolean);
  if (!terms.length) return [];
  return guides
    .filter((guide) =>
      terms.every((term) =>
        `${guide.title} ${guide.description} ${guide.audience} ${guide.searchText}`
          .toLowerCase()
          .includes(term),
      ),
    )
    .sort(
      (a, b) =>
        Number(b.title.toLowerCase().includes(query.toLowerCase().trim())) -
        Number(a.title.toLowerCase().includes(query.toLowerCase().trim())),
    );
}

function DocumentationSeo({
  guide,
  missing,
}: {
  guide?: Guide;
  missing: boolean;
}) {
  const path = guide ? `/docs/${guide.slug}` : "/docs";
  const url = `https://agenticorg.ai${path}`;
  const title = missing
    ? "Guide Not Found | AgenticOrg"
    : guide
      ? `${guide.title} | AgenticOrg Docs`
      : "AgenticOrg Documentation | User Guides and BFSI Playbooks";
  const description =
    guide?.description ??
    "Learn AgenticOrg from first agent to team rollout: setup, knowledge and OCR, connectors, workflows, human review, voice, RPA, integrations and BFSI examples.";
  return (
    <Helmet htmlAttributes={{ lang: "en-IN" }}>
      <title>{title}</title>
      <meta name="description" content={description} />
      <meta
        name="robots"
        content={
          missing
            ? "noindex, nofollow"
            : "index, follow, max-image-preview:large"
        }
      />
      <meta
        name="googlebot"
        content={missing ? "noindex, nofollow" : "index, follow"}
      />
      <meta
        name="bingbot"
        content={missing ? "noindex, nofollow" : "index, follow"}
      />
      <link rel="canonical" href={url} />
      <meta property="og:type" content={guide ? "article" : "website"} />
      <meta property="og:title" content={title} />
      <meta property="og:description" content={description} />
      <meta property="og:url" content={url} />
      <meta property="og:image" content="https://agenticorg.ai/og-image.png" />
      <meta property="og:site_name" content="AgenticOrg" />
      <meta name="twitter:title" content={title} />
      <meta name="twitter:description" content={description} />
      <meta name="twitter:url" content={url} />
      <meta name="twitter:card" content="summary_large_image" />
      <meta name="twitter:image" content="https://agenticorg.ai/og-image.png" />
      {!missing && (
        <script type="application/ld+json">
          {JSON.stringify(
            buildDocumentationSchema(publicSite.site, guide, manual.reviewed),
          ).replace(/</g, "\\u003c")}
        </script>
      )}
    </Helmet>
  );
}

export default function Documentation() {
  const { slug } = useParams();
  const location = useLocation();
  const guide = manual.articles.find((item) => item.slug === slug);
  const index = manual.articles.findIndex((item) => item.slug === slug);
  const [query, setQuery] = useState("");
  const [menuOpen, setMenuOpen] = useState(false);
  const [copied, setCopied] = useState(false);
  const [copyError, setCopyError] = useState(false);
  const searchRef = useRef<HTMLInputElement>(null);
  const results = searchGuides(query);
  const searching = query.trim().length > 0;

  useEffect(() => {
    setMenuOpen(false);
    setQuery("");
    setCopied(false);
    setCopyError(false);
    const frame = requestAnimationFrame(() => {
      let id = location.hash.slice(1);
      try {
        id = decodeURIComponent(id);
      } catch {
        /* Ignore malformed URL escapes. */
      }
      if (id) document.getElementById(id)?.scrollIntoView();
      else window.scrollTo({ top: 0 });
    });
    return () => cancelAnimationFrame(frame);
  }, [slug, location.hash]);

  async function copyLink() {
    try {
      await navigator.clipboard.writeText(
        `https://agenticorg.ai/docs${guide ? "/" + guide.slug : ""}${location.hash}`,
      );
      setCopied(true);
      setCopyError(false);
    } catch {
      setCopyError(true);
    }
  }

  const toc = guide?.headings.filter((heading) => heading.level === 2) ?? [];
  const navigation = (
    <nav aria-label="Documentation guides">
      <Link className={!slug ? "docs-nav-active" : ""} to="/docs">
        Documentation overview
      </Link>
      {manual.groups.map((group) => (
        <div key={group} className="docs-nav-group">
          <h2>{group}</h2>
          {manual.articles
            .filter((item) => item.group === group)
            .map((item) => (
              <Link
                key={item.slug}
                to={`/docs/${item.slug}`}
                aria-current={slug === item.slug ? "page" : undefined}
              >
                {item.title}
              </Link>
            ))}
        </div>
      ))}
    </nav>
  );

  return (
    <div className="docs-site">
      <DocumentationSeo guide={guide} missing={Boolean(slug && !guide)} />
      <a className="docs-skip" href="#docs-main">
        Skip to guide
      </a>
      <header className="docs-header">
        <Link to="/docs" className="docs-brand">
          <BookOpen size={22} aria-hidden="true" />
          <strong>AgenticOrg</strong>
          <span>Docs</span>
        </Link>
        <div className="docs-search">
          <Search size={18} aria-hidden="true" />
          <input
            ref={searchRef}
            aria-label="Search documentation"
            type="search"
            value={query}
            placeholder="Search guides, tasks, errors..."
            onChange={(event) => setQuery(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Escape") setQuery("");
            }}
          />
          {searching && (
            <button
              type="button"
              title="Clear search"
              aria-label="Clear search"
              onClick={() => {
                setQuery("");
                searchRef.current?.focus();
              }}
            >
              <X size={17} />
            </button>
          )}
        </div>
        <a className="docs-open-app" href="https://app.agenticorg.ai">
          Open app <ExternalLink size={14} aria-hidden="true" />
        </a>
        <button
          type="button"
          className="docs-menu-toggle"
          aria-label={
            menuOpen ? "Close guide navigation" : "Open guide navigation"
          }
          aria-expanded={menuOpen}
          aria-controls="docs-sidebar"
          title="Guide navigation"
          onClick={() => setMenuOpen(!menuOpen)}
        >
          {menuOpen ? <X size={22} /> : <Menu size={22} />}
        </button>
      </header>
      <div className="docs-layout">
        <aside
          id="docs-sidebar"
          className={`docs-sidebar ${menuOpen ? "docs-sidebar-open" : ""}`}
        >
          {navigation}
        </aside>
        <main id="docs-main" className="docs-main">
          {searching ? (
            <section aria-label="Search results">
              <p className="docs-eyebrow">Documentation search</p>
              <h1>Results for &ldquo;{query}&rdquo;</h1>
              <p role="status">
                {results.length} {results.length === 1 ? "guide" : "guides"}{" "}
                found
              </p>
              <div className="docs-results">
                {results.map((item) => (
                  <Link key={item.slug} to={`/docs/${item.slug}`}>
                    <span>{item.group}</span>
                    <h2>{item.title}</h2>
                    <p>{item.description}</p>
                    <ArrowRight size={18} aria-hidden="true" />
                  </Link>
                ))}
              </div>
              {!results.length && (
                <p>
                  No matching guide. Try a task such as OCR, model, company,
                  approval or Shopify.{" "}
                  <a href="https://agenticorg.ai/support">Contact support</a>{" "}
                  for an unresolved issue.
                </p>
              )}
            </section>
          ) : guide ? (
            <>
              <div className="docs-breadcrumb">
                <Link to="/docs">Documentation</Link>
                <span>/</span>
                <span>{guide.group}</span>
              </div>
              <div className="docs-article-heading">
                <p className="docs-eyebrow">{guide.group}</p>
                <h1>{guide.title}</h1>
                <p className="docs-description">{guide.description}</p>
                <div className="docs-meta">
                  <span>{guide.audience}</span>
                  <span>{guide.minutes} min read</span>
                  <span>Reviewed {guide.reviewed}</span>
                </div>
                <div className="docs-actions">
                  <button
                    type="button"
                    title={copied ? "Link copied" : "Copy guide link"}
                    aria-label={copied ? "Link copied" : "Copy guide link"}
                    onClick={copyLink}
                  >
                    {copied ? <Check size={18} /> : <Copy size={18} />}
                  </button>
                  <button
                    type="button"
                    title="Print guide"
                    aria-label="Print guide"
                    onClick={() => window.print()}
                  >
                    <Printer size={18} />
                  </button>
                  <a
                    href={`https://github.com/mishrasanjeev/agentic-org/blob/main/docs/user-guide/${guide.slug}.md`}
                  >
                    Markdown source <ExternalLink size={14} />
                  </a>
                  <span role="status">
                    {copyError
                      ? "Could not copy. Use the browser address."
                      : copied
                        ? "Link copied"
                        : ""}
                  </span>
                </div>
              </div>
              <details className="docs-mobile-toc">
                <summary>On this page</summary>
                <nav aria-label="On this page">
                  {toc.map((heading) => (
                    <a key={heading.id} href={`#${heading.id}`}>
                      {heading.title}
                    </a>
                  ))}
                </nav>
              </details>
              {guide.slug === "seller-a2a-commerce-journey" && (
                <>
                  <CommerceA2ATransaction />
                  <CommerceA2AJourney />
                </>
              )}
              <article
                className="docs-prose"
                dangerouslySetInnerHTML={{ __html: guide.html }}
              />
              <section
                className="docs-source-notes"
                aria-label="Implementation references"
              >
                <h2>Implementation references</h2>
                <p>
                  This guide is checked against repository sources. Enabled
                  features and deployed versions may differ by environment.
                </p>
                <ul>
                  {guide.sources.map((source) => (
                    <li key={source}>
                      <a
                        href={`https://github.com/mishrasanjeev/agentic-org/blob/main/${source}`}
                      >
                        {source}
                      </a>
                    </li>
                  ))}
                </ul>
              </section>
              <nav
                className="docs-pagination"
                aria-label="Previous and next guide"
              >
                {index > 0 ? (
                  <Link to={`/docs/${manual.articles[index - 1].slug}`}>
                    <ArrowLeft size={18} />
                    <span>
                      Previous
                      <strong>{manual.articles[index - 1].title}</strong>
                    </span>
                  </Link>
                ) : (
                  <span />
                )}
                {index < manual.articles.length - 1 && (
                  <Link to={`/docs/${manual.articles[index + 1].slug}`}>
                    <span>
                      Next<strong>{manual.articles[index + 1].title}</strong>
                    </span>
                    <ArrowRight size={18} />
                  </Link>
                )}
              </nav>
            </>
          ) : slug ? (
            <section>
              <p className="docs-eyebrow">Documentation</p>
              <h1>Guide not found</h1>
              <p>
                This guide address does not exist.{" "}
                <Link to="/docs">Browse the manual</Link> or search for the
                task.
              </p>
            </section>
          ) : (
            <>
              <p className="docs-eyebrow">User manual</p>
              <h1>AgenticOrg documentation</h1>
              <p className="docs-description">
                From your first agent to a working team. Practical setup,
                everyday tasks, operating guidance and end-to-end BFSI examples.
              </p>
              <div className="docs-start">
                <Link to="/docs/first-agent">
                  <BookOpen size={18} /> Your first useful agent{" "}
                  <ArrowRight size={18} />
                </Link>
                <Link to="/docs/start-here">Choose your learning path</Link>
              </div>
              <div className="docs-home-note">
                <strong>New to agents?</strong> Begin with a read-only task and
                a small approved knowledge set. Model access, business
                permissions and provider integrations are separate setup steps.
              </div>
              {manual.groups.map((group) => (
                <section key={group} className="docs-directory">
                  <h2>{group}</h2>
                  <div className="docs-guide-grid">
                    {manual.articles
                      .filter((item) => item.group === group)
                      .map((item) => (
                        <Link key={item.slug} to={`/docs/${item.slug}`}>
                          <h3>
                            {item.title}
                            <ArrowRight size={16} aria-hidden="true" />
                          </h3>
                          <p>{item.description}</p>
                          <span>
                            {item.audience} / {item.minutes} min
                          </span>
                        </Link>
                      ))}
                  </div>
                </section>
              ))}
            </>
          )}
          <footer className="docs-footer">
            <Link to="/">AgenticOrg home</Link>
            <a href="https://agenticorg.ai/support">Support</a>
            <ProductOwnership tone="light" compact className="docs-ownership" />
          </footer>
        </main>
        {guide && !searching && (
          <aside className="docs-toc">
            <nav aria-label="On this page">
              <h2>On this page</h2>
              {toc.map((heading) => (
                <a key={heading.id} href={`#${heading.id}`}>
                  {heading.title}
                </a>
              ))}
            </nav>
            <Link to="/docs/troubleshooting">
              Need help? Troubleshooting <ArrowRight size={14} />
            </Link>
          </aside>
        )}
      </div>
    </div>
  );
}
