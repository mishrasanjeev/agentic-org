// SPDX-License-Identifier: Apache-2.0
// Shared by the interactive reader and static HTML so route changes keep the
// same truthful metadata without requiring unsafe-inline CSP permissions.
export function buildDocumentationSchema(site, article, reviewed) {
  const base = site.url.replace(/\/+$/, "");
  const url = base + "/docs" + (article ? "/" + article.slug : "");
  const title = article?.title ?? "AgenticOrg Documentation";
  const breadcrumbs = [
    { name: "Home", item: base + "/" },
    { name: "Documentation", item: base + "/docs" },
    ...(article ? [{ name: article.title, item: url }] : []),
  ];
  return {
    "@context": "https://schema.org",
    "@graph": [
      {
        "@type": article ? "TechArticle" : "CollectionPage",
        "@id": url + "#document",
        url,
        name: title,
        ...(article
          ? { headline: title, description: article.description }
          : {}),
        dateModified: reviewed,
        inLanguage: site.language,
        isPartOf: { "@type": "WebSite", name: site.name, url: base + "/" },
        publisher: {
          "@type": "Organization",
          name: site.legalName || site.name,
          url: base,
          email: site.email,
        },
        breadcrumb: { "@id": url + "#breadcrumb" },
      },
      {
        "@type": "BreadcrumbList",
        "@id": url + "#breadcrumb",
        itemListElement: breadcrumbs.map((crumb, index) => ({
          "@type": "ListItem",
          position: index + 1,
          ...crumb,
        })),
      },
    ],
  };
}
