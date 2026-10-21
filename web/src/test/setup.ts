import "@testing-library/jest-dom/vitest";

window.getComputedStyle = () =>
  ({
    getPropertyValue: () => "",
    width: "0px",
    height: "0px",
  }) as unknown as CSSStyleDeclaration;

Object.defineProperty(window, "matchMedia", {
  writable: true,
  value: (query: string) => ({
    matches: false,
    media: query,
    onchange: null,
    addListener: () => {},
    removeListener: () => {},
    addEventListener: () => {},
    removeEventListener: () => {},
    dispatchEvent: () => false,
  }),
});
