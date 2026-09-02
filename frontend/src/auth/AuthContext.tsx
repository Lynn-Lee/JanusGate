import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from 'react';
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
  permissions?: string[];
};

type LoginResult = {
  access_token: string;
  refresh_token: string;
  token_type: string;
  requires_2fa?: boolean;
  two_fa_token?: string;
};

export type LoginOutcome = { status: 'authenticated' } | { status: 'requires_2fa' };

type AuthContextValue = {
  token: string | null;
  user: UserMe | null;
  api: ApiClient;
  isAuthenticated: boolean;
  pendingTwoFa: boolean;
  login: (username: string, password: string) => Promise<LoginOutcome>;
  verifyTwoFa: (totpCode: string) => Promise<void>;
  cancelTwoFa: () => void;
  logout: () => void;
  setUser: (user: UserMe | null) => void;
};

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [token, setToken] = useState(() => localStorage.getItem(ACCESS_TOKEN_KEY));
  const [twoFaToken, setTwoFaToken] = useState('');
  const [user, setUser] = useState<UserMe | null>(null);

  const logout = useCallback(() => {
    localStorage.removeItem(ACCESS_TOKEN_KEY);
    localStorage.removeItem(REFRESH_TOKEN_KEY);
    setToken(null);
    setTwoFaToken('');
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

  useEffect(() => {
    if (!token) {
      return;
    }
    let cancelled = false;
    api
      .get<UserMe>('/api/v1/auth/me')
      .then((current) => {
        if (!cancelled) {
          setUser(current);
        }
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, [api, token]);

  const login = useCallback(async (username: string, password: string): Promise<LoginOutcome> => {
    const result = await api.post<LoginResult>('/api/v1/auth/login', { username, password });
    if (result.requires_2fa) {
      if (!result.two_fa_token) {
        throw new Error('MFA 登录响应缺少二步验证凭证');
      }
      localStorage.removeItem(ACCESS_TOKEN_KEY);
      localStorage.removeItem(REFRESH_TOKEN_KEY);
      setToken(null);
      setUser(null);
      setTwoFaToken(result.two_fa_token);
      return { status: 'requires_2fa' };
    }
    localStorage.setItem(ACCESS_TOKEN_KEY, result.access_token);
    localStorage.setItem(REFRESH_TOKEN_KEY, result.refresh_token);
    setTwoFaToken('');
    setToken(result.access_token);
    const currentUser = await api.get<UserMe>('/api/v1/auth/me');
    setUser(currentUser);
    return { status: 'authenticated' };
  }, [api]);

  const verifyTwoFa = useCallback(
    async (totpCode: string) => {
      if (!twoFaToken) {
        throw new Error('请先完成用户名和密码验证');
      }
      const result = await api.post<LoginResult>('/api/v1/auth/login/2fa', {
        two_fa_token: twoFaToken,
        totp_code: totpCode
      });
      localStorage.setItem(ACCESS_TOKEN_KEY, result.access_token);
      localStorage.setItem(REFRESH_TOKEN_KEY, result.refresh_token);
      setTwoFaToken('');
      setToken(result.access_token);
      const currentUser = await api.get<UserMe>('/api/v1/auth/me');
      setUser(currentUser);
    },
    [api, twoFaToken]
  );

  const cancelTwoFa = useCallback(() => {
    setTwoFaToken('');
  }, []);

  const value = useMemo(
    () => ({
      token,
      user,
      api,
      isAuthenticated: Boolean(token),
      pendingTwoFa: Boolean(twoFaToken),
      login,
      verifyTwoFa,
      cancelTwoFa,
      logout,
      setUser
    }),
    [token, user, api, twoFaToken, login, verifyTwoFa, cancelTwoFa, logout]
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
