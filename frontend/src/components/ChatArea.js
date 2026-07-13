import { useCallback, useEffect, useRef, useState } from 'react';
import { toast } from 'sonner';
import { Send, Loader2, FileText, BookOpen, Globe, MessageSquare, ArrowUpRight } from 'lucide-react';
import { streamAgentRun } from '../api';

export default function ChatArea({
  threadId,
  messages,
  setMessages,
  messagesLoading,
  selectedDocIds,
  documents,
  onDocumentsChange,
  setActiveRunId,
  setRunEvents,
  refreshThreads,
  onEnsureThread,
  runInFlight,
  setRunInFlight,
}) {
  const [input, setInput] = useState('');
  const [streamingText, setStreamingText] = useState('');
  const [streamingCitations, setStreamingCitations] = useState([]);
  const [streamingBadges, setStreamingBadges] = useState([]);
  const scrollRef = useRef(null);

  useEffect(() => {
    // scroll to bottom on new message
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: 'smooth' });
  }, [messages, streamingText]);

  const send = useCallback(async () => {
    const text = input.trim();
    if (!text || runInFlight) return;
    setInput('');
    setRunInFlight(true);
    setStreamingText('');
    setStreamingCitations([]);
    setStreamingBadges([]);
    setRunEvents([]);

    // Optimistic user message.
    const tempUserId = `temp-user-${Date.now()}`;
    setMessages((prev) => [
      ...prev,
      { id: tempUserId, role: 'user', content: text, created_at: new Date().toISOString() },
    ]);

    let localRunId = null;
    let finalAnswer = '';
    let finalCitations = [];
    let finalBadges = [];

    try {
      await streamAgentRun({
        threadId,
        message: text,
        documentIds: selectedDocIds,
        onEvent: ({ event, data }) => {
          setRunEvents((prev) => [...prev, { event, data, ts: Date.now() }]);

          if (event === 'run_started') {
            localRunId = data.run_id;
            setActiveRunId(data.run_id);
            if (!threadId && data.thread_id) {
              onEnsureThread(data.thread_id);
            }
          } else if (event === 'answer_delta') {
            const delta = data.text || '';
            setStreamingText((prev) => prev + delta);
            finalAnswer += delta;
          } else if (event === 'evidence_ready') {
            setStreamingCitations(data.items || []);
            finalCitations = data.items || [];
          } else if (event === 'run_completed') {
            finalAnswer = data.answer || finalAnswer;
            finalCitations = data.citations || finalCitations;
            finalBadges = data.tool_badges || [];
            setStreamingBadges(finalBadges);
          } else if (event === 'run_failed') {
            toast.error(`Run failed: ${data.error || 'unknown error'}`);
          }
        },
      });
    } catch (err) {
      toast.error(`Streaming error: ${err.message || err}`);
    } finally {
      // Drop the optimistic user message; the server has persisted the real
      // messages and the parent effect will refetch them when runInFlight
      // flips back to false.
      setMessages((prev) => prev.filter((m) => m.id !== tempUserId));
      setStreamingText('');
      setStreamingCitations([]);
      setStreamingBadges([]);
      setRunInFlight(false);
      refreshThreads();
    }
  }, [input, runInFlight, selectedDocIds, threadId, setMessages, setActiveRunId, setRunEvents, onEnsureThread, refreshThreads, setRunInFlight]);

  const onKey = useCallback((e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      send();
    }
  }, [send]);

  const emptyState = !threadId && !messages.length && !runInFlight;

  return (
    <div className="flex-1 flex flex-col min-h-0">
      <div ref={scrollRef} className="flex-1 overflow-y-auto min-h-0" data-testid="chat-scroll">
        {emptyState ? (
          <EmptyState hasDocuments={documents.length > 0} />
        ) : messagesLoading ? (
          <div className="p-6 space-y-4">
            {[0, 1].map((i) => (
              <div key={i} className="h-24 bg-night-surfaceAlt animate-pulse" />
            ))}
          </div>
        ) : (
          <div className="max-w-3xl mx-auto px-6 py-8 space-y-8" data-testid="messages-list">
            {messages.map((m) => (
              <Message key={m.id} message={m} />
            ))}
            {runInFlight && (
              <StreamingBubble
                text={streamingText}
                citations={streamingCitations}
                badges={streamingBadges}
              />
            )}
          </div>
        )}
      </div>

      {/* Composer */}
      <div className="border-t border-night-border bg-night-bg">
        <div className="max-w-3xl mx-auto px-6 py-4">
          {selectedDocIds.length > 0 && (
            <div className="mb-2 flex items-center gap-2 flex-wrap" data-testid="scope-indicator">
              <span className="mono text-[10px] uppercase tracking-widest text-night-textMuted">scope</span>
              {selectedDocIds.map((id) => {
                const doc = documents.find((d) => d.id === id);
                if (!doc) return null;
                return (
                  <span
                    key={id}
                    className="mono text-[10px] uppercase tracking-widest text-signal-doc border border-signal-doc/40 bg-signal-doc/5 px-2 py-0.5"
                  >
                    {doc.filename}
                  </span>
                );
              })}
            </div>
          )}
          <div className="flex items-end gap-3 border border-night-border focus-within:border-night-text transition-colors">
            <textarea
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={onKey}
              rows={2}
              placeholder="Ask your research operator anything… (Shift+Enter for newline)"
              className="flex-1 bg-transparent px-4 py-3 text-night-text placeholder:text-night-textMuted outline-none resize-none max-h-48 min-h-[56px]"
              disabled={runInFlight}
              data-testid="chat-composer-input"
            />
            <button
              onClick={send}
              disabled={!input.trim() || runInFlight}
              className="m-1.5 self-end px-4 py-2 bg-night-text text-night-bg disabled:opacity-30 hover:opacity-90 transition-opacity"
              aria-label="Send"
              data-testid="chat-submit-button"
            >
              {runInFlight ? <Loader2 className="w-4 h-4 animate-spin" /> : <Send className="w-4 h-4" />}
            </button>
          </div>
          <div className="mt-2 mono text-[10px] uppercase tracking-widest text-night-textMuted flex items-center gap-3">
            <span>gpt-5.2</span>
            <span className="opacity-40">·</span>
            <span>{documents.filter((d) => d.status === 'ready').length} doc ready</span>
          </div>
        </div>
      </div>
    </div>
  );
}

