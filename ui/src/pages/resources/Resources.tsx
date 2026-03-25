import { useState } from "react";
import { Link } from "react-router-dom";
import { Helmet } from "react-helmet-async";
import { CONTENT_PAGES, CLUSTERS } from "./contentData";

export default function Resources() {
  const [activeCluster, setActiveCluster] = useState("");

  const filtered = activeCluster
    ? CONTENT_PAGES.filter((p) => p.cluster === activeCluster)
    : CONTENT_PAGES;

  return (
    <div className="min-h-screen bg-white">
      <Helmet>
        <title>Resources — AgenticOrg | AI Virtual Employees for Enterprise</title>
        <meta name="description" content="Guides, tutorials, and deep dives on enterprise AI agents, finance automation, HR automation, AI governance, and India-specific compliance." />
        <link rel="canonical" href="https://agenticorg.ai/resources" />
      </Helmet>

      <header className="bg-slate-900 py-16">
        <div className="max-w-4xl mx-auto px-4 text-center">
          <Link to="/" className="inline-flex items-center gap-2 mb-6 text-slate-400 hover:text-white transition-colors text-sm">
            <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 19l-7-7 7-7" /></svg>
            AgenticOrg
          </Link>
          <h1 className="text-4xl font-extrabold text-white">Resources</h1>
          <p className="mt-4 text-lg text-slate-400">{CONTENT_PAGES.length} guides on AI agents, automation, and governance</p>
        </div>
      </header>

      <main className="max-w-4xl mx-auto px-4 py-12">
        {/* Cluster filter */}
        <div className="flex flex-wrap gap-2 mb-8">
          <button onClick={() => setActiveCluster("")} className={`px-3 py-1.5 rounded-full text-sm font-medium transition-colors ${!activeCluster ? "bg-blue-600 text-white" : "bg-slate-100 text-slate-600 hover:bg-slate-200"}`}>
            All ({CONTENT_PAGES.length})
          </button>
          {CLUSTERS.map((c) => {
            const count = CONTENT_PAGES.filter((p) => p.cluster === c.id).length;
            return (
              <button key={c.id} onClick={() => setActiveCluster(c.id)} className={`px-3 py-1.5 rounded-full text-sm font-medium transition-colors ${activeCluster === c.id ? "bg-blue-600 text-white" : "bg-slate-100 text-slate-600 hover:bg-slate-200"}`}>
                {c.label} ({count})
              </button>
            );
          })}
        </div>

        {/* Content grid */}
        <div className="space-y-4">
          {filtered.map((page) => {
            const cluster = CLUSTERS.find((c) => c.id === page.cluster);
            return (
              <Link key={page.slug} to={`/resources/${page.slug}`} className="block group">
                <article className="border rounded-xl p-5 hover:shadow-lg hover:border-slate-300 transition-all">
                  <div className="flex items-center gap-2 mb-2">
                    <span className="text-xs font-semibold px-2 py-0.5 rounded-full bg-blue-50 text-blue-700">{cluster?.label}</span>
                  </div>
                  <h2 className="text-lg font-bold text-slate-900 group-hover:text-blue-600 transition-colors leading-snug">{page.title}</h2>
                  <p className="mt-2 text-sm text-slate-600 line-clamp-2">{page.metaDescription}</p>
                  <div className="mt-3 flex flex-wrap gap-1.5">
                    {page.keywords.slice(0, 3).map((kw) => (
                      <span key={kw} className="text-xs bg-slate-50 text-slate-500 px-2 py-0.5 rounded">{kw}</span>
                    ))}
                  </div>
                </article>
              </Link>
            );
          })}
        </div>

        {/* Bottom CTA */}
        <div className="mt-12 text-center">
          <h2 className="text-xl font-bold text-slate-900 mb-4">Ready to deploy AI virtual employees?</h2>
          <div className="flex justify-center gap-4">
            <Link to="/signup" className="bg-gradient-to-r from-blue-500 to-violet-600 text-white px-6 py-2.5 rounded-lg text-sm font-semibold">Start Free</Link>
            <Link to="/playground" className="border text-slate-700 px-6 py-2.5 rounded-lg text-sm font-semibold">Try Playground</Link>
          </div>
        </div>
      </main>
    </div>
  );
}
