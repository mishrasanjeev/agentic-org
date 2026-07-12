import { render, screen, waitFor } from "@testing-library/react";
import { HelmetProvider } from "react-helmet-async";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { describe, expect, it } from "vitest";
import RouteSeo, { buildRouteSchema, resolveRouteSeo } from "@/components/RouteSeo";
import BlogPost from "@/pages/blog/BlogPost";
import ResourcePage from "@/pages/resources/ResourcePage";
import { BLOG_POSTS } from "@/pages/blog/blogData";
import { CONTENT_PAGES } from "@/pages/resources/contentData";
import publicSite from "@/content/publicSite.json";

const MOCK_PAGES = [
  {
    path: "/pricing",
    name: "Pricing",
    title: "AgenticOrg Pricing | Governed AI Agent Plans",
    description: "Compare AgenticOrg plans for governed enterprise AI agents.",
    schemaType: "WebPage",
    index: true,
    keywords: ["AI agent pricing"],
  },
  {
    path: "/",
    name: "AgenticOrg",
    title: "AgenticOrg | Governed Enterprise AI Agents",
    description: "Build, deploy, and govern enterprise AI agents.",
    schemaType: "WebPage",
    index: true,
    keywords: ["enterprise AI agents"],
  },
];
const PRICING_PAGE = publicSite.pages.find((page) => page.path === "/pricing")!;


describe("route SEO resolution", () => {
  it("normalizes public paths and builds a route-specific canonical", () => {
    const seo = resolveRouteSeo("/pricing/?campaign=ignored", MOCK_PAGES);

    expect(seo).toMatchObject({
      path: "/pricing",
      name: "Pricing",
      canonical: "https://agenticorg.ai/pricing",
      index: true,
      kind: "public",
    });
  });

  it("uses a truthful Home-to-current-page breadcrumb without fabricated schema", () => {
    const seo = resolveRouteSeo("/pricing", MOCK_PAGES);
    expect(seo).not.toBeNull();

    const schema = buildRouteSchema(seo!);
    const graph = schema?.["@graph"] as Array<Record<string, unknown>>;
    const breadcrumb = graph.find((node) => node["@type"] === "BreadcrumbList");
    const items = breadcrumb?.itemListElement as Array<Record<string, unknown>>;

    expect(items).toEqual([
      {
        "@type": "ListItem",
        position: 1,
        name: "Home",
        item: "https://agenticorg.ai/",
      },
      {
        "@type": "ListItem",
        position: 2,
        name: "Pricing",
        item: "https://agenticorg.ai/pricing",
      },
    ]);
    expect(JSON.stringify(schema)).not.toContain("AggregateRating");
    expect(JSON.stringify(schema)).not.toContain("SearchAction");
    expect(JSON.stringify(schema)).not.toContain("FAQPage");
  });

  it("adds FAQ schema only to the home page from the visible landing FAQ registry", () => {
    const home = resolveRouteSeo("/", MOCK_PAGES);
    const schema = buildRouteSchema(home!);
    const graph = schema?.["@graph"] as Array<Record<string, unknown>>;
    const faq = graph.find((node) => node["@type"] === "FAQPage");

    expect(faq).toBeDefined();
    expect(faq?.mainEntity).toBeInstanceOf(Array);
    expect((faq?.mainEntity as unknown[]).length).toBeGreaterThan(0);
  });

  it("marks account and workspace routes noindex and emits no structured data", () => {
    const dashboard = resolveRouteSeo("/dashboard/agents/agent-1");
    const reset = resolveRouteSeo("/reset-password?token=secret");

    expect(dashboard).toMatchObject({ index: false, kind: "private" });
    expect(reset).toMatchObject({
      path: "/reset-password",
      canonical: "https://agenticorg.ai/reset-password",
      index: false,
      kind: "private",
    });
    expect(buildRouteSchema(dashboard!)).toBeNull();
  });

  it("leaves detail-page metadata to the owning components and noindexes unknown routes", () => {
    expect(resolveRouteSeo("/blog/known-or-unknown")).toBeNull();
    expect(resolveRouteSeo("/resources/known-or-unknown")).toBeNull();
    expect(resolveRouteSeo("/does-not-exist")).toMatchObject({
      canonical: "https://agenticorg.ai/404",
      index: false,
      kind: "not-found",
    });
  });
});

