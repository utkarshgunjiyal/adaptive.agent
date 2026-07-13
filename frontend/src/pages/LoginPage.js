import { useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { toast } from 'sonner';
import { ArrowRight, Loader2 } from 'lucide-react';
import { useAuth } from '../AuthContext';

export default function LoginPage() {
  const { login } = useAuth();
  const navigate = useNavigate();
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState('');

  async function onSubmit(e) {
    e.preventDefault();
    setSubmitting(true);
    setError('');
    try {
      await login(email.trim(), password);
      toast.success('Welcome back');
      navigate('/app');
    } catch (err) {
      const detail = err?.response?.data?.detail;
      setError(typeof detail === 'string' ? detail : 'Sign in failed. Please try again.');
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <AuthShell title="Welcome back" subtitle="Sign in to your workspace.">
      <form onSubmit={onSubmit} className="space-y-5" data-testid="login-form">
        <Field
          id="email"
          label="Email"
          type="email"
          value={email}
          onChange={setEmail}
          testid="login-email-input"
          autoComplete="email"
          required
        />
        <Field
          id="password"
          label="Password"
          type="password"
          value={password}
          onChange={setPassword}
          testid="login-password-input"
          autoComplete="current-password"
          required
        />
        {error && (
          <div
            className="text-sm text-signal-web border border-signal-web/30 bg-signal-web/5 px-3 py-2"
            data-testid="login-error"
          >
            {error}
          </div>
        )}
        <button
          type="submit"
          disabled={submitting || !email || !password}
          className="w-full flex items-center justify-center gap-2 px-4 py-3 bg-night-text text-night-bg font-medium disabled:opacity-40 hover:opacity-90 transition-opacity"
          data-testid="login-submit-button"
        >
          {submitting ? <Loader2 className="w-4 h-4 animate-spin" /> : (
            <>
              Sign in
              <ArrowRight className="w-4 h-4" />
            </>
          )}
        </button>
      </form>
      <p className="mt-8 text-sm text-night-textMuted">
        No account?{' '}
        <Link to="/register" className="text-night-text underline underline-offset-4" data-testid="link-register">
          Create one.
        </Link>
      </p>
    </AuthShell>
  );
}

export function AuthShell({ title, subtitle, children }) {
  return (
    <div className="min-h-screen grid lg:grid-cols-12 auth-hero relative">
      <div className="grain absolute inset-0 z-0" />

      {/* Left: brand + quote */}
      <div className="hidden lg:flex lg:col-span-6 relative z-10 border-r border-night-border p-12 flex-col">
        <Link to="/" className="flex items-center gap-3" data-testid="auth-brand-home">
          <div className="w-8 h-8 border border-night-text flex items-center justify-center relative">
            <span className="mono text-sm tracking-tighter">R</span>
            <span className="absolute -bottom-1 -right-1 w-2 h-2 bg-signal-doc" />
          </div>
          <span className="font-serif text-2xl tracking-tight">Runner.ai</span>
        </Link>

        <div className="my-auto max-w-md">
          <div className="mono text-xs text-night-textMuted mb-6 uppercase tracking-widest">
            manifesto · 001
          </div>
          <p className="font-serif text-3xl leading-tight text-night-text">
            Every answer this agent gives should be traceable to a page, a paper, or a URL.
            <span className="text-night-textMuted italic"> No exceptions.</span>
          </p>
          <div className="mt-8 mono text-xs text-night-textMuted tracking-widest uppercase">
            — the runner.ai design principle
          </div>
        </div>

        <div className="mono text-xs text-night-textMuted uppercase tracking-widest">
          gpt-5.2 · tavily · arxiv
        </div>
      </div>

      {/* Right: form */}
      <div className="lg:col-span-6 relative z-10 flex items-center justify-center p-8 lg:p-16">
        <div className="w-full max-w-md">
          <div className="mb-10">
            <h1 className="font-serif text-4xl mb-3 tracking-tight">{title}</h1>
            {subtitle && <p className="text-night-textMuted">{subtitle}</p>}
          </div>
          {children}
        </div>
      </div>
    </div>
  );
}

export function Field({ id, label, type = 'text', value, onChange, testid, autoComplete, required, minLength, maxLength }) {
  return (
    <div>
      <label htmlFor={id} className="mono text-[11px] uppercase tracking-widest text-night-textMuted block mb-2">
        {label}
      </label>
      <input
        id={id}
        type={type}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className="w-full bg-transparent border border-night-border focus:border-night-text px-3 py-3 text-night-text placeholder:text-night-textMuted outline-none transition-colors"
        data-testid={testid}
        autoComplete={autoComplete}
        required={required}
        minLength={minLength}
        maxLength={maxLength}
      />
    </div>
  );
}
