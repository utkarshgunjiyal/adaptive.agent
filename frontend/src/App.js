import { Navigate, Route, Routes } from 'react-router-dom';
import { Toaster } from 'sonner';
import { AuthProvider, useAuth } from './AuthContext';
import LandingPage from './pages/LandingPage';
import LoginPage from './pages/LoginPage';
import RegisterPage from './pages/RegisterPage';
import WorkspacePage from './pages/WorkspacePage';

function ProtectedRoute({ children }) {
  const { user, loading } = useAuth();
  if (loading || user === undefined) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-night-bg text-night-textMuted">
        <span className="mono text-sm tracking-wider" data-testid="app-loading">
          RUNNER.AI · booting…
        </span>
      </div>
    );
  }
  if (!user) return <Navigate to="/login" replace />;
  return children;
}

function AuthRedirect({ children }) {
  const { user, loading } = useAuth();
  if (loading) return null;
  if (user) return <Navigate to="/app" replace />;
  return children;
}

export default function App() {
  return (
    <AuthProvider>
      <Toaster
        theme="dark"
        position="bottom-right"
        toastOptions={{
          style: {
            background: '#151517',
            border: '1px solid #26262A',
            color: '#F5F2EC',
            borderRadius: '4px',
            fontFamily: 'IBM Plex Sans, sans-serif',
          },
        }}
      />
      <Routes>
        <Route path="/" element={<LandingPage />} />
        <Route path="/login" element={<AuthRedirect><LoginPage /></AuthRedirect>} />
        <Route path="/register" element={<AuthRedirect><RegisterPage /></AuthRedirect>} />
        <Route path="/app" element={<ProtectedRoute><WorkspacePage /></ProtectedRoute>} />
        <Route path="/app/:threadId" element={<ProtectedRoute><WorkspacePage /></ProtectedRoute>} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </AuthProvider>
  );
}
