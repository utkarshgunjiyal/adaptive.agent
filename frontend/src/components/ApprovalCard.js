import { useState } from 'react';
import { toast } from 'sonner';
import { ShieldCheck, ShieldX, Loader2, AlertTriangle } from 'lucide-react';
import { approveRun, rejectRun, streamAdaptiveApprove, streamAdaptiveReject } from '../api';

/**
 * Approval card shown inline in the chat when an agent run is waiting for
 * user approval. Works for both legacy and adaptive runs.
 *
 * Props:
 *   runId
 *   steps           — legacy plan step shape OR adaptive proposal shape
 *   runtime         — "adaptive" or "legacy" (defaults to legacy)
 *   onResolved      — called after approve/reject completes
 *   onAdaptiveEvent — for adaptive runs, receives the resume SSE frames so
 *                     the drawer/message stream can update in place.
 */
export default function ApprovalCard({ runId, steps, runtime, onResolved, onAdaptiveEvent }) {
  const [busy, setBusy] = useState(null);
  const isAdaptive = runtime === 'adaptive';

  async function approve() {
    setBusy('approve');
    try {
      if (isAdaptive) {
        await streamAdaptiveApprove({
          runId,
          decisions: null,
          onEvent: onAdaptiveEvent || (() => {}),
        });
        toast.success('Approved — resuming run');
        onResolved?.('approved');
      } else {
        const finalRun = await approveRun(runId);
        toast.success('Action approved — resuming run');
        onResolved?.('approved', finalRun);
      }
    } catch (err) {
      toast.error(err?.response?.data?.detail || err.message || 'Approval failed');
    } finally {
      setBusy(null);
    }
  }

  async function reject() {
    setBusy('reject');
    try {
      if (isAdaptive) {
        await streamAdaptiveReject({
          runId,
          onEvent: onAdaptiveEvent || (() => {}),
        });
        toast('Rejected — nothing was executed.');
        onResolved?.('rejected');
      } else {
        const finalRun = await rejectRun(runId);
        toast('Action rejected. No write occurred.');
        onResolved?.('rejected', finalRun);
      }
    } catch (err) {
      toast.error(err?.response?.data?.detail || err.message || 'Reject failed');
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
        {steps.map((step, i) => {
          const toolId = step.tool_id || step.toolId;
          const args = step.arguments || step.args;
          const rationale = step.rationale;
          return (
            <li
              key={step.id || step.tool_call_id || i}
              className="border border-night-border bg-night-surface p-3"
              data-testid={`approval-step-${i + 1}`}
            >
              <div className="mono text-[11px] text-night-text mb-1">
                {toolId}
              </div>
              {rationale && (
                <div className="text-[12px] text-night-textMuted leading-relaxed mb-2">
                  {rationale}
                </div>
              )}
              {args && Object.keys(args).length > 0 && (
                <pre className="mono text-[10px] text-night-textMuted bg-night-bg border border-night-border p-2 overflow-x-auto max-h-32">
                  {JSON.stringify(args, null, 2)}
                </pre>
              )}
            </li>
          );
        })}
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
