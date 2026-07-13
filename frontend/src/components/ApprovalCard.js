import { useState } from 'react';
import { toast } from 'sonner';
import { ShieldCheck, ShieldX, Loader2, AlertTriangle } from 'lucide-react';
import { approveRun, rejectRun } from '../api';

/**
 * Approval card shown inline in the chat when an agent run is waiting for
 * user approval. Renders the pending write/sensitive steps + Approve /
 * Reject buttons.
 */
export default function ApprovalCard({ runId, steps, onResolved }) {
  const [busy, setBusy] = useState(null);

  async function approve() {
    setBusy('approve');
    try {
      const finalRun = await approveRun(runId);
      toast.success('Action approved — resuming run');
      onResolved?.('approved', finalRun);
    } catch (err) {
      toast.error(err?.response?.data?.detail || 'Approval failed');
    } finally {
      setBusy(null);
    }
  }

  async function reject() {
    setBusy('reject');
    try {
      const finalRun = await rejectRun(runId);
      toast('Action rejected. No write occurred.');
      onResolved?.('rejected', finalRun);
    } catch (err) {
      toast.error(err?.response?.data?.detail || 'Reject failed');
    } finally {
      setBusy(null);
    }
  }

  return (
    <div
      className="border border-amber-400/60 bg-amber-400/5 p-4 animate-slideUp"
      data-testid={`approval-card-${runId}`}
    >
      <div className="flex items-center gap-2 mb-3">
        <AlertTriangle className="w-4 h-4 text-amber-300" />
        <span className="mono text-[11px] uppercase tracking-widest text-amber-300">
          approval required
        </span>
      </div>

      <p className="text-sm text-night-text mb-4 leading-relaxed">
        The agent wants to run the following <strong>write</strong> action(s) on your account.
        Nothing is executed until you approve.
      </p>

      <ul className="space-y-2 mb-4" data-testid="approval-steps">
        {steps.map((step, i) => (
          <li
            key={step.id || i}
            className="border border-night-border bg-night-surface p-3"
            data-testid={`approval-step-${i + 1}`}
          >
            <div className="mono text-[11px] text-night-text mb-1">
              {step.tool_id}
            </div>
            {step.rationale && (
              <div className="text-[12px] text-night-textMuted leading-relaxed mb-2">
                {step.rationale}
              </div>
            )}
            {step.arguments && Object.keys(step.arguments).length > 0 && (
              <pre className="mono text-[10px] text-night-textMuted bg-night-bg border border-night-border p-2 overflow-x-auto max-h-32">
                {JSON.stringify(step.arguments, null, 2)}
              </pre>
            )}
          </li>
        ))}
      </ul>

      <div className="flex items-center gap-3">
        <button
          onClick={approve}
          disabled={!!busy}
          className="inline-flex items-center gap-2 px-4 py-2 bg-amber-300 text-night-bg font-medium hover:opacity-90 disabled:opacity-40"
          data-testid="approval-approve-button"
        >
          {busy === 'approve' ? <Loader2 className="w-4 h-4 animate-spin" /> : <ShieldCheck className="w-4 h-4" />}
          Approve
        </button>
        <button
          onClick={reject}
          disabled={!!busy}
          className="inline-flex items-center gap-2 px-4 py-2 border border-night-border text-night-text hover:border-night-text disabled:opacity-40"
          data-testid="approval-reject-button"
        >
          {busy === 'reject' ? <Loader2 className="w-4 h-4 animate-spin" /> : <ShieldX className="w-4 h-4" />}
          Reject
        </button>
      </div>
    </div>
  );
}
