import { useCallback, useEffect, useState } from 'react';
import { toast } from 'sonner';
import { Plus, Trash2, Loader2, Sparkles, ChevronRight } from 'lucide-react';
import {
  createDigestSchedule,
  deleteDigestSchedule,
  listDigestSchedules,
  listDigests,
} from '../api';
import { formatDistanceToNow } from '../lib/timeAgo';

/**
 * Digest tab — recurring agent runs on a topic. Users create a schedule
 * (topic + cadence). The backend APScheduler runs an agent on that cadence
 * and stores the answer in db.digests. Users open past digests as normal
 * threads.
 */
export default function DigestPanel({ onOpenThread }) {
  const [schedules, setSchedules] = useState([]);
  const [digests, setDigests] = useState([]);
  const [loading, setLoading] = useState(true);
  const [creating, setCreating] = useState(false);
  const [topic, setTopic] = useState('');
  const [cadence, setCadence] = useState('daily');

  const refresh = useCallback(async () => {
    try {
      const [s, d] = await Promise.all([listDigestSchedules(), listDigests()]);
      setSchedules(s);
      setDigests(d);
    } catch (_) {
      /* ignored — panel is optional */
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    refresh();
    const t = setInterval(refresh, 15000);
    return () => clearInterval(t);
  }, [refresh]);

  const create = async () => {
    const t = topic.trim();
    if (t.length < 3) return;
    setCreating(true);
    try {
      await createDigestSchedule(t, cadence);
      toast.success(`Digest scheduled · ${cadence}`);
      setTopic('');
      refresh();
    } catch (err) {
      toast.error(err?.response?.data?.detail || 'Could not create schedule');
    } finally {
      setCreating(false);
    }
  };

  const remove = async (id) => {
    try {
      await deleteDigestSchedule(id);
      refresh();
    } catch (_) { toast.error('Delete failed'); }
  };

  return (
    <div className="p-3 space-y-4" data-testid="digest-panel">
      <div className="border border-night-border p-3">
        <div className="mono text-[10px] uppercase tracking-widest text-night-textMuted mb-2">
          new digest
        </div>
        <input
          value={topic}
          onChange={(e) => setTopic(e.target.value)}
          placeholder="Topic to track (e.g. agentic RAG)"
          className="w-full bg-transparent border border-night-border focus:border-night-text px-3 py-2 text-sm outline-none"
          data-testid="digest-topic-input"
          maxLength={200}
        />
        <div className="mt-2 flex items-center gap-2">
          <select
            value={cadence}
            onChange={(e) => setCadence(e.target.value)}
            className="bg-night-bg border border-night-border text-night-text px-2 py-1 mono text-[11px] uppercase tracking-widest"
            data-testid="digest-cadence-select"
          >
            <option value="hourly">hourly</option>
            <option value="daily">daily</option>
            <option value="weekly">weekly</option>
          </select>
          <button
            onClick={create}
            disabled={creating || topic.trim().length < 3}
            className="ml-auto inline-flex items-center gap-1.5 px-3 py-1.5 bg-night-text text-night-bg text-sm disabled:opacity-40"
            data-testid="digest-create-button"
          >
            {creating ? <Loader2 className="w-3 h-3 animate-spin" /> : <Plus className="w-3 h-3" />}
            Schedule
          </button>
        </div>
      </div>

      {loading ? (
        <div className="space-y-2" data-testid="digest-loading">
          <div className="h-14 bg-night-surfaceAlt animate-pulse" />
          <div className="h-14 bg-night-surfaceAlt animate-pulse" />
        </div>
      ) : (
        <>
          {schedules.length > 0 && (
            <div>
              <div className="mono text-[10px] uppercase tracking-widest text-night-textMuted mb-2 px-1">
                schedules · {schedules.length}
              </div>
              <ul className="space-y-1" data-testid="digest-schedules-list">
                {schedules.map((s) => (
                  <li key={s.id} className="border border-night-border p-2 flex items-center gap-2" data-testid={`digest-schedule-${s.id}`}>
                    <Sparkles className="w-3 h-3 text-signal-paper flex-shrink-0" />
                    <div className="flex-1 min-w-0">
                      <div className="text-sm truncate">{s.topic}</div>
                      <div className="mono text-[10px] text-night-textMuted uppercase tracking-widest">
                        {s.cadence}
                        {s.last_run_at && <> · last {formatDistanceToNow(s.last_run_at)}</>}
                      </div>
                    </div>
                    <button
                      onClick={() => remove(s.id)}
                      className="p-1 text-night-textMuted hover:text-signal-web"
                      aria-label="Delete schedule"
                      data-testid={`digest-delete-${s.id}`}
                    >
                      <Trash2 className="w-3 h-3" />
                    </button>
                  </li>
                ))}
              </ul>
            </div>
          )}

          <div>
            <div className="mono text-[10px] uppercase tracking-widest text-night-textMuted mb-2 px-1">
              runs · {digests.length}
            </div>
            {digests.length === 0 ? (
              <div className="text-center text-[12px] text-night-textMuted py-4" data-testid="digest-runs-empty">
                No digest runs yet. Schedule one above — the first run fires within a minute.
              </div>
            ) : (
              <ul className="space-y-1" data-testid="digest-runs-list">
                {digests.map((d) => (
                  <li key={d.id}>
                    <button
                      onClick={() => d.thread_id && onOpenThread?.(d.thread_id)}
                      className="w-full text-left border border-night-border p-2 hover:border-night-textMuted transition-colors"
                      data-testid={`digest-run-${d.id}`}
                    >
                      <div className="flex items-center gap-2">
                        <span className="text-sm truncate flex-1">{d.topic}</span>
                        <ChevronRight className="w-3 h-3 text-night-textMuted flex-shrink-0" />
                      </div>
                      <div className="mono text-[10px] text-night-textMuted uppercase tracking-widest mt-1">
                        {formatDistanceToNow(d.created_at)} · {d.citation_count} sources
                      </div>
                      {d.answer_preview && (
                        <div className="text-[11px] text-night-textMuted mt-1 line-clamp-2">
                          {d.answer_preview}
                        </div>
                      )}
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </div>
        </>
      )}
    </div>
  );
}
