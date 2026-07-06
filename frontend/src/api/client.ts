export type ApiErrorPayload = {
  code?: string;
  message?: string;
  detail?: unknown;
  request_id?: string;
};

export type ApiError = Error & {
  code: string;
  status: number;
  detail?: unknown;
  requestId?: string;
};

const statusCodeMap: Record<number, string> = {
  400: 'BAD_REQUEST',
  401: 'UNAUTHORIZED',
  403: 'FORBIDDEN',
  404: 'NOT_FOUND',
  422: 'VALIDATION_ERROR'
};

export async function parseApiError(response: Response): Promise<ApiError> {
  let payload: ApiErrorPayload | undefined;
  try {
    payload = (await response.json()) as ApiErrorPayload;
  } catch {
    payload = undefined;
  }

  const detail = payload?.detail;
  const detailMessage = typeof detail === 'string' ? detail : undefined;
  const validationMessage = Array.isArray(detail)
    ? detail
        .map((item) => {
          if (item && typeof item === 'object' && 'msg' in item) {
            return String((item as { msg: unknown }).msg);
          }
          return '';
        })
        .filter(Boolean)
        .join('；')
    : undefined;

  const code = payload?.code || statusCodeMap[response.status] || `HTTP_${response.status}`;
  const message = payload?.message || detailMessage || validationMessage || response.statusText || '请求失败';
  const error = new Error(message) as ApiError;
  error.name = 'ApiError';
  error.code = code;
  error.status = response.status;
  error.detail = detail;
  error.requestId = payload?.request_id;
  return error;
}

export type ApiClientOptions = {
  baseUrl?: string;
  getToken?: () => string | null;
  onUnauthorized?: () => void;
};

type DocsScreenshotFixture = {
  api_responses?: Record<string, unknown>;
  evidence?: Array<{
    api_responses?: Record<string, unknown>;
  }>;
};

function getDocsScreenshotFixtureResponse(path: string): unknown | undefined {
  if (!import.meta.env.DEV) {
    return undefined;
  }
  const rawFixture = globalThis.localStorage?.getItem('janusgate-doc-screenshot-fixture');
  if (!rawFixture) {
    return undefined;
  }
  try {
    const fixture = JSON.parse(rawFixture) as DocsScreenshotFixture;
    if (fixture.api_responses && path in fixture.api_responses) {
      return fixture.api_responses[path];
    }
    for (const evidence of fixture.evidence ?? []) {
      if (evidence.api_responses && path in evidence.api_responses) {
        return evidence.api_responses[path];
      }
    }
  } catch {
    return undefined;
  }
  return undefined;
}

export class ApiClient {
  private readonly baseUrl: string;
  private readonly getToken?: () => string | null;
  private readonly onUnauthorized?: () => void;

  constructor(options: ApiClientOptions = {}) {
    this.baseUrl = options.baseUrl ?? import.meta.env.VITE_API_BASE_URL ?? '';
    this.getToken = options.getToken;
    this.onUnauthorized = options.onUnauthorized;
  }

  async request<T>(path: string, init: RequestInit = {}): Promise<T> {
    const docsFixtureResponse = getDocsScreenshotFixtureResponse(path);
    if (docsFixtureResponse !== undefined) {
      return docsFixtureResponse as T;
    }

    const headers = new Headers(init.headers);
    if (!headers.has('Content-Type') && init.body) {
      headers.set('Content-Type', 'application/json');
    }
    const token = this.getToken?.();
    if (token) {
      headers.set('Authorization', `Bearer ${token}`);
    }

    const response = await fetch(`${this.baseUrl}${path}`, { ...init, headers });
    if (!response.ok) {
      const error = await parseApiError(response);
      if (error.status === 401) {
        this.onUnauthorized?.();
      }
      throw error;
    }
    if (response.status === 204) {
      return undefined as T;
    }
    return (await response.json()) as T;
  }

  get<T>(path: string): Promise<T> {
    return this.request<T>(path);
  }

  post<T>(path: string, body?: unknown): Promise<T> {
    return this.request<T>(path, {
      method: 'POST',
      body: body === undefined ? undefined : JSON.stringify(body)
    });
  }
}

export const createApiClient = (options?: ApiClientOptions) => new ApiClient(options);
