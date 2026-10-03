// SPDX-License-Identifier: Apache-2.0
import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import MarkdownIt from "markdown-it";

const UI_ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const escape = (text) =>
  String(text)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
const headingId = (text) =>
  text
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-|-$/g, "");

function parseFlowStep(line) {
  const [rawTitle, rawBody, ...extras] = line.split("|");
  const title = rawTitle?.trim();
  if (!title || !rawBody?.trim())
    throw new Error("Flow steps need a title and description.");
  if (/^(Human decision|If blocked):/i.test(extras[0]?.trim() ?? ""))
    throw new Error("Process-map steps need an Owner field before other fields.");
  if (!/^Owner:\s*/i.test(extras[0]?.trim() ?? "")) {
    return { title, body: [rawBody, ...extras].join("|").trim() };
  }
  const fields = {};
  for (const extra of extras) {
    const match = /^(Owner|Human decision|If blocked):\s*(.+)$/i.exec(extra.trim());
    if (!match) throw new Error(`Invalid flow field: ${extra.trim()}`);
    const key = match[1].toLowerCase();
    if (fields[key]) throw new Error(`Duplicate flow field: ${match[1]}`);
    fields[key] = match[2].trim();
  }
  return {
    title,
    body: rawBody.trim(),
    owner: fields.owner,
    decision: fields["human decision"],
    exception: fields["if blocked"],
  };
}

export function renderGuide(markdown) {
  markdown = markdown.replace(/\r\n?/g, "\n");
  const md = new MarkdownIt({
    html: false,
    linkify: false,
    typographer: false,
  });
  const headings = [];
  const used = new Set();
  md.renderer.rules.heading_open = (tokens, index) => {
    const title = tokens[index + 1].content;
    const base = headingId(title);
    let id = base;
    let suffix = 2;
    while (used.has(id)) id = `${base}-${suffix++}`;
    used.add(id);
    headings.push({ id, title, level: Number(tokens[index].tag.slice(1)) });
    return `<${tokens[index].tag} id="${id}">`;
  };
  const fence = md.renderer.rules.fence;
  md.renderer.rules.fence = (tokens, index, options, env, self) => {
    if (tokens[index].info.trim() !== "flow")
      return fence(tokens, index, options, env, self);
    const steps = tokens[index].content.trim().split("\n")
      .filter((line) => line.trim()).map(parseFlowStep);
    const processMap = steps.some((step) => step.owner);
    if (processMap && steps.some((step) => !step.owner))
      throw new Error("Every process-map step needs an Owner field.");
    if (processMap) {
      return (
        '<ol class="docs-flow docs-process-map" aria-label="Process map">' +
        steps.map((step, i) =>
          `<li><span class="docs-flow-number" aria-hidden="true">${i + 1}</span>` +
          '<div class="docs-process-content"><div class="docs-process-heading">' +
          `<strong>${escape(step.title)}</strong>` +
          `<span class="docs-process-owner"><span>Owner:</span> ${escape(step.owner)}</span>` +
          `</div><p>${escape(step.body)}</p>` +
          (step.decision || step.exception ? '<div class="docs-process-outcomes">' : "") +
          (step.decision ? `<div class="docs-process-decision"><strong>Human decision</strong><p>${escape(step.decision)}</p></div>` : "") +
          (step.exception ? `<div class="docs-process-exception"><strong>Blocked / exception</strong><p>${escape(step.exception)}</p></div>` : "") +
          (step.decision || step.exception ? "</div>" : "") +
          "</div></li>",
        ).join("") +
        "</ol>"
      );
    }
    return (
      `<ol class="docs-flow${steps.length === 6 ? " docs-flow-six" : ""}" aria-label="Workflow">` +
      steps
        .map((step, i) => {
          return `<li><span class="docs-flow-number">${i + 1}</span><strong>${escape(step.title)}</strong><p>${escape(step.body)}</p></li>`;
        })
        .join("") +
      "</ol>"
    );
  };
  // Tables scroll independently on narrow screens; article text never widens the page.
  md.renderer.rules.table_open = () =>
    '<div class="docs-table-scroll" tabindex="0" role="region" aria-label="Reference table"><table>';
  md.renderer.rules.table_close = () => "</table></div>";
  const html = md.render(markdown);
  const searchText = markdown
    .replace(/```flow\n|```[a-z]*\n|```/g, " ")
    .replace(/[#*`|]/g, " ");
  return {
    html,
    headings,
    searchText,
    minutes: Math.max(2, Math.ceil(searchText.split(/\s+/).length / 200)),
  };
}

export function loadUserGuides(root = UI_ROOT) {
  const repo = resolve(root, "..");
  const directory = join(repo, "docs/user-guide");
  if (!existsSync(join(directory, "index.json")))
    throw new Error("Missing user-guide manifest; refusing to build an empty manual.");
  const manifest = JSON.parse(
    readFileSync(join(directory, "index.json"), "utf8"),
  );
  const slugs = new Set();
  const articles = manifest.articles.map((article) => {
    if (
      !/^[a-z0-9]+(?:-[a-z0-9]+)*$/.test(article.slug) ||
      slugs.has(article.slug)
    ) {
      throw new Error(`Invalid or duplicate guide slug: ${article.slug}`);
    }
    slugs.add(article.slug);
    if (!manifest.groups.includes(article.group))
      throw new Error(`Unknown guide group: ${article.group}`);
    for (const source of article.sources) {
      const file = resolve(repo, source);
      if (!file.startsWith(repo + "/") && !file.startsWith(repo + "\\"))
        throw new Error(`Invalid source: ${source}`);
      if (!existsSync(file))
        throw new Error(`Missing source for ${article.slug}: ${source}`);
    }
    const markdown = readFileSync(
      join(directory, article.slug + ".md"),
      "utf8",
    );
    if (/^# /m.test(markdown))
      throw new Error(`Guide titles belong in index.json: ${article.slug}`);
    if (!markdown.includes("## ") || markdown.split(/\s+/).length < 200)
      throw new Error(`Incomplete guide: ${article.slug}`);
    for (const image of markdown.matchAll(/!\[[^\]]*\]\((\/[^)]+)\)/g)) {
      if (!existsSync(join(root, "public", image[1])))
        throw new Error(`Missing image: ${image[1]}`);
    }
    return {
      ...article,
      markdown,
      ...renderGuide(markdown),
      reviewed: article.reviewed ?? manifest.reviewed,
    };
  });
  for (const article of articles) {
    for (const link of article.markdown.matchAll(
      /\]\(\/docs\/([a-z0-9-]+)(?:#([a-z0-9-]+))?\)/g,
    )) {
      const target = articles.find((item) => item.slug === link[1]);
      if (
        !target ||
        (link[2] && !target.headings.some((heading) => heading.id === link[2]))
      ) {
        throw new Error(`Broken guide link in ${article.slug}: ${link[0]}`);
      }
    }
  }
  return { ...manifest, articles };
}

export function generateUserDocs(root = UI_ROOT) {
  const { articles, ...manifest } = loadUserGuides(root);
  const output = join(root, "src/content/userDocs.generated.json");
  mkdirSync(dirname(output), { recursive: true });
  writeFileSync(
    output,
    JSON.stringify(
      {
        ...manifest,
        articles: articles.map(
          ({ markdown: _markdown, ...article }) => article,
        ),
      },
      null,
      2,
    ) + "\n",
  );
  return articles.length;
}

if (
  process.argv[1] &&
  resolve(process.argv[1]) === fileURLToPath(import.meta.url)
) {
  console.log(`Generated ${generateUserDocs()} public user guides.`);
}
