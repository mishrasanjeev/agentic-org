// SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
import { describe, expect, it } from 'vitest';
import llmsFull from '../../public/llms-full.txt?raw';
import { BLOG_POSTS } from '../pages/blog/blogData';

describe('llms-full blog index', () => {
  it('names every post by its own title, not by a nested workflow or figure title', () => {
    for (const post of BLOG_POSTS) {
      const line = llmsFull.split(/\r?\n/).find((l) => l.endsWith(`(https://agenticorg.ai/blog/${post.slug})`));
      expect(line, post.slug).toBe(`- [${post.title}](https://agenticorg.ai/blog/${post.slug})`);
    }
  });
});
