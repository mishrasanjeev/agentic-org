// SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
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

// A figure fence names one diagram from the UI's figure registry:
//   ```figure
//   oacp-flow | Title | Caption read by screen readers, search and llms.txt
//   ```
function parseFigure(content) {
  const lines = content.trim().split("\n").filter((line) => line.trim());
  if (lines.length !== 1) throw new Error("A figure fence holds exactly one line.");
  const [id, title, caption, ...extra] = lines[0].split("|").map((part) => part.trim());
  if (!/^[a-z0-9]+(?:-[a-z0-9]+)*$/.test(id ?? "") || !title || !caption || extra.length)
    throw new Error(`Figure fences need "id | title | caption": ${lines[0]}`);
  return { id, title, caption };
}

// A video fence names one recording kept in ui/public/media/docs/ as <id>.mp4, with
// English captions <id>.vtt and a poster frame <id>.jpg:
//   ```video
//   oacp-end-to-end | Title | What the recording shows, read by search and llms.txt
//   ```
export const VIDEO_PATH = "/media/docs";
function parseVideo(content) {
  const lines = content.trim().split("\n").filter((line) => line.trim());
  if (lines.length !== 1) throw new Error("A video fence holds exactly one line.");
  const [id, title, caption, ...extra] = lines[0].split("|").map((part) => part.trim());
  if (!/^[a-z0-9]+(?:-[a-z0-9]+)*$/.test(id ?? "") || !title || !caption || extra.length)
    throw new Error(`Video fences need "id | title | caption": ${lines[0]}`);
  return { id, title, caption };
}

// The files a video fence needs, relative to ui/public.
export function videoFiles(id) {
  return ["mp4", "vtt", "jpg"].map((ext) => `${VIDEO_PATH.slice(1)}/${id}.${ext}`);
}

// Info string of a flow fence: "flow" or "flow start=N" to continue a numbered checklist.
function parseFlowInfo(info) {
  const match = /^flow(?:\s+start=(\d+))?$/.exec(info.trim());
  if (!match) return null;
  const start = match[1] ? Number(match[1]) : 1;
  if (start < 1) throw new Error("A flow starts at 1 or later.");
  return { start };
}

// Plain markdown for text-only readers (llms.txt, search): figure and summary fences become prose.
export function plainGuideMarkdown(markdown) {
  return markdown
    .replace(/\r\n?/g, "\n")
    .replace(/```figure\n([\s\S]*?)```/g, (_whole, body) => {
      const figure = parseFigure(body);
      return `Figure: ${figure.title}. ${figure.caption}`;
    })
    .replace(/```video\n([\s\S]*?)```/g, (_whole, body) => {
      const video = parseVideo(body);
      return `Video: ${video.title}. ${video.caption} Watch it at ${VIDEO_PATH}/${video.id}.mp4 (English captions at ${VIDEO_PATH}/${video.id}.vtt).`;
    })
    .replace(/```summary\n([\s\S]*?)```/g, (_whole, body) =>
      "In 30 seconds:\n\n" +
      body.trim().split("\n").filter((line) => line.trim()).map((line) => `- ${line.trim()}`).join("\n"));
}

export function renderGuide(markdown) {
  markdown = markdown.replace(/\r\n?/g, "\n");
  const md = new MarkdownIt({
    html: false,
    linkify: false,
    typographer: false,
  });
  const headings = [];
  const figures = [];
  const videos = [];
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
    const info = tokens[index].info.trim();
    if (info === "figure") {
      const figure = parseFigure(tokens[index].content);
      if (figures.some((item) => item.id === figure.id))
        throw new Error(`Duplicate figure in one guide: ${figure.id}`);
      figures.push(figure);
      // The UI swaps this placeholder for the diagram; without scripts the caption remains.
      return (
        `<figure class="docs-figure" data-docs-figure="${figure.id}" aria-labelledby="docs-figure-${figure.id}-title">` +
        `<figcaption><p class="docs-figure-title" id="docs-figure-${figure.id}-title">${escape(figure.title)}</p>` +
        `<p class="docs-figure-caption">${escape(figure.caption)}</p></figcaption></figure>`
      );
    }
    if (info === "video") {
      const video = parseVideo(tokens[index].content);
      if (videos.some((item) => item.id === video.id)) throw new Error(`Duplicate video in one guide: ${video.id}`);
      videos.push(video);
      const base = `${VIDEO_PATH}/${video.id}`;
      return (
        `<figure class="docs-video" aria-labelledby="docs-video-${video.id}-title">` +
        `<video controls preload="metadata" playsinline poster="${base}.jpg" aria-describedby="docs-video-${video.id}-caption">` +
        `<source src="${base}.mp4" type="video/mp4">` +
        `<track kind="captions" src="${base}.vtt" srclang="en" label="English" default>` +
        `</video>` +
        `<figcaption><p class="docs-figure-title" id="docs-video-${video.id}-title">${escape(video.title)}</p>` +
        `<p class="docs-figure-caption" id="docs-video-${video.id}-caption">${escape(video.caption)} ` +
        `<a href="${base}.mp4">Download the video</a> · <a href="${base}.vtt">Captions</a></p></figcaption></figure>`
      );
    }
    if (info === "summary") {
      const items = tokens[index].content.trim().split("\n").filter((line) => line.trim());
      if (!items.length) throw new Error("A summary fence needs at least one line.");
      return (
        '<aside class="docs-summary" aria-label="In 30 seconds"><p class="docs-summary-title">In 30 seconds</p><ul>' +
        items.map((item) => `<li>${md.renderInline(item.trim())}</li>`).join("") +
        "</ul></aside>"
      );
    }
    const flow = parseFlowInfo(info);
    if (!flow)
      return fence(tokens, index, options, env, self);
    const steps = tokens[index].content.trim().split("\n")
      .filter((line) => line.trim()).map(parseFlowStep);
    const processMap = steps.some((step) => step.owner);
    if (processMap && steps.some((step) => !step.owner))
      throw new Error("Every process-map step needs an Owner field.");
    const startAttr = flow.start > 1 ? ` start="${flow.start}"` : "";
    if (processMap) {
      return (
        `<ol class="docs-flow docs-process-map" aria-label="Process map"${startAttr}>` +
        steps.map((step, i) =>
          `<li><span class="docs-flow-number" aria-hidden="true">${i + flow.start}</span>` +
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
      `<ol class="docs-flow${steps.length === 6 ? " docs-flow-six" : ""}" aria-label="Workflow"${startAttr}>` +
      steps
        .map((step, i) => {
          return `<li><span class="docs-flow-number">${i + flow.start}</span><strong>${escape(step.title)}</strong><p>${escape(step.body)}</p></li>`;
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
  const searchText = plainGuideMarkdown(markdown)
    .replace(/```[a-z]*(?: start=\d+)?\n|```/g, " ")
    .replace(/[#*`|]/g, " ");
  return {
    html,
    headings,
    figures,
    videos,
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
    // "adaptive" guides follow the reader's light or dark preference; the rest stay light.
    const colorScheme = article.colorScheme ?? "light";
    if (!["light", "adaptive"].includes(colorScheme))
      throw new Error(`Invalid colorScheme for ${article.slug}: ${colorScheme}`);
    const rendered = renderGuide(markdown);
    for (const video of rendered.videos)
      for (const file of videoFiles(video.id))
        if (!existsSync(join(root, "public", file)))
          throw new Error(`Missing video file for ${article.slug}: ${file}`);
    return {
      ...article,
      colorScheme,
      markdown,
      ...rendered,
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