function EmptyState({ hasDocuments }) {
  return (
    <div className="h-full flex flex-col items-center justify-center px-6 text-center max-w-2xl mx-auto" data-testid="chat-empty-state">
      <div className="mono text-[10px] uppercase tracking-widest text-night-textMuted mb-6">
        ready to research
      </div>
      <h1 className="font-serif text-5xl mb-4 tracking-tight">
        What would you like to know?
      </h1>
      <p className="text-night-textMuted mb-10 max-w-md">
        {hasDocuments
          ? 'Ask about your documents, current web signals, or academic literature. The agent decides which tools to use.'
          : 'Upload a PDF from the Documents tab, or ask about the web / arXiv literature directly.'}
      </p>

      <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 w-full">
        {[
          { icon: <FileText className="w-4 h-4 text-signal-doc" />, text: 'Summarize my uploaded document' },
          { icon: <BookOpen className="w-4 h-4 text-signal-paper" />, text: 'Find recent papers about agentic RAG' },
          { icon: <Globe className="w-4 h-4 text-signal-web" />, text: 'What is current news on MCP?' },
          { icon: <MessageSquare className="w-4 h-4 text-signal-ctx" />, text: 'Compare my doc with recent research' },
        ].map((s, i) => (
          <div
            key={i}
            className="border border-night-border p-4 text-left flex items-center gap-3 hover:border-night-textMuted transition-colors"
            data-testid={`suggestion-card-${i}`}
          >
            <span className="flex-shrink-0">{s.icon}</span>
            <span className="text-sm text-night-textMuted">{s.text}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

function Message({ message }) {
  if (message.role === 'user') {
    return (
      <div className="flex justify-end" data-testid={`message-user-${message.id}`}>
        <div className="max-w-[85%] px-4 py-3 bg-night-surface border border-night-border">
          <p className="whitespace-pre-wrap text-night-text">{message.content}</p>
        </div>
      </div>
    );
  }
  return <AssistantMessage message={message} />;
}

function AssistantMessage({ message }) {
  const badges = message.tool_badges || [];
  const citations = message.citations || [];
  return (
    <div className="animate-slideUp" data-testid={`message-assistant-${message.id}`}>
      {badges.length > 0 && (
        <div className="flex items-center gap-2 mb-3 flex-wrap">
          {badges.map((b) => (
            <SourceBadge key={b} kind={b} />
          ))}
        </div>
      )}
      <div className="answer-prose text-night-text leading-relaxed">
        {renderWithCitations(message.content, citations)}
      </div>
      {citations.length > 0 && (
        <CitationsList citations={citations} />
      )}
    </div>
  );
}

function StreamingBubble({ text, citations, badges }) {
  return (
    <div className="animate-slideUp" data-testid="streaming-bubble">
      {badges?.length > 0 && (
        <div className="flex items-center gap-2 mb-3 flex-wrap">
          {badges.map((b) => (
            <SourceBadge key={b} kind={b} />
          ))}
        </div>
      )}
      <div className="answer-prose text-night-text leading-relaxed">
        {text ? renderWithCitations(text, citations || []) : (
          <span className="mono text-xs uppercase tracking-widest text-night-textMuted">
            <span className="inline-block w-1.5 h-1.5 bg-signal-doc mr-2 animate-pulseDot" />
            thinking…
          </span>
        )}
        {text && <span className="terminal-caret text-night-text" />}
      </div>
    </div>
  );
}

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
  // Split text on [N] tokens, render each as a linkable pill.
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
            className="inline-flex items-center mx-0.5 mono text-[10px] px-1.5 py-0.5 border border-night-textMuted text-night-textMuted hover:text-night-text hover:border-night-text transition-colors align-baseline"
            data-testid={`citation-${idx + 1}`}
          >
            [{m[1]}]
          </a>
        );
      }
    }
    return <span key={i}>{part}</span>;
  });
}