describe("RouteSeo head output", () => {
  it("writes noindex, canonical, Open Graph, and Twitter metadata for private routes", async () => {
    render(
      <HelmetProvider>
        <MemoryRouter initialEntries={["/dashboard/agents"]}>
          <RouteSeo />
        </MemoryRouter>
      </HelmetProvider>,
    );

    await waitFor(() => {
      expect(document.title).toBe("Secure Workspace | AgenticOrg");
    });
    expect(document.querySelector('meta[name="robots"]')).toHaveAttribute(
      "content",
      "noindex, nofollow, noarchive",
    );
    expect(document.querySelector('link[rel="canonical"]')).toHaveAttribute(
      "href",
      "https://agenticorg.ai/dashboard/agents",
    );
    expect(document.querySelector('meta[property="og:title"]')).toHaveAttribute(
      "content",
      "Secure Workspace | AgenticOrg",
    );
    expect(document.querySelector('meta[name="twitter:card"]')).toHaveAttribute(
      "content",
      "summary_large_image",
    );
  });

  it("writes complete metadata for a registered public route", async () => {
    render(
      <HelmetProvider>
        <MemoryRouter initialEntries={["/pricing"]}>
          <RouteSeo />
        </MemoryRouter>
      </HelmetProvider>,
    );

    await waitFor(() => {
      expect(document.title).toBe(PRICING_PAGE.title);
    });
    expect(document.querySelector('meta[name="description"]')).toHaveAttribute(
      "content",
      PRICING_PAGE.description,
    );
    expect(document.querySelector('meta[name="robots"]')).toHaveAttribute(
      "content",
      "index, follow, max-image-preview:large, max-snippet:-1, max-video-preview:-1",
    );
    expect(document.querySelector('link[rel="canonical"]')).toHaveAttribute(
      "href",
      "https://agenticorg.ai/pricing",
    );
    expect(document.querySelector('meta[property="og:url"]')).toHaveAttribute(
      "content",
      "https://agenticorg.ai/pricing",
    );
  });

  it("reconciles pre-rendered route metadata without hydration duplicates", async () => {
    const description = document.createElement("meta");
    description.name = "description";
    description.content = PRICING_PAGE.description;
    description.dataset.rh = "true";
    const robots = document.createElement("meta");
    robots.name = "robots";
    robots.content = "index, follow";
    robots.dataset.rh = "true";
    const canonical = document.createElement("link");
    canonical.rel = "canonical";
    canonical.href = "https://agenticorg.ai/pricing";
    canonical.dataset.rh = "true";
    const schema = document.createElement("script");
    schema.type = "application/ld+json";
    schema.textContent = "{}";
    schema.dataset.rh = "true";
    document.head.append(description, robots, canonical, schema);

    render(
      <HelmetProvider>
        <MemoryRouter initialEntries={["/pricing"]}>
          <RouteSeo />
        </MemoryRouter>
      </HelmetProvider>,
    );

    await waitFor(() => {
      expect(document.head.querySelector('[data-rh="true"]')).toBeNull();
    });
    expect(document.querySelectorAll('meta[name="description"]')).toHaveLength(1);
    expect(document.querySelectorAll('meta[name="robots"]')).toHaveLength(1);
    expect(document.querySelectorAll('link[rel="canonical"]')).toHaveLength(1);
    expect(document.querySelectorAll('script[type="application/ld+json"]')).toHaveLength(1);
  });
});

describe("detail page metadata", () => {
  it("publishes complete BlogPosting and social metadata for a known post", async () => {
    const post = BLOG_POSTS[0];
    const canonical = "https://agenticorg.ai/blog/" + post.slug;

    render(
      <HelmetProvider>
        <MemoryRouter initialEntries={["/blog/" + post.slug]}>
          <Routes>
            <Route path="/blog/:slug" element={<BlogPost />} />
          </Routes>
        </MemoryRouter>
      </HelmetProvider>,
    );

    await waitFor(() => {
      expect(document.title).toBe(post.title + " | AgenticOrg Blog");
    });
    expect(document.querySelector('link[rel="canonical"]')).toHaveAttribute(
      "href",
      canonical,
    );
    expect(document.querySelector('meta[property="og:image"]')).toHaveAttribute(
      "content",
      "https://agenticorg.ai/og-image.png",
    );
    const script = document.querySelector('script[type="application/ld+json"]');
    const schema = JSON.parse(script?.textContent ?? "{}") as {
      "@graph"?: Array<Record<string, unknown>>;
    };
    expect(schema["@graph"]?.some((node) => node["@type"] === "BlogPosting")).toBe(true);
    expect(schema["@graph"]?.some((node) => node["@type"] === "BreadcrumbList")).toBe(true);
    expect(JSON.stringify(schema)).not.toContain("AggregateRating");
    expect(JSON.stringify(schema)).not.toContain("SearchAction");
  });

  it("publishes resource FAQ schema only for the Q&A rendered on the page", async () => {
    const page = CONTENT_PAGES[0];
    const canonical = "https://agenticorg.ai/resources/" + page.slug;

    render(
      <HelmetProvider>
        <MemoryRouter initialEntries={["/resources/" + page.slug]}>
          <Routes>
            <Route path="/resources/:slug" element={<ResourcePage />} />
          </Routes>
        </MemoryRouter>
      </HelmetProvider>,
    );

    await waitFor(() => {
      expect(document.title).toBe(page.metaTitle);
    });
    expect(document.querySelector('link[rel="canonical"]')).toHaveAttribute(
      "href",
      canonical,
    );
    for (const faq of page.faqs) {
      expect(screen.getByText(faq.q)).toBeInTheDocument();
      expect(screen.getByText(faq.a)).toBeInTheDocument();
    }
    const script = document.querySelector('script[type="application/ld+json"]');
    const schema = JSON.parse(script?.textContent ?? "{}") as {
      "@graph"?: Array<Record<string, unknown>>;
    };
    const faqNode = schema["@graph"]?.find((node) => node["@type"] === "FAQPage");
    expect(faqNode?.mainEntity).toHaveLength(page.faqs.length);
  });
});

describe("detail page not-found handling", () => {
  it("redirects an unknown blog slug to the shared 404 route", async () => {
    render(
      <HelmetProvider>
        <MemoryRouter initialEntries={["/blog/not-a-real-post"]}>
          <Routes>
            <Route path="/blog/:slug" element={<BlogPost />} />
            <Route path="/404" element={<p>Shared 404 destination</p>} />
          </Routes>
        </MemoryRouter>
      </HelmetProvider>,
    );

    expect(await screen.findByText("Shared 404 destination")).toBeInTheDocument();
  });

  it("redirects an unknown resource slug to the shared 404 route", async () => {
    render(
      <HelmetProvider>
        <MemoryRouter initialEntries={["/resources/not-a-real-resource"]}>
          <Routes>
            <Route path="/resources/:slug" element={<ResourcePage />} />
            <Route path="/404" element={<p>Shared resource 404 destination</p>} />
          </Routes>
        </MemoryRouter>
      </HelmetProvider>,
    );

    expect(await screen.findByText("Shared resource 404 destination")).toBeInTheDocument();
  });
});
