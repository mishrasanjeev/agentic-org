export function buildDocumentationSchema(
  site: {
    url: string;
    name: string;
    language: string;
    legalName?: string;
    email?: string;
  },
  article: { slug: string; title: string; description: string } | undefined,
  reviewed: string,
): Record<string, unknown>;
