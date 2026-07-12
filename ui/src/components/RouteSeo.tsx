import { useLayoutEffect } from "react";
import { Helmet } from "react-helmet-async";
import { useLocation } from "react-router-dom";
import publicSite from "@/content/publicSite.json";

type PublicPage = {
  path: string;
  name: string;
  title: string;
  description: string;
  summary?: string;
  section?: string;
  schemaType?: string;
  index?: boolean;
  keywords?: string[];
};

type LandingFaq = {
  question: string;
  answer: string;
};

type SiteConfig = {
  name: string;
  url: string;
  language: string;
  locale: string;
  defaultImage: string;
};

export type RouteSeoData = {
  path: string;
  name: string;
  title: string;
  description: string;
  canonical: string;
  image: string;
  index: boolean;
  schemaType: string;
  keywords: string[];
  kind: "public" | "private" | "not-found";
};

const content = publicSite as unknown as {
  site: SiteConfig;
  pages: PublicPage[];
  landingFaqs: LandingFaq[];
};

const SITE = content.site;
const PUBLIC_PAGES = content.pages;
const LANDING_FAQS = content.landingFaqs;
const INDEX_ROBOTS =
  "index, follow, max-image-preview:large, max-snippet:-1, max-video-preview:-1";
const NOINDEX_ROBOTS = "noindex, nofollow, noarchive";
const OWN_METADATA_PATTERN = /^\/(?:blog|resources)\/[^/]+$/;
const PRIVATE_PATHS = new Set([
  "/login",
  "/signup",
  "/forgot-password",
  "/reset-password",
  "/invite",
  "/accept-invite",
  "/sso/callback",
  "/onboarding",
]);
const PAGE_SCHEMA_TYPES = new Set([
  "WebPage",
  "AboutPage",
  "CollectionPage",
  "ContactPage",
  "ItemPage",
  "ProfilePage",
]);

