import { Link } from "react-router-dom";
import { Helmet } from "react-helmet-async";
import { BLOG_POSTS } from "./blogData";

const CATEGORY_COLORS: Record<string, string> = {
  Finance: "bg-emerald-100 text-emerald-800",
  Strategy: "bg-blue-100 text-blue-800",
  Governance: "bg-amber-100 text-amber-800",
  Product: "bg-purple-100 text-purple-800",
};

export default function Blog() {
  return (
    <div className="min-h-screen bg-white">
      <Helmet>
        <title>Blog — AgenticOrg | AI Virtual Employees for Enterprise</title>
        <meta name="description" content="Insights on AI agents, enterprise automation, virtual employees, bank reconciliation, invoice processing, HITL governance, and no-code agent building." />
        <link rel="canonical" href="https://agenticorg.ai/blog" />
      </Helmet>

      {/* Header */}
      <header className="bg-slate-900 py-20">
        <div className="max-w-4xl mx-auto px-4 sm:px-6 lg:px-8 text-center">
          <Link to="/" className="inline-flex items-center gap-2 mb-8 text-slate-400 hover:text-white transition-colors text-sm">
            <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 19l-7-7 7-7" />
            </svg>
            Back to AgenticOrg
          </Link>
          <h1 className="text-4xl sm:text-5xl font-extrabold text-white">AgenticOrg Blog</h1>
          <p className="mt-4 text-lg text-slate-400">
            Insights on AI virtual employees, enterprise automation, and the future of work.
          </p>
        </div>
      </header>

      {/* Post Grid */}
      <main className="max-w-4xl mx-auto px-4 sm:px-6 lg:px-8 py-16">
        <div className="space-y-8">
          {BLOG_POSTS.map((post) => (
            <Link
              key={post.slug}
              to={`/blog/${post.slug}`}
              className="block group"
            >
              <article className="border border-slate-200 rounded-2xl p-6 sm:p-8 hover:shadow-lg hover:border-slate-300 transition-all duration-300">
                <div className="flex items-center gap-3 mb-3">
                  <span className={`text-xs font-semibold px-2.5 py-1 rounded-full ${CATEGORY_COLORS[post.category] || "bg-slate-100 text-slate-800"}`}>
                    {post.category}
                  </span>
                  <span className="text-xs text-slate-400">{post.date}</span>
                  <span className="text-xs text-slate-400">{post.readTime}</span>
                </div>

                <h2 className="text-xl sm:text-2xl font-bold text-slate-900 group-hover:text-blue-600 transition-colors leading-tight">
                  {post.title}
                </h2>

                <p className="mt-3 text-slate-600 text-sm leading-relaxed line-clamp-2">
                  {post.description}
                </p>

                <div className="mt-4 flex items-center gap-2">
                  <div className="w-6 h-6 rounded-full bg-gradient-to-br from-blue-500 to-teal-500 flex items-center justify-center text-white text-xs font-bold">
                    A
                  </div>
                  <span className="text-xs text-slate-500">{post.author}</span>
                </div>
              </article>
            </Link>
          ))}
        </div>
      </main>

      {/* Footer CTA */}
      <section className="bg-slate-50 py-16 border-t">
        <div className="max-w-4xl mx-auto px-4 text-center">
          <h2 className="text-2xl font-bold text-slate-900 mb-4">Ready to deploy AI virtual employees?</h2>
          <div className="flex justify-center gap-4">
            <Link to="/signup" className="bg-gradient-to-r from-blue-500 to-cyan-500 text-white px-6 py-3 rounded-xl text-sm font-semibold hover:from-blue-600 hover:to-cyan-600 transition-all shadow-lg shadow-blue-500/25">
              Start Free
            </Link>
            <Link to="/playground" className="border border-slate-300 text-slate-700 px-6 py-3 rounded-xl text-sm font-semibold hover:bg-slate-100 transition-all">
              Try Playground
            </Link>
          </div>
        </div>
      </section>
    </div>
  );
}
