import { createContext, useCallback, useContext, useMemo, useState, type ReactNode } from 'react';
import { ApiClient } from '../api/client';

const ACCESS_TOKEN_KEY = 'janusgate-access-token';
const REFRESH_TOKEN_KEY = 'janusgate-refresh-token';

export type UserMe = {
  id: number;
  username: string;
  display_name: string;
  email: string;
  is_superuser: boolean;
  totp_enabled: boolean;
};

type LoginResult = {
  access_token: string;
  refresh_token: string;
  token_type: string;
  requires_2fa?: boolean;
  two_fa_token?: string;
};

type AuthContextValue = {
  token: string | null;
  user: UserMe | null;
  api: ApiClient;
  isAuthenticated: boolean;
  login: (username: string, password: string) => Promise<void>;
  logout: () => void;
  setUser: (user: UserMe | null) => void;
};

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [token, setToken] = useState(() => localStorage.getItem(ACCESS_TOKEN_KEY));
  const [user, setUser] = useState<UserMe | null>(null);

  const logout = useCallback(() => {
    localStorage.removeItem(ACCESS_TOKEN_KEY);
    localStorage.removeItem(REFRESH_TOKEN_KEY);
    setToken(null);
    setUser(null);
  }, []);

  const api = useMemo(
    () =>
      new ApiClient({
        getToken: () => localStorage.getItem(ACCESS_TOKEN_KEY),
        onUnauthorized: logout
      }),
    [logout]
  );

  const login = useCallback(async (username: string, password: string) => {
    const result = await api.post<LoginResult>('/api/v1/auth/login', { username, password });
    if (result.requires_2fa) {
      throw new Error('当前账号需要 MFA，MVP 前端将在后续版本接入 2FA 二次验证。');
    }
    localStorage.setItem(ACCESS_TOKEN_KEY, result.access_token);
    localStorage.setItem(REFRESH_TOKEN_KEY, result.refresh_token);
    setToken(result.access_token);
    const currentUser = await api.get<UserMe>('/api/v1/auth/me');
    setUser(currentUser);
  }, [api]);

  const value = useMemo(
    () => ({ token, user, api, isAuthenticated: Boolean(token), login, logout, setUser }),
    [token, user, api, login, logout]
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth() {
  const value = useContext(AuthContext);
  if (!value) {
    throw new Error('useAuth must be used within AuthProvider');
  }
  return value;
}
