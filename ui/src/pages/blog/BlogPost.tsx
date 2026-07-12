import { Link, Navigate, useParams } from "react-router-dom";
import { Helmet } from "react-helmet-async";
import { BLOG_POSTS } from "./blogData";

const SITE_URL = "https://agenticorg.ai";
const SOCIAL_IMAGE = SITE_URL + "/og-image.png";

export default function BlogPost() {
  const { slug } = useParams();
  const post = BLOG_POSTS.find((p) => p.slug === slug);

  if (!post) {
    return (
      <>
        <Helmet htmlAttributes={{ lang: "en-IN" }}>
          <title>Blog Post Not Found | AgenticOrg</title>
          <meta name="robots" content="noindex, nofollow, noarchive" />
          <meta name="googlebot" content="noindex, nofollow, noarchive" />
          <meta name="bingbot" content="noindex, nofollow, noarchive" />
        </Helmet>
        <Navigate to="/404" replace />
      </>
    );
  }

  const canonical = SITE_URL + "/blog/" + post.slug;
  const pageTitle = post.title + " | AgenticOrg Blog";
  const breadcrumbId = canonical + "#breadcrumb";
  const authorNode =
    post.author === "AgenticOrg Team"
      ? {
          "@type": "Organization",
          name: post.author,
          url: SITE_URL,
        }
      : {
          "@type": "Person",
          name: post.author,
          jobTitle: post.authorRole,
        };
  const structuredData = {
    "@context": "https://schema.org",
    "@graph": [
      {
        "@type": "BlogPosting",
        "@id": canonical + "#article",
        headline: post.title,
        description: post.description,
        image: SOCIAL_IMAGE,
        datePublished: post.date,
        articleSection: post.category,
        keywords: post.keywords,
        inLanguage: "en-IN",
        author: authorNode,
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
            name: "Blog",
            item: SITE_URL + "/blog",
          },
          {
            "@type": "ListItem",
            position: 3,
            name: post.title,
            item: canonical,
          },
        ],
      },
    ],
  };

  return (
    <div className="min-h-screen bg-white">
      <Helmet htmlAttributes={{ lang: "en-IN" }}>
        <title>{pageTitle}</title>
        <meta name="description" content={post.description} />
        <meta name="keywords" content={post.keywords.join(", ")} />
        <meta name="robots" content="index, follow, max-image-preview:large, max-snippet:-1, max-video-preview:-1" />
        <meta name="googlebot" content="index, follow, max-image-preview:large, max-snippet:-1, max-video-preview:-1" />
        <meta name="bingbot" content="index, follow, max-image-preview:large, max-snippet:-1, max-video-preview:-1" />
        <link rel="canonical" href={canonical} />
        <meta property="og:type" content="article" />
        <meta property="og:site_name" content="AgenticOrg" />
        <meta property="og:locale" content="en_IN" />
        <meta property="og:title" content={pageTitle} />
        <meta property="og:description" content={post.description} />
        <meta property="og:url" content={canonical} />
        <meta property="og:image" content={SOCIAL_IMAGE} />
        <meta property="og:image:width" content="1200" />
        <meta property="og:image:height" content="630" />
        <meta property="og:image:alt" content={post.title} />
        <meta property="article:published_time" content={post.date} />
        <meta property="article:author" content="AgenticOrg" />
        <meta property="article:section" content={post.category} />
        {post.keywords.map((kw) => (
          <meta key={kw} property="article:tag" content={kw} />
        ))}
        <meta name="twitter:card" content="summary_large_image" />
        <meta name="twitter:title" content={pageTitle} />
        <meta name="twitter:description" content={post.description} />
        <meta name="twitter:url" content={canonical} />
        <meta name="twitter:image" content={SOCIAL_IMAGE} />
        <meta name="twitter:image:alt" content={post.title} />
        <script type="application/ld+json">{JSON.stringify(structuredData)}</script>
      </Helmet>

      {/* Header */}
      <header className="bg-slate-900 py-16">
        <div className="max-w-3xl mx-auto px-4 sm:px-6 lg:px-8">
          <Link to="/blog" className="inline-flex items-center gap-2 mb-6 text-slate-400 hover:text-white transition-colors text-sm">
            <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 19l-7-7 7-7" />
            </svg>
            All Posts
          </Link>

          <div className="flex items-center gap-3 mb-4">
            <span className="text-xs font-semibold px-2.5 py-1 rounded-full bg-blue-500/20 text-blue-300">
              {post.category}
            </span>
            <span className="text-xs text-slate-500">{post.date}</span>
            <span className="text-xs text-slate-500">{post.readTime}</span>
          </div>

          <h1 className="text-3xl sm:text-4xl font-extrabold text-white leading-tight">
            {post.title}
          </h1>

          <div className="mt-6 flex items-center gap-3">
            <div className="w-8 h-8 rounded-full bg-gradient-to-br from-blue-500 to-teal-500 flex items-center justify-center text-white text-sm font-bold">
              A
            </div>
            <div>
              <p className="text-sm text-white font-medium">{post.author}</p>
              <p className="text-xs text-slate-500">{post.authorRole}</p>
            </div>
          </div>
        </div>
      </header>

      {/* Article Content */}
      <article className="max-w-3xl mx-auto px-4 sm:px-6 lg:px-8 py-12">
        <div className="prose prose-slate prose-lg max-w-none">
          {post.content.map((paragraph, i) => {
            if (paragraph.startsWith("## ")) {
              return <h2 key={i} className="text-2xl font-bold text-slate-900 mt-10 mb-4">{paragraph.slice(3)}</h2>;
            }
            if (paragraph.startsWith("**") && paragraph.endsWith("**")) {
              return <h3 key={i} className="text-lg font-bold text-slate-800 mt-6 mb-2">{paragraph.slice(2, -2)}</h3>;
            }
            if (paragraph.startsWith("**")) {
              const parts = paragraph.split("**");
              return (
                <p key={i} className="text-slate-700 leading-relaxed mb-4">
                  {parts.map((part, j) =>
                    j % 2 === 1 ? <strong key={j} className="text-slate-900">{part}</strong> : part
                  )}
                </p>
              );
            }
            return <p key={i} className="text-slate-700 leading-relaxed mb-4">{paragraph}</p>;
          })}
        </div>

        {/* Tags */}
        <div className="mt-12 pt-8 border-t">
          <p className="text-xs text-slate-400 uppercase tracking-wide mb-3">Topics</p>
          <div className="flex flex-wrap gap-2">
            {post.keywords.map((kw) => (
              <span key={kw} className="text-xs bg-slate-100 text-slate-600 px-3 py-1.5 rounded-full">
                {kw}
              </span>
            ))}
          </div>
        </div>

        {/* CTA */}
        <div className="mt-12 bg-gradient-to-r from-blue-50 to-cyan-50 rounded-2xl p-8 text-center border border-blue-100">
          <h3 className="text-xl font-bold text-slate-900 mb-2">Ready to try it?</h3>
          <p className="text-sm text-slate-600 mb-6">Explore the public playground, or create an account to evaluate an agent with your own approved data and controls.</p>
          <div className="flex justify-center gap-4">
            <Link to="/signup" className="bg-gradient-to-r from-blue-500 to-cyan-500 text-white px-6 py-2.5 rounded-lg text-sm font-semibold hover:from-blue-600 hover:to-cyan-600 transition-all shadow-lg shadow-blue-500/25">
              Create Account
            </Link>
            <Link to="/playground" className="border border-slate-300 text-slate-700 px-6 py-2.5 rounded-lg text-sm font-semibold hover:bg-white transition-all">
              Try Playground
            </Link>
          </div>
        </div>

        {/* Related Posts */}
        <div className="mt-12">
          <h3 className="text-lg font-bold text-slate-900 mb-4">More from AgenticOrg</h3>
          <div className="grid sm:grid-cols-2 gap-4">
            {BLOG_POSTS.filter((p) => p.slug !== post.slug).slice(0, 2).map((related) => (
              <Link key={related.slug} to={`/blog/${related.slug}`} className="block border border-slate-200 rounded-xl p-4 hover:shadow-md transition-all">
                <span className="text-xs font-semibold text-blue-600">{related.category}</span>
                <h4 className="text-sm font-bold text-slate-900 mt-1 leading-snug">{related.title}</h4>
                <p className="text-xs text-slate-500 mt-2">{related.readTime}</p>
              </Link>
            ))}
          </div>
        </div>
      </article>
    </div>
  );
}
