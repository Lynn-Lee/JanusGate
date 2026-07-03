import { ConfigProvider, App as AntApp } from 'antd';
import zhCN from 'antd/locale/zh_CN';
import { Navigate, Route, BrowserRouter, Routes } from 'react-router-dom';
import { AuthProvider, useAuth } from './auth/AuthContext';
import { AppShell } from './components/AppShell';
import { AccountsPage } from './pages/AccountsPage';
import { AssetsPage } from './pages/AssetsPage';
import { AuditsPage } from './pages/AuditsPage';
import { LoginPage } from './pages/LoginPage';
import { SessionsPage } from './pages/SessionsPage';
import { SettingsPage } from './pages/SettingsPage';
import { TenancyPage } from './pages/TenancyPage';
import { WorkflowPage } from './pages/WorkflowPage';
import './styles.css';

function ProtectedShell() {
  const { isAuthenticated } = useAuth();
  if (!isAuthenticated) {
    return <Navigate to="/login" replace />;
  }
  return <AppShell />;
}

export default function App() {
  return (
    <ConfigProvider locale={zhCN} theme={{ token: { colorPrimary: '#2563eb', borderRadius: 10 } }}>
      <AntApp>
        <AuthProvider>
          <BrowserRouter>
            <Routes>
              <Route path="/login" element={<LoginPage />} />
              <Route element={<ProtectedShell />}>
                <Route path="/assets" element={<AssetsPage />} />
                <Route path="/accounts" element={<AccountsPage />} />
                <Route path="/sessions" element={<SessionsPage />} />
                <Route path="/workflow" element={<WorkflowPage />} />
                <Route path="/tenancy" element={<TenancyPage />} />
                <Route path="/audits" element={<AuditsPage />} />
                <Route path="/settings" element={<SettingsPage />} />
              </Route>
              <Route path="*" element={<Navigate to="/assets" replace />} />
            </Routes>
          </BrowserRouter>
        </AuthProvider>
      </AntApp>
    </ConfigProvider>
  );
}
