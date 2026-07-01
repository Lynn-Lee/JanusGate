import '@testing-library/jest-dom/vitest';

Object.defineProperty(window, 'matchMedia', {
  writable: true,
  value: (query: string) => ({
    matches: false,
    media: query,
    onchange: null,
    addListener: () => undefined,
    removeListener: () => undefined,
    addEventListener: () => undefined,
    removeEventListener: () => undefined,
    dispatchEvent: () => false
  })
});

const store = new Map<string, string>();
const memoryStorage: Storage = {
  get length() {
    return store.size;
  },
  clear: () => store.clear(),
  getItem: (key: string) => store.get(key) ?? null,
  key: (index: number) => Array.from(store.keys())[index] ?? null,
  removeItem: (key: string) => void store.delete(key),
  setItem: (key: string, value: string) => void store.set(key, value)
};

Object.defineProperty(window, 'localStorage', { value: memoryStorage, configurable: true });
Object.defineProperty(globalThis, 'localStorage', { value: memoryStorage, configurable: true });

Object.defineProperty(window, 'getComputedStyle', {
  value: () => ({ getPropertyValue: () => '', overflow: 'visible', overflowX: 'visible', overflowY: 'visible' }),
  configurable: true
});