function normalizePath(pathname: string): string {
  const path = pathname.split(/[?#]/, 1)[0] || "/";
  if (path === "/") return path;
  return path.replace(/\/+$/, "") || "/";
}

function canonicalFor(path: string): string {
  return path === "/" ? SITE.url + "/" : SITE.url + path;
}

function isPrivatePath(path: string): boolean {
  return PRIVATE_PATHS.has(path) || /^\/dashboard(?:\/|$)/.test(path);
}

function safeSchemaType(schemaType?: string): string {
  return schemaType && PAGE_SCHEMA_TYPES.has(schemaType) ? schemaType : "WebPage";
}

export function resolveRouteSeo(
  pathname: string,
  pages: PublicPage[] = PUBLIC_PAGES,
): RouteSeoData | null {
  const path = normalizePath(pathname);

  // Detail pages have richer metadata from BlogPost and ResourcePage.
  if (OWN_METADATA_PATTERN.test(path)) return null;

  const page = pages.find((candidate) => normalizePath(candidate.path) === path);
  if (page) {
    return {
      path,
      name: page.name,
      title: page.title,
      description: page.description,
      canonical: canonicalFor(path),
      image: SITE.defaultImage,
      index: page.index !== false,
      schemaType: safeSchemaType(page.schemaType),
      keywords: page.keywords ?? [],
      kind: isPrivatePath(path) ? "private" : "public",
    };
  }

  if (isPrivatePath(path)) {
    const workspace = path.startsWith("/dashboard") || path === "/onboarding";
    return {
      path,
      name: workspace ? "Secure workspace" : "Account access",
      title: workspace
        ? "Secure Workspace | " + SITE.name
        : "Account Access | " + SITE.name,
      description: workspace
        ? "A private AgenticOrg workspace for authenticated organization members."
        : "Sign in to or manage your AgenticOrg account.",
      canonical: canonicalFor(path),
      image: SITE.defaultImage,
      index: false,
      schemaType: "WebPage",
      keywords: [],
      kind: "private",
    };
  }

  return {
    path,
    name: "Page not found",
    title: "Page Not Found | " + SITE.name,
    description: "The requested AgenticOrg page could not be found.",
    canonical: SITE.url + "/404",
    image: SITE.defaultImage,
    index: false,
    schemaType: "WebPage",
    keywords: [],
    kind: "not-found",
  };
}

export function buildRouteSchema(data: RouteSeoData): Record<string, unknown> | null {
  if (!data.index || data.kind !== "public") return null;

  const pageId = data.canonical + "#webpage";
  const pageNode: Record<string, unknown> = {
    "@type": data.schemaType,
    "@id": pageId,
    url: data.canonical,
    name: data.name,
    headline: data.title,
    description: data.description,
    inLanguage: SITE.language,
    isPartOf: { "@id": SITE.url + "/#website" },
    primaryImageOfPage: {
      "@type": "ImageObject",
      url: data.image,
    },
  };

  const graph: Record<string, unknown>[] = [pageNode];
  if (data.path !== "/") {
    const breadcrumbId = data.canonical + "#breadcrumb";
    pageNode.breadcrumb = { "@id": breadcrumbId };
    graph.push({
      "@type": "BreadcrumbList",
      "@id": breadcrumbId,
      itemListElement: [
        {
          "@type": "ListItem",
          position: 1,
          name: "Home",
          item: SITE.url + "/",
        },
        {
          "@type": "ListItem",
          position: 2,
          name: data.name,
          item: data.canonical,
        },
      ],
    });
  } else if (LANDING_FAQS.length > 0) {
    const faqId = data.canonical + "#faq";
    pageNode.mainEntity = { "@id": faqId };
    graph.push({
      "@type": "FAQPage",
      "@id": faqId,
      mainEntity: LANDING_FAQS.map((faq) => ({
        "@type": "Question",
        name: faq.question,
        acceptedAnswer: {
          "@type": "Answer",
          text: faq.answer,
        },
      })),
    });
  }

  return {
    "@context": "https://schema.org",
    "@graph": graph,
  };
}

export default function RouteSeo() {
  // React 19 renders Helmet metadata through native document metadata tags,
  // which do not reconcile with the route shell that existed before hydration.
  // Remove only the generator-marked shell tags after React has committed its
  // live route metadata; the source HTML remains complete for no-JS crawlers.
  useLayoutEffect(() => {
    document.head
      .querySelectorAll('[data-rh="true"]')
      .forEach((node) => node.remove());
  }, []);

  const { pathname } = useLocation();
  const seo = resolveRouteSeo(pathname);
  if (!seo) return null;

  const robots = seo.index ? INDEX_ROBOTS : NOINDEX_ROBOTS;
  const schema = buildRouteSchema(seo);
  const imageAlt = seo.path === "/"
    ? "AgenticOrg governed enterprise AI agent platform"
    : seo.name + " - " + SITE.name;

  return (
    <Helmet htmlAttributes={{ lang: SITE.language }}>
      <title>{seo.title}</title>
      <meta name="description" content={seo.description} />
      <meta name="robots" content={robots} />
      <meta name="googlebot" content={robots} />
      <meta name="bingbot" content={robots} />
      {seo.keywords.length > 0 && (
        <meta name="keywords" content={seo.keywords.join(", ")} />
      )}
      <link rel="canonical" href={seo.canonical} />

      <meta property="og:type" content="website" />
      <meta property="og:site_name" content={SITE.name} />
      <meta property="og:locale" content={SITE.locale} />
      <meta property="og:title" content={seo.title} />
      <meta property="og:description" content={seo.description} />
      <meta property="og:url" content={seo.canonical} />
      <meta property="og:image" content={seo.image} />
      <meta property="og:image:width" content="1200" />
      <meta property="og:image:height" content="630" />
      <meta property="og:image:alt" content={imageAlt} />

      <meta name="twitter:card" content="summary_large_image" />
      <meta name="twitter:title" content={seo.title} />
      <meta name="twitter:description" content={seo.description} />
      <meta name="twitter:url" content={seo.canonical} />
      <meta name="twitter:image" content={seo.image} />
      <meta name="twitter:image:alt" content={imageAlt} />

      {schema && (
        <script type="application/ld+json">{JSON.stringify(schema)}</script>
      )}
    </Helmet>
  );
}
