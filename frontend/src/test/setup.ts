import "@testing-library/jest-dom/vitest";
import { vi } from "vitest";

vi.mock("echarts/core", () => ({
  init: () => ({
    dispose: () => undefined,
    resize: () => undefined,
    setOption: () => undefined,
  }),
  use: () => undefined,
}));

class TestResizeObserver implements ResizeObserver {
  disconnect() {}

  observe() {}

  unobserve() {}
}

Object.defineProperty(window, "ResizeObserver", {
  writable: true,
  value: TestResizeObserver,
});

Object.defineProperty(window, "matchMedia", {
  writable: true,
  value: (query: string) => ({
    matches: false,
    media: query,
    onchange: null,
    addListener: () => undefined,
    removeListener: () => undefined,
    addEventListener: () => undefined,
    removeEventListener: () => undefined,
    dispatchEvent: () => false,
  }),
});
