import { useEffect, useState } from 'react';
import { Link, Route, Routes } from 'react-router-dom';

import { Alert, AlertDescription } from '@/components/ui/alert';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Separator } from '@/components/ui/separator';
import {
  ApiError,
  AUTH_EXPIRED_EVENT,
  SESSION_EXPIRED_MESSAGE,
  getCurrentUser,
  login,
  logout,
  type AuthUser,
} from './lib/api';
import { HomePage } from './pages/HomePage';
import { RunHistoryPage } from './pages/RunHistoryPage';
import { RunPage } from './pages/RunPage';
import { RunResultsPage } from './pages/RunResultsPage';

function LoginPage({
  onLoginSuccess,
  initialError,
}: {
  onLoginSuccess: (user: AuthUser) => void;
  initialError?: string | null;
}) {
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState<string | null>(initialError ?? null);
  const [loading, setLoading] = useState(false);

  const handleSubmit = async (e: { preventDefault: () => void }) => {
    e.preventDefault();
    setError(null);
    setLoading(true);

    try {
      const response = await login(username, password);
      onLoginSuccess(response.user);
    } catch (err) {
      if (err instanceof ApiError) {
        setError(err.message);
      } else {
        setError('登录失败，请检查网络连接');
      }
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="min-h-screen bg-background flex items-center justify-center p-4">
      <Card className="w-full max-w-sm">
        <CardHeader>
          <CardTitle className="text-center">COMET-L 登录</CardTitle>
        </CardHeader>
        <CardContent>
          <form onSubmit={handleSubmit} className="space-y-4">
            <div className="space-y-2">
              <Label htmlFor="username">用户名</Label>
              <Input
                id="username"
                type="text"
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                required
                autoComplete="username"
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="password">密码</Label>
              <Input
                id="password"
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                required
                autoComplete="current-password"
              />
            </div>
            {error && (
              <Alert variant="destructive">
                <AlertDescription>{error}</AlertDescription>
              </Alert>
            )}
            <Button type="submit" className="w-full" disabled={loading}>
              {loading ? '登录中...' : '登录'}
            </Button>
          </form>
        </CardContent>
      </Card>
    </div>
  );
}

function AuthenticatedApp({ user, onLogout }: { user: AuthUser; onLogout: () => void }) {
  const handleLogout = async () => {
    try {
      await logout();
      onLogout();
    } catch {
      onLogout();
    }
  };

  return (
    <div className="min-h-screen bg-background">
      <header className="sticky top-0 z-10 bg-background/95 backdrop-blur border-b border-border">
        <div className="max-w-5xl mx-auto px-4 h-12 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <span className="text-xs font-mono text-muted-foreground tracking-widest uppercase">
              COMET-L
            </span>
            <Separator orientation="vertical" className="h-4" />
            <span className="text-sm font-medium">Web 控制台</span>
          </div>
          <div className="flex items-center gap-2">
            <nav className="flex items-center gap-1" aria-label="主导航">
              <Button variant="ghost" size="sm" asChild>
                <Link to="/">首页</Link>
              </Button>
              <Button variant="ghost" size="sm" asChild>
                <Link to="/runs/history">运行记录</Link>
              </Button>
            </nav>
            <Separator orientation="vertical" className="h-4" />
            <div className="flex items-center gap-2">
              <span className="text-sm font-medium">{user.username}</span>
              <Badge variant={user.role === 'admin' ? 'default' : 'secondary'}>
                {user.role === 'admin' ? '管理员' : '用户'}
              </Badge>
              <Button variant="ghost" size="sm" onClick={handleLogout}>
                注销
              </Button>
            </div>
          </div>
        </div>
      </header>

      <main className="max-w-5xl mx-auto px-4 py-6">
        <Routes>
          <Route path="/" element={<HomePage user={user} />} />
          <Route path="/runs/history" element={<RunHistoryPage />} />
          <Route path="/runs/:runId" element={<RunPage />} />
          <Route path="/runs/:runId/results" element={<RunResultsPage />} />
        </Routes>
      </main>
    </div>
  );
}

export function App() {
  const [user, setUser] = useState<AuthUser | null>(null);
  const [loading, setLoading] = useState(true);
  const [authMessage, setAuthMessage] = useState<string | null>(null);

  useEffect(() => {
    getCurrentUser()
      .then((response) => {
        setUser(response.user);
        setAuthMessage(null);
      })
      .catch(() => {
        setUser(null);
      })
      .finally(() => {
        setLoading(false);
      });
  }, []);

  useEffect(() => {
    const handleAuthExpired = () => {
      setUser(null);
      setAuthMessage(SESSION_EXPIRED_MESSAGE);
      setLoading(false);
    };

    window.addEventListener(AUTH_EXPIRED_EVENT, handleAuthExpired);
    return () => {
      window.removeEventListener(AUTH_EXPIRED_EVENT, handleAuthExpired);
    };
  }, []);

  if (loading) {
    return (
      <div className="min-h-screen bg-background flex items-center justify-center">
        <span className="text-sm text-muted-foreground">加载中...</span>
      </div>
    );
  }

  if (!user) {
    return (
      <LoginPage
        initialError={authMessage}
        onLoginSuccess={(nextUser) => {
          setAuthMessage(null);
          setUser(nextUser);
        }}
      />
    );
  }

  return <AuthenticatedApp user={user} onLogout={() => setUser(null)} />;
}