function CitationsList({ citations }) {
  return (
    <div className="mt-6 pt-4 border-t border-night-border">
      <div className="mono text-[10px] uppercase tracking-widest text-night-textMuted mb-3">
        sources · {citations.length}
      </div>
      <ol className="space-y-2">
        {citations.map((c, i) => (
          <li key={c.id || i} className="flex items-start gap-3" data-testid={`source-item-${i + 1}`}>
            <span className="mono text-[10px] text-night-textMuted mt-0.5 tabular w-6 shrink-0">[{i + 1}]</span>
            <SourceBadge kind={c.source_type} />
            <div className="min-w-0 flex-1">
              <div className="text-sm truncate">
                {c.url ? (
                  <a
                    href={c.url}
                    target="_blank"
                    rel="noreferrer"
                    className="text-night-text hover:underline underline-offset-2 inline-flex items-center gap-1"
                  >
                    {c.title}
                    <ArrowUpRight className="w-3 h-3 opacity-60" />
                  </a>
                ) : (
                  c.title
                )}
              </div>
              {c.snippet && (
                <div className="mono text-[11px] text-night-textMuted line-clamp-2 mt-0.5">
                  {c.snippet}
                </div>
              )}
              {c.filename && c.page != null && (
                <div className="mono text-[10px] text-night-textMuted uppercase tracking-widest mt-1">
                  {c.filename} · page {c.page}
                </div>
              )}
              {c.authors?.length > 0 && (
                <div className="mono text-[10px] text-night-textMuted uppercase tracking-widest mt-1">
                  {c.authors.slice(0, 3).join(', ')}
                  {c.authors.length > 3 && ' et al.'}
                  {c.published && ` · ${c.published}`}
                </div>
              )}
            </div>
          </li>
        ))}
      </ol>
    </div>
  );
}
