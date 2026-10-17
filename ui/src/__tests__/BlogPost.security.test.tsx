// SPDX-License-Identifier: Apache-2.0
import { render, screen, within } from '@testing-library/react';
import { HelmetProvider } from 'react-helmet-async';
import { MemoryRouter, Route, Routes } from 'react-router';
import { describe, expect, it } from 'vitest';
import BlogPost from '../pages/blog/BlogPost';
import { BLOG_POSTS } from '../pages/blog/blogData';

describe('security response article', () => {
  it('renders official clickable sources and explicit sandbox and deployment limits', () => {
    render(<HelmetProvider><MemoryRouter initialEntries={['/blog/cert-in-ai-agent-security']}>
      <Routes><Route path="/blog/:slug" element={<BlogPost />} /></Routes>
    </MemoryRouter></HelmetProvider>);
    expect(screen.getByRole('link', { name: /CERT-In CIAD-2026-0049:/ })).toHaveAttribute('href',
      'https://www.cert-in.org.in/s2cMainServlet?pageid=PUBVLNOTES02&VLCODE=CIAD-2026-0049');
    expect(screen.getByText(/Live verification and activation stay disabled/)).toBeInTheDocument();
    expect(screen.getByRole('link', { name: /Agent Security Deployment/ })).toHaveAttribute('href',
      'https://agenticorg.ai/docs/cert-in-agent-security');
    const checklist = screen.getByRole('heading', { name: 'Start with a bounded pilot' });
    const contents = screen.getByText('In this guide').closest('details');
    expect(contents).not.toHaveAttribute('open');
    expect(checklist.compareDocumentPosition(contents!)).toBe(Node.DOCUMENT_POSITION_FOLLOWING);
    for (const link of contents!.querySelectorAll<HTMLAnchorElement>('a[href^="#"]')) {
      expect(document.getElementById(link.hash.slice(1))).not.toBeNull();
    }
    expect(screen.getByRole('link', { name: /Official 2026 Advisory Index/ })).toBeInTheDocument();
    expect(screen.getByRole('link', { name: /Reporting and Log Retention/ })).toHaveAttribute('href',
      'https://www.cert-in.org.in/PDF/CERT-In_Directions_70B_28.04.2022.pdf');
    expect(screen.getByText(/not CERT-In endorsement, certification or a complete compliance solution/)).toBeInTheDocument();
    expect(screen.getByText(/not globally enabled in production/)).toBeInTheDocument();
  });

  it('shows three ordered handoffs and makes merchant-specific work explicitly illustrative', () => {
    render(<HelmetProvider><MemoryRouter initialEntries={['/blog/cert-in-ai-agent-security']}>
      <Routes><Route path="/blog/:slug" element={<BlogPost />} /></Routes>
    </MemoryRouter></HelmetProvider>);
    const workflows = BLOG_POSTS[0].workflows!;
    expect(workflows).toHaveLength(3);
    for (const workflow of workflows) {
      expect(BLOG_POSTS[0].content).toContain(`## ${workflow.heading}`);
      const figure = screen.getByRole('figure', { name: workflow.title });
      expect(within(figure).getAllByRole('listitem')).toHaveLength(4);
      expect(within(figure).getByText(workflow.status)).toBeInTheDocument();
      expect(within(figure).getByText(workflow.note)).toBeInTheDocument();
    }
    expect(screen.getByText(/not proof of validated ownership or complete screening/)).toBeInTheDocument();
    expect(screen.getByText(/proposed acceptance gate, not a completed live flow/)).toBeInTheDocument();
    expect(screen.getByText(/does not undo a completed action/)).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Use the deployment and evidence checklist' })).toHaveAttribute('href', '/docs/cert-in-agent-security');
  });

  it('leaves an ordinary article on its existing layout without workflow diagrams', () => {
    const ordinary = BLOG_POSTS.find(post => !post.workflows)!;
    render(<HelmetProvider><MemoryRouter initialEntries={[`/blog/${ordinary.slug}`]}>
      <Routes><Route path="/blog/:slug" element={<BlogPost />} /></Routes>
    </MemoryRouter></HelmetProvider>);
    expect(screen.getByRole('heading', { level: 1, name: ordinary.title })).toBeInTheDocument();
    expect(screen.queryByRole('figure')).not.toBeInTheDocument();
    expect(document.querySelector('.workflow-article')).toBeNull();
  });
});
