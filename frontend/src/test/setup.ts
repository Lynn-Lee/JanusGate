import '@testing-library/jest-dom/vitest';
import '@ant-design/v5-patch-for-react-19';
import { afterEach } from 'vitest';
import { cleanup } from '@testing-library/react';

afterEach(() => {
  cleanup();
  // antd 静态 Modal/message 渲染在自建容器中，RTL cleanup 不会移除，需手动清理避免跨用例泄漏
  document
    .querySelectorAll('.ant-modal-root, .ant-message-root, .ant-notification-root')
    .forEach((el) => el.remove());
});

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
