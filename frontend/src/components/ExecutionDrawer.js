import { useMemo, useState } from 'react';
import { CheckCircle2, XCircle, Loader2, Clock, ChevronDown, ChevronRight, Terminal, Zap, FileText, BookOpen, Globe, MessageSquare } from 'lucide-react';

export default function ExecutionDrawer({ runId, events, inFlight }) {
  const state = useMemo(() => buildState(events), [events]);

  return (
    <div className="flex flex-col h-full">
      <div className="p-4 border-b border-night-border">
        <div className="mono text-[10px] uppercase tracking-widest text-night-textMuted mb-1 flex items-center gap-2">
          <Terminal className="w-3 h-3" />
          execution details
        </div>
        <h2 className="font-serif text-lg tracking-tight">
          {runId ? 'Run trace' : 'Waiting for a run…'}
        </h2>
        {runId && (
          <div className="mono text-[10px] text-night-textMuted mt-1 truncate">
            run_id · <span className="text-night-text">{runId}</span>
          </div>
        )}
      </div>

      <div className="flex-1 overflow-y-auto min-h-0 p-4 space-y-4" data-testid="execution-drawer-body">
        {!events?.length ? (
          <div className="mono text-xs uppercase tracking-widest text-night-textMuted text-center py-12">
            <Zap className="w-4 h-4 mx-auto mb-2 opacity-40" />
            no run yet
            <p className="mt-3 text-night-textMuted normal-case tracking-normal font-sans text-xs">
              Ask something — the trace shows selected tools, generated plan, evidence, and timings.
            </p>
          </div>
        ) : (
          <>
            <Section title="Capabilities selected" defaultOpen>
              {state.selectedTools.length ? (
                <div className="flex flex-wrap gap-2">
                  {state.selectedTools.map((t) => (
                    <ToolChip key={t.id} tool={t} />
                  ))}
                </div>
              ) : (
                <div className="mono text-[10px] text-night-textMuted uppercase tracking-widest">
                  {inFlight ? 'analyzing…' : 'no tools shortlisted'}
                </div>
              )}
            </Section>

            {state.plan && (
              <Section title={`Plan · ${state.plan.steps?.length || 0} step`} defaultOpen>
                <div className="mono text-[11px] text-night-textMuted leading-relaxed mb-3">
                  {state.plan.goal}
                </div>
                <ol className="space-y-2">
                  {(state.plan.steps || []).map((step, i) => (
                    <li key={step.id || i} className="border border-night-border p-3" data-testid={`plan-step-${i + 1}`}>
                      <div className="flex items-center gap-2 mb-1">
                        <span className="mono text-[10px] text-night-textMuted tabular">{String(i + 1).padStart(2, '0')}</span>
                        <span className="mono text-[11px] text-night-text">{step.tool_id}</span>
                        <StepStatus toolCall={state.toolCalls.find((c) => c.step_id === step.id)} />
                      </div>
                      <div className="mono text-[10px] text-night-textMuted leading-relaxed">
                        {step.rationale || step.expected_output}
                      </div>
                    </li>
                  ))}
                </ol>
              </Section>
            )}

            {state.reselections.length > 0 && (
              <Section title={`Reselections · ${state.reselections.length}`} defaultOpen>
                <ul className="space-y-2" data-testid="reselections-list">
                  {state.reselections.map((r, i) => (
                    <li key={i} className="border border-signal-paper/40 bg-signal-paper/5 p-2 mono text-[10px]">
                      <div className="text-signal-paper mb-1 uppercase tracking-widest">
                        added: {(r.added || []).join(', ') || '—'}
                      </div>
                      <div className="text-night-textMuted normal-case tracking-normal">
                        {r.reason}
                      </div>
                    </li>
                  ))}
                </ul>
              </Section>
            )}

            {state.toolCalls.length > 0 && (
              <Section title={`Tool calls · ${state.toolCalls.length}`} defaultOpen>
                <ul className="space-y-2">
                  {state.toolCalls.map((c, i) => (
                    <li key={c.id || i} className="border border-night-border p-3" data-testid={`tool-call-${i + 1}`}>
                      <div className="flex items-center justify-between gap-2 mb-1.5">
                        <span className="mono text-[11px] text-night-text truncate">{c.tool_id}</span>
                        <span className="mono text-[10px] text-night-textMuted tabular whitespace-nowrap">
                          {c.latency_ms != null ? `${c.latency_ms}ms` : '…'}
                        </span>
                      </div>
                      <div className="flex items-center gap-2 mb-1">
                        <ToolCallStatus status={c.status} />
                        {c.evidence_count > 0 && (
                          <span className="mono text-[10px] text-night-textMuted">
                            {c.evidence_count} evidence
                          </span>
                        )}
                      </div>
                      {c.output_summary && (
                        <div className="mono text-[11px] text-night-textMuted line-clamp-2 mt-1">
                          {c.output_summary}
                        </div>
                      )}
                      {c.error && (
                        <div className="mono text-[11px] text-signal-web mt-1">
                          {c.error}
                        </div>
                      )}
                    </li>
                  ))}
                </ul>
              </Section>
            )}

            {state.evidence.length > 0 && (
              <Section title={`Evidence · ${state.evidence.length}`}>
                <ul className="space-y-1.5">
                  {state.evidence.slice(0, 20).map((e, i) => (
                    <li key={e.id || i} className="mono text-[11px] flex items-start gap-2" data-testid={`evidence-item-${i + 1}`}>
                      <span className="text-night-textMuted tabular w-6 shrink-0">[{i + 1}]</span>
                      <SourceIcon kind={e.source_type} />
                      <span className="text-night-text truncate flex-1">{e.title}</span>
                      {e.score != null && (
                        <span className="text-night-textMuted tabular">{Number(e.score).toFixed(2)}</span>
                      )}
                    </li>
                  ))}
                </ul>
              </Section>
            )}

            <Section title="Event stream">
              <ol className="space-y-1 mono text-[10px]">
                {events.slice(-40).map((e, i) => (
                  <li key={i} className="flex items-center gap-2">
                    <EventDot event={e.event} />
                    <span className="text-night-textMuted uppercase tracking-widest">{e.event}</span>
                  </li>
                ))}
              </ol>
            </Section>

            {state.completed && (
              <div className="border border-signal-doc/40 bg-signal-doc/5 p-3 space-y-2" data-testid="run-completed-card">
                <div className="mono text-[10px] uppercase tracking-widest text-signal-doc flex items-center gap-2">
                  <CheckCircle2 className="w-3 h-3" />
                  completed
                </div>
                <div className="grid grid-cols-3 gap-2 text-center">
                  <Stat label="duration" value={state.durationMs != null ? `${state.durationMs}ms` : '—'} />
                  <Stat label="tools" value={state.toolCalls.length} />
                  <Stat label="evidence" value={state.evidence.length} />
                </div>
                {state.toolCalls.length > 0 && (
                  <div className="pt-2 border-t border-night-border">
                    <div className="mono text-[10px] uppercase tracking-widest text-night-textMuted mb-1">
                      per-tool latency
                    </div>
                    <ul className="space-y-0.5">
                      {state.toolCalls.map((c, i) => (
                        <li key={i} className="flex items-center gap-2 mono text-[10px]">
                          <span className="text-night-text truncate flex-1">{c.tool_id}</span>
                          <span className="text-night-textMuted tabular">
                            {c.latency_ms != null ? `${c.latency_ms}ms` : '—'}
                          </span>
                        </li>
                      ))}
                    </ul>
                  </div>
                )}
              </div>
            )}
            {state.failed && (
              <div className="border border-signal-web/40 bg-signal-web/5 p-3">
                <div className="mono text-[10px] uppercase tracking-widest text-signal-web flex items-center gap-2">
                  <XCircle className="w-3 h-3" />
                  failed
                </div>
                {state.error && (
                  <div className="mono text-[11px] text-signal-web mt-1">{state.error}</div>
                )}
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
}

function Stat({ label, value }) {
  return (
    <div className="border border-night-border py-1.5 px-1">
      <div className="mono text-sm tabular text-night-text">{value}</div>
      <div className="mono text-[9px] text-night-textMuted uppercase tracking-widest">{label}</div>
    </div>
  );
}

function Section({ title, defaultOpen = false, children }) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <div className="border border-night-border" data-testid={`section-${title.split(' ')[0].toLowerCase()}`}>
      <button
        onClick={() => setOpen((v) => !v)}
        className="w-full flex items-center justify-between px-3 py-2 hover:bg-night-surfaceAlt transition-colors"
      >
        <span className="mono text-[10px] uppercase tracking-widest text-night-text">
          {title}
        </span>
        {open ? (
          <ChevronDown className="w-3 h-3 text-night-textMuted" />
        ) : (
          <ChevronRight className="w-3 h-3 text-night-textMuted" />
        )}
      </button>
      {open && <div className="p-3 border-t border-night-border">{children}</div>}
    </div>
  );
}

function ToolChip({ tool }) {
  const meta = BADGE_META[tool.badge] || BADGE_META.context;
  return (
    <span className={`inline-flex items-center gap-1.5 mono text-[10px] uppercase tracking-widest border px-2 py-0.5 ${meta.color}`}>
      {meta.icon}
      {tool.name}
    </span>
  );
}

function StepStatus({ toolCall }) {
  if (!toolCall) {
    return <Clock className="w-3 h-3 text-night-textMuted ml-auto" />;
  }
  return <ToolCallStatus status={toolCall.status} inline />;
}

function ToolCallStatus({ status, inline }) {
  let icon;
  let cls;
  let label;
  switch (status) {
    case 'running':
      icon = <Loader2 className="w-3 h-3 animate-spin" />;
      cls = 'text-blue-400';
      label = 'running';
      break;
    case 'ok':
      icon = <CheckCircle2 className="w-3 h-3" />;
      cls = 'text-signal-doc';
      label = 'ok';
      break;
    case 'error':
      icon = <XCircle className="w-3 h-3" />;
      cls = 'text-signal-web';
      label = 'error';
      break;
    case 'skipped':
      icon = <XCircle className="w-3 h-3" />;
      cls = 'text-night-textMuted';
      label = 'skipped';
      break;
    default:
      icon = <Clock className="w-3 h-3" />;
      cls = 'text-night-textMuted';
      label = 'pending';
  }
  return (
    <span className={`inline-flex items-center gap-1.5 mono text-[10px] uppercase tracking-widest ${cls} ${inline ? 'ml-auto' : ''}`}>
      {icon}
      {label}
    </span>
  );
}

function EventDot({ event }) {
  let color = 'text-night-textMuted';
  if (event === 'answer_delta') color = 'text-signal-paper';
  if (event === 'tool_call') color = 'text-signal-doc';
  if (event === 'run_failed') color = 'text-signal-web';
  if (event === 'run_completed') color = 'text-signal-doc';
  return <span className={`${color}`}>●</span>;
}

const BADGE_META = {
  private_doc: { icon: <FileText className="w-3 h-3" />, color: 'text-signal-doc border-signal-doc/50 bg-signal-doc/10' },
  research_paper: { icon: <BookOpen className="w-3 h-3" />, color: 'text-signal-paper border-signal-paper/50 bg-signal-paper/10' },
  web_source: { icon: <Globe className="w-3 h-3" />, color: 'text-signal-web border-signal-web/50 bg-signal-web/10' },
  context: { icon: <MessageSquare className="w-3 h-3" />, color: 'text-signal-ctx border-signal-ctx/50 bg-signal-ctx/10' },
};

function SourceIcon({ kind }) {
  const meta = BADGE_META[kind] || BADGE_META.context;
  return <span className={meta.color.split(' ')[0]}>{meta.icon}</span>;
}

function buildState(events) {
  const state = {
    selectedTools: [],
    plan: null,
    toolCalls: [],
    evidence: [],
    reselections: [],
    completed: false,
    failed: false,
    error: null,
    durationMs: null,
    runtime: null,
  };
  for (const { event, data } of events) {
    if (event === 'capabilities_selected') {
      const tools = data.tools || [];
      state.selectedTools = tools.map((t) =>
        typeof t === 'string'
          ? { id: t, name: t, badge: 'context' }
          : t,
      );
    } else if (event === 'plan_ready') state.plan = data.plan;
    else if (event === 'tool_call') {
      // Legacy path — one event carries both start and end.
      const idx = state.toolCalls.findIndex((c) => c.step_id === data.step_id);
      if (idx >= 0) state.toolCalls[idx] = data;
      else state.toolCalls.push(data);
    } else if (event === 'tool_started') {
      // Adaptive path — separate start/complete pair.
      state.toolCalls.push({
        id: data.tool_call_id,
        tool_id: data.tool_id,
        status: 'running',
        arguments: data.arguments,
        approval_status: data.approval_status,
      });
    } else if (event === 'tool_completed') {
      const idx = state.toolCalls.findIndex((c) => c.id === data.tool_call_id);
      const patch = {
        status: mapAdaptiveStatus(data.status),
        latency_ms: data.duration_ms,
        evidence_count: data.evidence_count || 0,
        output_summary: data.summary,
        error: (data.error && (data.error.message || data.error.type)) || null,
      };
      if (idx >= 0) state.toolCalls[idx] = { ...state.toolCalls[idx], ...patch };
      else state.toolCalls.push({ id: data.tool_call_id, tool_id: data.tool_id, ...patch });
    } else if (event === 'evidence_ready') {
      state.evidence = data.items || [];
    } else if (event === 'evidence_added') {
      state.evidence = [...state.evidence, ...(data.items || [])];
    } else if (event === 'capability_reselected') {
      state.reselections.push({
        reason: data.reason,
        added: data.added || [],
        bound: data.bound_tools || [],
      });
      state.selectedTools = (data.bound_tools || []).map((n) => ({ id: n, name: n, badge: 'context' }));
    } else if (event === 'run_started') {
      state.runtime = data.runtime || null;
    } else if (event === 'run_completed') {
      state.completed = true;
      state.durationMs = data.duration_ms;
    } else if (event === 'run_failed') {
      state.failed = true;
      state.error = data.error;
    }
  }
  return state;
}

function mapAdaptiveStatus(s) {
  if (s === 'success') return 'ok';
  if (s === 'failed') return 'error';
  if (s === 'rejected') return 'skipped';
  if (s === 'empty') return 'ok';
  if (s === 'unavailable') return 'skipped';
  return s || 'pending';
}
