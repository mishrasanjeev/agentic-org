// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
import { render, screen, within } from '@testing-library/react';
import { HelmetProvider } from 'react-helmet-async';
import { MemoryRouter, Route, Routes } from 'react-router';
import { describe, expect, it } from 'vitest';
import BlogPost from '../pages/blog/BlogPost';
import { BLOG_POSTS } from '../pages/blog/blogData';

const SLUG = 'oacp-one-trust-layer-under-ucp-acp-ap2';
const PUBLIC_BLOG_IMAGES = Object.keys(import.meta.glob('../../public/blog/*.png', { query: '?url', import: 'default', eager: true }));
const post = BLOG_POSTS.find((p) => p.slug === SLUG)!;

function renderPost(slug = SLUG) {
  return render(<HelmetProvider><MemoryRouter initialEntries={[`/blog/${slug}`]}>
    <Routes><Route path="/blog/:slug" element={<BlogPost />} /></Routes>
  </MemoryRouter></HelmetProvider>);
}

describe('OACP explainer figures', () => {
  it('places every figure under the content heading it names', () => {
    expect(post.figures).toHaveLength(5);
    renderPost();
    for (const figure of post.figures!) {
      expect(post.content).toContain(`## ${figure.heading}`);
      const heading = screen.getByRole('heading', { level: 2, name: figure.heading });
      const element = screen.getByRole('figure', { name: figure.title });
      expect(heading.compareDocumentPosition(element)).toBe(Node.DOCUMENT_POSITION_FOLLOWING);
      expect(within(element).getByText(figure.caption)).toBeInTheDocument();
    }
  });

  it('gives each diagram a text alternative and draws the hub as two panels', () => {
    renderPost();
    const diagrams = post.figures!.filter((figure) => figure.diagram);
    for (const figure of diagrams) {
      const element = screen.getByRole('figure', { name: figure.title });
      for (const svg of within(element).getAllByRole('img')) {
        const labelIds = svg.getAttribute('aria-labelledby')!.split(' ');
        expect(labelIds).toHaveLength(2);
        for (const id of labelIds) expect(document.getElementById(id)?.textContent?.length).toBeGreaterThan(10);
      }
    }
    const hub = screen.getByRole('figure', { name: 'Wired one by one, or one layer underneath' });
    expect(within(hub).getByRole('img', { name: /Without OACP/ })).toBeInTheDocument();
    expect(within(hub).getByRole('img', { name: /With OACP underneath/ })).toBeInTheDocument();
  });

  it('renders the comparison as a real table with row headers and labelled cells', () => {
    renderPost();
    const figure = screen.getByRole('figure', { name: 'The same shop, wired two ways' });
    const table = within(figure).getByRole('table');
    const { columns, rows } = post.figures!.find((f) => f.table)!.table!;
    expect(within(table).getAllByRole('columnheader').map((th) => th.textContent)).toEqual(columns);
    expect(within(table).getAllByRole('rowheader')).toHaveLength(rows.length);
    for (const cell of table.querySelectorAll('td')) expect(columns).toContain(cell.getAttribute('data-label'));
  });

  it('opens with the 30-second summary, states what is switched off, and links onward', () => {
    renderPost();
    const summary = screen.getByRole('complementary', { name: 'In 30 seconds' });
    expect(within(summary).getAllByRole('listitem')).toHaveLength(post.summary!.length);
    expect(screen.getByText(/ship behind switches that default to off/, { selector: 'p' })).toBeInTheDocument();
    expect(screen.getByText(/does not replace UCP, ACP or AP2/)).toBeInTheDocument();
    expect(screen.getByRole('link', { name: post.nextStep!.label })).toHaveAttribute('href', post.nextStep!.href);
    expect(screen.queryByRole('link', { name: 'Use the deployment and evidence checklist' })).toBeNull();
    expect(document.querySelector('.figure-article.workflow-article')).not.toBeNull();
  });

  it('ships the social preview image it declares', () => {
    expect(post.socialImage).toBe(`/blog/${SLUG}.png`);
    expect(PUBLIC_BLOG_IMAGES).toContain(`../../public${post.socialImage}`);
  });

  it('keeps the dark-mode page styles off posts without figures', () => {
    renderPost('cert-in-ai-agent-security');
    expect(document.querySelector('.workflow-article')).not.toBeNull();
    expect(document.querySelector('.figure-article')).toBeNull();
    expect(screen.queryByRole('complementary', { name: 'In 30 seconds' })).toBeNull();
  });
});
