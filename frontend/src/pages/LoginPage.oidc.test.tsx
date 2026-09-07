import { render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { AuthProvider } from '../auth/AuthContext';
import { LoginPage } from './LoginPage';

function renderLogin() {
  return render(
    <MemoryRouter initialEntries={['/login']}>
      <AuthProvider>
        <LoginPage />
      </AuthProvider>
    </MemoryRouter>
  );
}

describe('LoginPage OIDC button', () => {
  beforeEach(() => {
    localStorage.clear();
    vi.restoreAllMocks();
  });

  it('shows OIDC button only when enabled and fully configured', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        if (url.endsWith('/api/v1/auth/oidc/login-options')) {
          return Response.json({ enabled: true, display_name: '公司 IdP' });
        }
        return Response.json({ detail: `Unhandled ${url}` }, { status: 404 });
      })
    );
    renderLogin();
    expect(await screen.findByRole('button', { name: '用 公司 IdP 登录' })).toBeInTheDocument();
  });

  it('hides OIDC button when incomplete / disabled', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        if (url.endsWith('/api/v1/auth/oidc/login-options')) {
          return Response.json({ enabled: false, display_name: '' });
        }
        return Response.json({ detail: `Unhandled ${url}` }, { status: 404 });
      })
    );
    renderLogin();
    expect(await screen.findByRole('heading', { name: '登录 JanusGate' })).toBeInTheDocument();
    await waitFor(() => {
      expect(screen.queryByRole('button', { name: /用 .+ 登录/ })).not.toBeInTheDocument();
    });
  });
});
