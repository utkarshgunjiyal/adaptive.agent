import { useCallback, useEffect, useMemo, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { toast } from 'sonner';
import { LogOut, MessageSquarePlus, ChevronRight, Terminal, Share2 } from 'lucide-react';
import { useAuth } from '../AuthContext';
import { createThread, listDocuments, listMessages, listThreads } from '../api';
import ThreadSidebar from '../components/ThreadSidebar';
import DocumentPanel from '../components/DocumentPanel';
import ChatArea from '../components/ChatArea';
import ExecutionDrawer from '../components/ExecutionDrawer';
import DigestPanel from '../components/DigestPanel';
import ShareModal from '../components/ShareModal';

export default function WorkspacePage() {
  const { user, logout } = useAuth();
  const navigate = useNavigate();
  const { threadId } = useParams();

  const [threads, setThreads] = useState([]);
  const [threadsLoading, setThreadsLoading] = useState(true);
  const [messages, setMessages] = useState([]);
  const [messagesLoading, setMessagesLoading] = useState(false);
  const [activeRunId, setActiveRunId] = useState(null);
  const [runEvents, setRunEvents] = useState([]); // For execution drawer
  const [drawerOpen, setDrawerOpen] = useState(true);
  const [selectedDocIds, setSelectedDocIds] = useState([]);
  const [documents, setDocuments] = useState([]);
  const [runInFlight, setRunInFlight] = useState(false);
  const [leftPanel, setLeftPanel] = useState('threads'); // 'threads' | 'docs' | 'digests'
  const [shareOpen, setShareOpen] = useState(false);

  const refreshThreads = useCallback(async () => {
    try {
      const list = await listThreads();
      setThreads(list);
      setThreadsLoading(false);
      return list;
    } catch (_) {
      setThreadsLoading(false);
      return [];
    }
  }, []);

  useEffect(() => {
    refreshThreads();
  }, [refreshThreads]);

  // Fetch documents once on workspace mount so the composer footer + doc
  // scope UX work before the user visits the Documents tab.
  useEffect(() => {
    (async () => {
      try {
        const docs = await listDocuments();
        setDocuments(docs);
      } catch (_) {
        /* ignored — DocumentPanel will refetch on mount */
      }
    })();
  }, []);

  useEffect(() => {
    if (!threadId) {
      setMessages([]);
      setActiveRunId(null);
      setRunEvents([]);
      return;
    }
    // If a run is currently in flight (e.g. we just navigated mid-stream from
    // /app to /app/<newThreadId>), don't refetch — that would duplicate the
    // optimistic user message with the server-persisted one.
    if (runInFlight) return;

    let cancelled = false;
    (async () => {
      setMessagesLoading(true);
      try {
        const list = await listMessages(threadId);
        if (!cancelled) setMessages(list);
      } catch (err) {
        toast.error('Failed to load messages');
      } finally {
        if (!cancelled) setMessagesLoading(false);
      }
    })();
    return () => { cancelled = true; };
  }, [threadId, runInFlight]);

  const handleNewThread = useCallback(async () => {
    try {
      const t = await createThread(null);
      await refreshThreads();
      navigate(`/app/${t.id}`);
      setSelectedDocIds([]);
    } catch (_) {
      toast.error('Could not create thread');
    }
  }, [navigate, refreshThreads]);

  const handleLogout = useCallback(() => {
    logout();
    navigate('/login');
  }, [logout, navigate]);

  const activeThread = useMemo(
    () => threads.find((t) => t.id === threadId) || null,
    [threads, threadId]
  );

  return (
    <div className="h-screen w-screen overflow-hidden bg-night-bg text-night-text flex" data-testid="workspace-root">
      {/* Left rail — brand + panel switch */}
      <aside className="w-64 min-w-64 border-r border-night-border bg-night-surface flex flex-col">
        <div className="p-4 border-b border-night-border flex items-center gap-3">
          <div className="w-8 h-8 border border-night-text flex items-center justify-center relative">
            <span className="mono text-sm tracking-tighter">R</span>
            <span className="absolute -bottom-1 -right-1 w-2 h-2 bg-signal-doc" />
          </div>
          <div>
            <div className="font-serif text-lg leading-none">Runner.ai</div>
            <div className="mono text-[10px] text-night-textMuted tracking-widest uppercase mt-1">
              private workspace
            </div>
          </div>
        </div>

        <div className="p-3 border-b border-night-border">
          <button
            onClick={handleNewThread}
            className="w-full flex items-center justify-center gap-2 py-2 border border-night-text text-night-text hover:bg-night-text hover:text-night-bg transition-colors"
            data-testid="new-thread-button"
          >
            <MessageSquarePlus className="w-4 h-4" />
            <span className="text-sm">New conversation</span>
          </button>
        </div>

        {/* Tabs */}
        <div className="flex border-b border-night-border">
          <TabButton
            active={leftPanel === 'threads'}
            onClick={() => setLeftPanel('threads')}
            testid="tab-threads"
          >
            Threads
          </TabButton>
          <TabButton
            active={leftPanel === 'docs'}
            onClick={() => setLeftPanel('docs')}
            testid="tab-documents"
          >
            Documents
          </TabButton>
          <TabButton
            active={leftPanel === 'digests'}
            onClick={() => setLeftPanel('digests')}
            testid="tab-digests"
          >
            Digests
          </TabButton>
        </div>

        <div className="flex-1 overflow-y-auto min-h-0">
          {leftPanel === 'threads' && (
            <ThreadSidebar
              threads={threads}
              loading={threadsLoading}
              activeThreadId={threadId}
              onSelect={(id) => navigate(`/app/${id}`)}
            />
          )}
          {leftPanel === 'docs' && (
            <DocumentPanel
              documents={documents}
              onDocumentsChange={setDocuments}
              selectedIds={selectedDocIds}
              onSelectedChange={setSelectedDocIds}
            />
          )}
          {leftPanel === 'digests' && (
            <DigestPanel onOpenThread={(id) => navigate(`/app/${id}`)} />
          )}
        </div>

        {/* User footer */}
        <div className="border-t border-night-border p-3 flex items-center gap-3">
          <div className="w-8 h-8 flex items-center justify-center bg-night-surfaceAlt text-night-text text-sm mono uppercase" data-testid="user-avatar">
            {(user?.name || user?.email || '?').charAt(0)}
          </div>
          <div className="min-w-0 flex-1">
            <div className="truncate text-sm" data-testid="user-name">{user?.name || 'Researcher'}</div>
            <div className="truncate mono text-[10px] text-night-textMuted" data-testid="user-email">{user?.email}</div>
          </div>
          <button
            onClick={handleLogout}
            className="p-2 text-night-textMuted hover:text-night-text hover:bg-night-surfaceAlt transition-colors"
            aria-label="Sign out"
            data-testid="logout-button"
          >
            <LogOut className="w-4 h-4" />
          </button>
        </div>
      </aside>

      {/* Middle — chat */}
      <main className="flex-1 min-w-0 flex flex-col">
        <header className="border-b border-night-border px-6 py-3 flex items-center justify-between gap-4 bg-night-bg/80 backdrop-blur-md">
          <div className="min-w-0 flex-1">
            <div className="mono text-[10px] uppercase tracking-widest text-night-textMuted">
              conversation
            </div>
            <h1 className="font-serif text-xl truncate" data-testid="active-thread-title">
              {activeThread?.title || (threadId ? 'Loading…' : 'Start a new conversation')}
            </h1>
          </div>
          <div className="flex items-center gap-2">
            {threadId && (
              <button
                onClick={() => setShareOpen(true)}
                className="inline-flex items-center gap-2 px-3 py-2 border border-night-border text-night-textMuted hover:text-night-text hover:border-night-text transition-colors"
                data-testid="share-thread-button"
                title="Share this thread"
              >
                <Share2 className="w-4 h-4" />
                <span className="mono text-[11px] uppercase tracking-widest">
                  share
                </span>
              </button>
            )}
            <button
              onClick={() => setDrawerOpen((v) => !v)}
              className={`inline-flex items-center gap-2 px-3 py-2 border transition-colors ${drawerOpen ? 'border-night-text text-night-text' : 'border-night-border text-night-textMuted hover:text-night-text'}`}
              data-testid="toggle-execution-drawer"
            >
              <Terminal className="w-4 h-4" />
              <span className="mono text-[11px] uppercase tracking-widest">
                execution
              </span>
              <ChevronRight className={`w-3 h-3 transition-transform ${drawerOpen ? 'rotate-180' : ''}`} />
            </button>
          </div>
        </header>

        <ChatArea
          threadId={threadId || null}
          messages={messages}
          setMessages={setMessages}
          messagesLoading={messagesLoading}
          selectedDocIds={selectedDocIds}
          documents={documents}
          onDocumentsChange={setDocuments}
          setActiveRunId={setActiveRunId}
          setRunEvents={setRunEvents}
          refreshThreads={refreshThreads}
          onEnsureThread={(newId) => navigate(`/app/${newId}`)}
          runInFlight={runInFlight}
          setRunInFlight={setRunInFlight}
        />
      </main>

      {/* Right — execution drawer */}
      {drawerOpen && (
        <aside className="w-96 min-w-96 border-l border-night-border bg-night-surface flex flex-col" data-testid="execution-drawer">
          <ExecutionDrawer
            runId={activeRunId}
            events={runEvents}
            inFlight={runInFlight}
          />
        </aside>
      )}

      {shareOpen && threadId && (
        <ShareModal threadId={threadId} onClose={() => setShareOpen(false)} />
      )}
    </div>
  );
}

function TabButton({ active, onClick, children, testid }) {
  return (
    <button
      onClick={onClick}
      data-testid={testid}
      className={`flex-1 py-3 mono text-[11px] uppercase tracking-widest transition-colors ${active ? 'text-night-text border-b border-night-text -mb-px' : 'text-night-textMuted hover:text-night-text'}`}
    >
      {children}
    </button>
  );
}
