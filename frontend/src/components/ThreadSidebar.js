import { formatDistanceToNow } from '../lib/timeAgo';

export default function ThreadSidebar({ threads, loading, activeThreadId, onSelect }) {
  if (loading) {
    return (
      <div className="p-3 space-y-2" data-testid="threads-loading">
        {[0, 1, 2].map((i) => (
          <div key={i} className="h-14 bg-night-surfaceAlt animate-pulse" />
        ))}
      </div>
    );
  }
  if (!threads.length) {
    return (
      <div className="p-6 text-center" data-testid="threads-empty">
        <div className="mono text-[10px] uppercase tracking-widest text-night-textMuted mb-2">
          no threads yet
        </div>
        <p className="text-sm text-night-textMuted">
          Start a new conversation to begin.
        </p>
      </div>
    );
  }

  return (
    <ul className="p-2 space-y-1" data-testid="threads-list">
      {threads.map((t) => {
        const active = t.id === activeThreadId;
        return (
          <li key={t.id}>
            <button
              onClick={() => onSelect(t.id)}
              data-testid={`thread-item-${t.id}`}
              className={`w-full text-left px-3 py-2 border transition-colors ${
                active
                  ? 'border-night-text bg-night-surfaceAlt'
                  : 'border-transparent hover:bg-night-surfaceAlt hover:border-night-border'
              }`}
            >
              <div className="text-sm truncate">{t.title}</div>
              <div className="mono text-[10px] text-night-textMuted mt-1 uppercase tracking-widest flex items-center gap-2">
                <span>{formatDistanceToNow(t.updated_at)}</span>
                <span className="opacity-40">·</span>
                <span>{t.message_count} msg</span>
              </div>
            </button>
          </li>
        );
      })}
    </ul>
  );
}
