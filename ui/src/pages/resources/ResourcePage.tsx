import { Link, Navigate, useParams } from "react-router-dom";
import { Helmet } from "react-helmet-async";
import { CONTENT_PAGES, CLUSTERS } from "./contentData";

const SITE_URL = "https://agenticorg.ai";
const SOCIAL_IMAGE = SITE_URL + "/og-image.png";

export default function ResourcePage() {
  const { slug } = useParams();
  const page = CONTENT_PAGES.find((p) => p.slug === slug);

  if (!page) {
    return (
      <>
        <Helmet htmlAttributes={{ lang: "en-IN" }}>
          <title>Resource Not Found | AgenticOrg</title>
          <meta name="robots" content="noindex, nofollow, noarchive" />
          <meta name="googlebot" content="noindex, nofollow, noarchive" />
          <meta name="bingbot" content="noindex, nofollow, noarchive" />
        </Helmet>
        <Navigate to="/404" replace />
      </>
    );
  }

  const cluster = CLUSTERS.find((c) => c.id === page.cluster);
  const related = CONTENT_PAGES.filter((p) => page.relatedSlugs.includes(p.slug));

  const canonical = SITE_URL + "/resources/" + page.slug;
  const breadcrumbId = canonical + "#breadcrumb";
  const faqNode = page.faqs.length > 0
    ? {
        "@type": "FAQPage",
        "@id": canonical + "#faq",
        mainEntity: page.faqs.map((faq) => ({
          "@type": "Question",
          name: faq.q,
          acceptedAnswer: { "@type": "Answer", text: faq.a },
        })),
      }
    : null;
  const structuredData = {
    "@context": "https://schema.org",
    "@graph": [
      {
        "@type": "TechArticle",
        "@id": canonical + "#article",
        headline: page.title,
        description: page.metaDescription,
        image: SOCIAL_IMAGE,
        keywords: page.keywords,
        inLanguage: "en-IN",
        author: { "@type": "Organization", name: "AgenticOrg", url: SITE_URL },
        publisher: {
          "@type": "Organization",
          name: "AgenticOrg",
          url: SITE_URL,
          logo: {
            "@type": "ImageObject",
            url: SITE_URL + "/favicon-512x512.png",
          },
        },
        mainEntityOfPage: { "@type": "WebPage", "@id": canonical },
        breadcrumb: { "@id": breadcrumbId },
      },
      {
        "@type": "BreadcrumbList",
        "@id": breadcrumbId,
        itemListElement: [
          {
            "@type": "ListItem",
            position: 1,
            name: "Home",
            item: SITE_URL + "/",
          },
          {
            "@type": "ListItem",
            position: 2,
            name: "Resources",
            item: SITE_URL + "/resources",
          },
          {
            "@type": "ListItem",
            position: 3,
            name: page.title,
            item: canonical,
          },
        ],
      },
      ...(faqNode ? [faqNode] : []),
    ],
  };

  return (
    <div className="min-h-screen bg-white">
      <Helmet htmlAttributes={{ lang: "en-IN" }}>
        <title>{page.metaTitle}</title>
        <meta name="description" content={page.metaDescription} />
        <meta name="keywords" content={page.keywords.join(", ")} />
        <meta name="robots" content="index, follow, max-image-preview:large, max-snippet:-1, max-video-preview:-1" />
        <meta name="googlebot" content="index, follow, max-image-preview:large, max-snippet:-1, max-video-preview:-1" />
        <meta name="bingbot" content="index, follow, max-image-preview:large, max-snippet:-1, max-video-preview:-1" />
        <link rel="canonical" href={canonical} />
        <meta property="og:type" content="article" />
        <meta property="og:site_name" content="AgenticOrg" />
        <meta property="og:locale" content="en_IN" />
        <meta property="og:title" content={page.metaTitle} />
        <meta property="og:description" content={page.metaDescription} />
        <meta property="og:url" content={canonical} />
        <meta property="og:image" content={SOCIAL_IMAGE} />
        <meta property="og:image:width" content="1200" />
        <meta property="og:image:height" content="630" />
        <meta property="og:image:alt" content={page.title} />
        {cluster && <meta property="article:section" content={cluster.label} />}
        {page.keywords.map((kw) => (
          <meta key={kw} property="article:tag" content={kw} />
        ))}
        <meta name="twitter:card" content="summary_large_image" />
        <meta name="twitter:title" content={page.metaTitle} />
        <meta name="twitter:description" content={page.metaDescription} />
        <meta name="twitter:url" content={canonical} />
        <meta name="twitter:image" content={SOCIAL_IMAGE} />
        <meta name="twitter:image:alt" content={page.title} />
        <script type="application/ld+json">{JSON.stringify(structuredData)}</script>
      </Helmet>

      {/* Minimal nav */}
      <nav className="bg-white border-b px-4 py-3">
        <div className="max-w-4xl mx-auto flex items-center justify-between">
          <Link to="/" className="flex items-center gap-2">
            <div className="w-7 h-7 rounded-lg bg-gradient-to-br from-blue-500 to-teal-500 flex items-center justify-center text-white font-bold text-xs">AO</div>
            <span className="font-semibold text-slate-900">AgenticOrg</span>
          </Link>
          <div className="flex gap-4 text-sm">
            <Link to="/resources" className="text-slate-600 hover:text-slate-900">All Resources</Link>
            <Link to="/blog" className="text-slate-600 hover:text-slate-900">Blog</Link>
            <Link to="/playground" className="text-blue-600 font-medium">Try Playground</Link>
          </div>
        </div>
      </nav>

      {/* Header */}
      <header className="bg-slate-50 border-b py-12">
        <div className="max-w-3xl mx-auto px-4">
          <Link to="/resources" className="inline-flex items-center gap-1 text-sm text-slate-500 hover:text-slate-700 mb-4">
            <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 19l-7-7 7-7" /></svg>
            All Resources
          </Link>
          {cluster && (
            <span className="inline-block bg-blue-50 text-blue-700 text-xs font-semibold px-2.5 py-1 rounded-full mb-3 ml-3">{cluster.label}</span>
          )}
          <h1 className="text-3xl sm:text-4xl font-extrabold text-slate-900 leading-tight">{page.title}</h1>
          <p className="mt-3 text-lg text-slate-600">{page.metaDescription}</p>

          {page.heroStat && (
            <div className="mt-6 inline-flex items-center gap-3 bg-white rounded-xl border px-5 py-3">
              <span className="text-3xl font-extrabold text-blue-600">{page.heroStat.value}</span>
              <span className="text-sm text-slate-600">{page.heroStat.label}</span>
            </div>
          )}
        </div>
      </header>

      {/* Content */}
      <article className="max-w-3xl mx-auto px-4 py-12">
        {page.sections.map((section, i) => (
          <div key={i} className="mb-10">
            <h2 className="text-2xl font-bold text-slate-900 mb-4">{section.heading}</h2>
            {section.body.split("\n\n").map((para, j) => {
              if (para.startsWith("•") || para.startsWith("1.")) {
                return (
                  <ul key={j} className="list-disc list-inside text-slate-700 leading-relaxed mb-4 space-y-1">
                    {para.split("\n").map((line, k) => (
                      <li key={k} className="text-slate-700">{line.replace(/^[•\d]+\.?\s*/, "")}</li>
                    ))}
                  </ul>
                );
              }
              return <p key={j} className="text-slate-700 leading-relaxed mb-4">{para}</p>;
            })}
          </div>
        ))}

        {/* FAQ Section */}
        {page.faqs.length > 0 && (
          <div className="mt-12 border-t pt-8">
            <h2 className="text-2xl font-bold text-slate-900 mb-6">Frequently Asked Questions</h2>
            <div className="space-y-6">
              {page.faqs.map((faq, i) => (
                <div key={i}>
                  <h3 className="text-lg font-semibold text-slate-900 mb-2">{faq.q}</h3>
                  <p className="text-slate-600 leading-relaxed">{faq.a}</p>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* Keywords */}
        <div className="mt-10 pt-6 border-t">
          <p className="text-xs text-slate-400 uppercase tracking-wide mb-3">Topics</p>
          <div className="flex flex-wrap gap-2">
            {page.keywords.map((kw) => (
              <span key={kw} className="text-xs bg-slate-100 text-slate-600 px-3 py-1.5 rounded-full">{kw}</span>
            ))}
          </div>
        </div>

        {/* CTA */}
        <div className="mt-10 bg-gradient-to-r from-blue-50 to-cyan-50 rounded-2xl p-8 text-center border border-blue-100">
          <h3 className="text-xl font-bold text-slate-900 mb-2">Ready to try it?</h3>
          <p className="text-sm text-slate-600 mb-4">Evaluate the workflow with your own approved data, integrations, review gates, and success criteria.</p>
          <Link to={page.cta.link} className="inline-block bg-gradient-to-r from-blue-500 to-cyan-500 text-white px-6 py-2.5 rounded-lg text-sm font-semibold hover:from-blue-600 hover:to-cyan-600 transition-all shadow-lg shadow-blue-500/25">
            {page.cta.text}
          </Link>
        </div>

        {/* Related */}
        {related.length > 0 && (
          <div className="mt-10">
            <h3 className="text-lg font-bold text-slate-900 mb-4">Related</h3>
            <div className="grid sm:grid-cols-2 gap-3">
              {related.slice(0, 4).map((r) => (
                <Link key={r.slug} to={`/resources/${r.slug}`} className="block border rounded-xl p-4 hover:shadow-md transition-all">
                  <span className="text-xs font-semibold text-blue-600">{CLUSTERS.find((c) => c.id === r.cluster)?.label}</span>
                  <h4 className="text-sm font-bold text-slate-900 mt-1 leading-snug">{r.title}</h4>
                </Link>
              ))}
            </div>
          </div>
        )}
      </article>
    </div>
  );
}
