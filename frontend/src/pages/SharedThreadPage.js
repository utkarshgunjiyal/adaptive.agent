import { useEffect, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { ArrowUpRight, FileText, BookOpen, Globe, MessageSquare } from 'lucide-react';
import { getSharedThread } from '../api';

const BADGE_META = {
  private_doc: { icon: <FileText className="w-3 h-3" />, color: 'text-signal-doc border-signal-doc/50 bg-signal-doc/10', label: 'Your document' },
  research_paper: { icon: <BookOpen className="w-3 h-3" />, color: 'text-signal-paper border-signal-paper/50 bg-signal-paper/10', label: 'Research paper' },
  web_source: { icon: <Globe className="w-3 h-3" />, color: 'text-signal-web border-signal-web/50 bg-signal-web/10', label: 'Web' },
  context: { icon: <MessageSquare className="w-3 h-3" />, color: 'text-signal-ctx border-signal-ctx/50 bg-signal-ctx/10', label: 'Conversation' },
};

function SourceBadge({ kind }) {
  const m = BADGE_META[kind] || BADGE_META.context;
  return (
    <span className={`inline-flex items-center gap-1.5 mono text-[10px] uppercase tracking-widest border px-2 py-0.5 ${m.color}`}>
      {m.icon}
      {m.label}
    </span>
  );
}

function renderWithCitations(text, citations) {
  const parts = String(text || '').split(/(\[\d+\])/g);
  return parts.map((part, i) => {
    const m = part.match(/^\[(\d+)\]$/);
    if (m) {
      const idx = parseInt(m[1], 10) - 1;
      const cite = citations[idx];
      if (cite) {
        return (
          <a
            key={i}
            href={cite.url || '#'}
            onClick={(e) => { if (!cite.url) e.preventDefault(); }}
            target={cite.url ? '_blank' : undefined}
            rel="noreferrer"
            title={cite.title}
            className="inline-flex items-center mx-0.5 mono text-[10px] px-1.5 py-0.5 border border-night-textMuted text-night-textMuted hover:text-night-text hover:border-night-text align-baseline"
          >
            [{m[1]}]
          </a>
        );
      }
    }
    return <span key={i}>{part}</span>;
  });
}

export default function SharedThreadPage() {
  const { token } = useParams();
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    (async () => {
      try {
        const d = await getSharedThread(token);
        setData(d);
      } catch (err) {
        setError(err?.response?.status === 404 ? 'This link is no longer active.' : 'Could not load thread.');
      } finally {
        setLoading(false);
      }
    })();
  }, [token]);

  if (loading) {
    return (
      <div className="min-h-screen bg-night-bg text-night-textMuted flex items-center justify-center">
        <span className="mono text-sm tracking-wider">Loading shared thread…</span>
      </div>
    );
  }
  if (error) {
    return (
      <div className="min-h-screen bg-night-bg text-night-text flex flex-col items-center justify-center gap-4">
        <div className="mono text-xs uppercase tracking-widest text-signal-web">unavailable</div>
        <h1 className="font-serif text-3xl">{error}</h1>
        <Link to="/" className="text-sm text-night-textMuted hover:text-night-text underline underline-offset-4">
          Go to Runner.ai
        </Link>
      </div>
    );
  }

  const { thread, messages } = data;
  return (
    <div className="min-h-screen bg-night-bg text-night-text" data-testid="shared-thread-page">
      <header className="border-b border-night-border">
        <div className="max-w-3xl mx-auto px-6 py-8">
          <Link to="/" className="mono text-[10px] uppercase tracking-widest text-night-textMuted hover:text-night-text" data-testid="shared-brand">
            Runner.ai · shared
          </Link>
          <h1 className="font-serif text-4xl mt-3 tracking-tight" data-testid="shared-title">{thread.title}</h1>
          <div className="mono text-[10px] uppercase tracking-widest text-night-textMuted mt-2">
            {thread.message_count} messages · read-only
          </div>
        </div>
      </header>

      <main className="max-w-3xl mx-auto px-6 py-10 space-y-8">
        {messages.map((m, i) => (
          <div key={i} data-testid={`shared-message-${i}`}>
            {m.role === 'user' ? (
              <div className="flex justify-end">
                <div className="max-w-[85%] px-4 py-3 bg-night-surface border border-night-border">
                  <p className="whitespace-pre-wrap">{m.content}</p>
                </div>
              </div>
            ) : (
              <div>
                {m.tool_badges?.length > 0 && (
                  <div className="flex items-center gap-2 mb-3 flex-wrap">
                    {m.tool_badges.map((b) => <SourceBadge key={b} kind={b} />)}
                  </div>
                )}
                <div className="answer-prose leading-relaxed">
                  {renderWithCitations(m.content, m.citations || [])}
                </div>
                {m.citations?.length > 0 && (
                  <div className="mt-6 pt-4 border-t border-night-border">
                    <div className="mono text-[10px] uppercase tracking-widest text-night-textMuted mb-3">
                      sources · {m.citations.length}
                    </div>
                    <ol className="space-y-2">
                      {m.citations.map((c, k) => (
                        <li key={k} className="flex items-start gap-3">
                          <span className="mono text-[10px] text-night-textMuted mt-0.5 tabular w-6 shrink-0">[{k + 1}]</span>
                          <SourceBadge kind={c.source_type} />
                          <div className="min-w-0 flex-1">
                            <div className="text-sm truncate">
                              {c.url ? (
                                <a href={c.url} target="_blank" rel="noreferrer" className="hover:underline underline-offset-2 inline-flex items-center gap-1">
                                  {c.title}
                                  <ArrowUpRight className="w-3 h-3 opacity-60" />
                                </a>
                              ) : (
                                c.title
                              )}
                            </div>
                            {c.filename && c.page != null && (
                              <div className="mono text-[10px] text-night-textMuted uppercase tracking-widest mt-1">
                                {c.filename} · page {c.page}
                              </div>
                            )}
                          </div>
                        </li>
                      ))}
                    </ol>
                  </div>
                )}
              </div>
            )}
          </div>
        ))}
      </main>

      <footer className="border-t border-night-border">
        <div className="max-w-3xl mx-auto px-6 py-8 mono text-[10px] uppercase tracking-widest text-night-textMuted flex items-center justify-between">
          <span>© Runner.ai</span>
          <Link to="/register" className="hover:text-night-text underline underline-offset-4" data-testid="shared-cta-register">
            Create your own research workspace →
          </Link>
        </div>
      </footer>
    </div>
  );
}
