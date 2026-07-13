import { useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { toast } from 'sonner';
import { ArrowRight, Loader2 } from 'lucide-react';
import { useAuth } from '../AuthContext';
import { AuthShell, Field } from './LoginPage';

export default function RegisterPage() {
  const { register } = useAuth();
  const navigate = useNavigate();
  const [name, setName] = useState('');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState('');

  async function onSubmit(e) {
    e.preventDefault();
    setSubmitting(true);
    setError('');
    try {
      await register(email.trim(), password, name.trim());
      toast.success('Welcome to Runner.ai');
      navigate('/app');
    } catch (err) {
      const detail = err?.response?.data?.detail;
      const msg = typeof detail === 'string'
        ? detail
        : Array.isArray(detail)
          ? detail.map((d) => d.msg || JSON.stringify(d)).join(' · ')
          : 'Sign up failed. Please try again.';
      setError(msg);
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <AuthShell title="Create your workspace" subtitle="A private research console — for you alone.">
      <form onSubmit={onSubmit} className="space-y-5" data-testid="register-form">
        <Field
          id="name"
          label="Your name"
          value={name}
          onChange={setName}
          testid="register-name-input"
          autoComplete="name"
          required
          maxLength={100}
        />
        <Field
          id="email"
          label="Email"
          type="email"
          value={email}
          onChange={setEmail}
          testid="register-email-input"
          autoComplete="email"
          required
        />
        <Field
          id="password"
          label="Password"
          type="password"
          value={password}
          onChange={setPassword}
          testid="register-password-input"
          autoComplete="new-password"
          required
          minLength={6}
        />
        {error && (
          <div
            className="text-sm text-signal-web border border-signal-web/30 bg-signal-web/5 px-3 py-2"
            data-testid="register-error"
          >
            {error}
          </div>
        )}
        <button
          type="submit"
          disabled={submitting || !email || !password || password.length < 6 || !name}
          className="w-full flex items-center justify-center gap-2 px-4 py-3 bg-night-text text-night-bg font-medium disabled:opacity-40 hover:opacity-90 transition-opacity"
          data-testid="register-submit-button"
        >
          {submitting ? <Loader2 className="w-4 h-4 animate-spin" /> : (
            <>
              Create workspace
              <ArrowRight className="w-4 h-4" />
            </>
          )}
        </button>
      </form>
      <p className="mt-8 text-sm text-night-textMuted">
        Already have an account?{' '}
        <Link to="/login" className="text-night-text underline underline-offset-4" data-testid="link-login">
          Sign in.
        </Link>
      </p>
    </AuthShell>
  );
}
