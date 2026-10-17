// SPDX-License-Identifier: Apache-2.0
import { render, screen } from '@testing-library/react';
import { HelmetProvider } from 'react-helmet-async';
import { MemoryRouter, Route, Routes } from 'react-router';
import { describe, expect, it } from 'vitest';
import BlogPost from '../pages/blog/BlogPost';

describe('security response article', () => {
  it('renders official clickable sources and explicit sandbox and deployment limits', () => {
    render(<HelmetProvider><MemoryRouter initialEntries={['/blog/cert-in-ai-agent-security']}>
      <Routes><Route path="/blog/:slug" element={<BlogPost />} /></Routes>
    </MemoryRouter></HelmetProvider>);
    expect(screen.getByRole('link', { name: /CERT-In CIAD-2026-0049:/ })).toHaveAttribute('href',
      'https://www.cert-in.org.in/s2cMainServlet?pageid=PUBVLNOTES02&VLCODE=CIAD-2026-0049');
    expect(screen.getByText(/merchant-onboarding work discussed in our pilot remains sandbox-only/)).toBeInTheDocument();
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
  });
});
