import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach, vi } from "vitest";

// Automatic cleanup after each test
afterEach(() => {
  cleanup();
});

// Mock window.matchMedia for responsive components
Object.defineProperty(window, "matchMedia", {
  writable: true,
  value: vi.fn().mockImplementation((query: string) => ({
    matches: false,
    media: query,
    onchange: null,
    addListener: vi.fn(),
    removeListener: vi.fn(),
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
    dispatchEvent: vi.fn(),
  })),
});

// Mock IntersectionObserver
class MockIntersectionObserver {
  observe = vi.fn();
  unobserve = vi.fn();
  disconnect = vi.fn();
}
Object.defineProperty(window, "IntersectionObserver", {
  writable: true,
  value: MockIntersectionObserver,
});

// Mock ResizeObserver (used by Recharts)
class MockResizeObserver {
  observe = vi.fn();
  unobserve = vi.fn();
  disconnect = vi.fn();
}
Object.defineProperty(window, "ResizeObserver", {
  writable: true,
  value: MockResizeObserver,
});

// Mock crypto.randomUUID
Object.defineProperty(crypto, "randomUUID", {
  value: vi.fn(() => "00000000-0000-0000-0000-000000000000"),
});

// Mock localStorage
const store: Record<string, string> = {};
const localStorageMock = {
  getItem: vi.fn((key: string) => store[key] ?? null),
  setItem: vi.fn((key: string, value: string) => {
    store[key] = value;
  }),
  removeItem: vi.fn((key: string) => {
    delete store[key];
  }),
  clear: vi.fn(() => {
    Object.keys(store).forEach((key) => delete store[key]);
  }),
  get length() {
    return Object.keys(store).length;
  },
  key: vi.fn((index: number) => Object.keys(store)[index] ?? null),
};
Object.defineProperty(window, "localStorage", { value: localStorageMock });
