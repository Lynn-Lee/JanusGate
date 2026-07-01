import { useEffect, useState } from 'react';
import { App } from 'antd';
import type { ApiError } from '../api/client';
import { useAuth } from '../auth/AuthContext';

export function getErrorMessage(error: unknown): string {
  if (error instanceof Error) {
    const apiError = error as Partial<ApiError>;
    return apiError.requestId ? `${error.message}（请求 ${apiError.requestId}）` : error.message;
  }
  return '请求失败';
}

export function useApiData<T>(loader: () => Promise<T>, deps: unknown[] = []) {
  const [data, setData] = useState<T | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  const reload = () => {
    setLoading(true);
    setError('');
    loader()
      .then(setData)
      .catch((err: unknown) => setError(getErrorMessage(err)))
      .finally(() => setLoading(false));
  };

  useEffect(() => {
    reload();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);

  return { data, loading, error, reload };
}

export function useApiMessage() {
  const { message } = App.useApp();
  return {
    success: (text: string) => void message.success(text),
    error: (text: string) => void message.error(text)
  };
}

export function useSessionCache() {
  const { user } = useAuth();
  const key = `janusgate-console-sessions:${user?.id ?? 'anonymous'}`;

  const read = () => {
    try {
      return JSON.parse(localStorage.getItem(key) || '[]') as import('./types').SessionRecord[];
    } catch {
      return [];
    }
  };
  const write = (items: import('./types').SessionRecord[]) => localStorage.setItem(key, JSON.stringify(items));
  return { read, write };
}
