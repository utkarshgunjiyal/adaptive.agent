import { Link } from 'react-router-dom';
import { ArrowRight, FileText, Globe, BookOpen, Terminal } from 'lucide-react';

export default function LandingPage() {
  return (
    <div className="min-h-screen auth-hero relative overflow-hidden">
      <div className="grain absolute inset-0 z-0" />

      {/* Top nav */}
      <header className="relative z-10 border-b border-night-border">
        <div className="max-w-7xl mx-auto px-8 py-5 flex items-center justify-between">
          <Link to="/" className="flex items-center gap-3" data-testid="brand-home">
            <BrandMark />
            <span className="font-serif text-2xl tracking-tight">Runner.ai</span>
          </Link>
          <div className="flex items-center gap-6">
            <Link
              to="/login"
              className="text-sm text-night-textMuted hover:text-night-text transition-colors"
              data-testid="nav-login"
            >
              Sign in
            </Link>
            <Link
              to="/register"
              className="text-sm px-4 py-2 bg-night-text text-night-bg font-medium hover:opacity-90 transition-opacity"
              data-testid="nav-register"
            >
              Get started
            </Link>
          </div>
        </div>
      </header>

      {/* Hero */}
      <main className="relative z-10 max-w-7xl mx-auto px-8 py-24 lg:py-32 grid lg:grid-cols-12 gap-16 items-start">
        <div className="lg:col-span-7">
          <div className="mono text-xs text-night-textMuted mb-8 tracking-widest uppercase">
            [ private · grounded · transparent ]
          </div>
          <h1 className="font-serif text-6xl lg:text-7xl leading-[1.05] tracking-tight mb-8">
            Your private
            <br />
            research
            <br />
            <span className="italic text-night-textMuted">operator.</span>
          </h1>
          <p className="text-lg text-night-textMuted mb-10 max-w-xl leading-relaxed">
            Upload your PDFs, ask real questions, and watch an autonomous
            agent decide which tools to use — your documents, the web, or
            academic papers — with every citation traceable.
          </p>
          <div className="flex flex-wrap items-center gap-4">
            <Link
              to="/register"
              className="group inline-flex items-center gap-2 px-6 py-3 bg-night-text text-night-bg font-medium hover:opacity-90 transition-opacity"
              data-testid="hero-cta-register"
            >
              Start a workspace
              <ArrowRight className="w-4 h-4 group-hover:translate-x-0.5 transition-transform" />
            </Link>
            <Link
              to="/login"
              className="text-sm text-night-text underline underline-offset-4 decoration-night-border hover:decoration-night-text transition-colors"
              data-testid="hero-cta-login"
            >
              I already have an account
            </Link>
          </div>
        </div>

        <div className="lg:col-span-5 lg:sticky lg:top-24">
          <div className="relative border border-night-border bg-night-surface p-6">
            <div className="absolute top-0 left-0 w-full h-full grain opacity-40 pointer-events-none" />
            <div className="relative">
              <div className="flex items-center justify-between mb-4 pb-4 border-b border-night-border">
                <div className="flex items-center gap-2">
                  <Terminal className="w-4 h-4 text-night-textMuted" />
                  <span className="mono text-xs text-night-textMuted tracking-wider uppercase">
                    execution
                  </span>
                </div>
                <span className="mono text-[10px] text-signal-doc uppercase tracking-widest">
                  ● live
                </span>
              </div>
              <MockExecutionTrace />
            </div>
          </div>
        </div>
      </main>

      {/* Sources of truth */}
      <section className="relative z-10 border-t border-night-border">
        <div className="max-w-7xl mx-auto px-8 py-16">
          <div className="mono text-xs text-night-textMuted mb-10 tracking-widest uppercase">
            four sources of truth
          </div>
          <div className="grid md:grid-cols-4 gap-px bg-night-border">
            <SourceCard
              color="text-signal-doc"
              label="Your document"
              icon={<FileText className="w-5 h-5" />}
              text="Filename · Page N"
              detail="Semantic retrieval from your uploaded PDFs. Every claim links back to a page number."
            />
            <SourceCard
              color="text-signal-paper"
              label="Research paper"
              icon={<BookOpen className="w-5 h-5" />}
              text="arXiv · authors · date"
              detail="Peer-reviewed and pre-print academic literature. Compare your work with the state of the art."
            />
            <SourceCard
              color="text-signal-web"
              label="Web source"
              icon={<Globe className="w-5 h-5" />}
              text="title · url · retrieved"
              detail="Current public web via Tavily. Fresh signals when your documents can't answer alone."
            />
            <SourceCard
              color="text-signal-ctx"
              label="Conversation"
              icon={<Terminal className="w-5 h-5" />}
              text="thread history"
              detail="Prior turns, retrieved as short-term memory so the agent stays coherent across a chat."
            />
          </div>
        </div>
      </section>

      <footer className="relative z-10 border-t border-night-border">
        <div className="max-w-7xl mx-auto px-8 py-8 flex items-center justify-between text-xs mono text-night-textMuted uppercase tracking-widest">
          <span>© Runner.ai — {new Date().getFullYear()}</span>
          <span>gpt-5.2 · tavily · arxiv</span>
        </div>
      </footer>
    </div>
  );
}

function BrandMark() {
  return (
    <div className="w-8 h-8 border border-night-text flex items-center justify-center relative">
      <span className="mono text-sm tracking-tighter">R</span>
      <span className="absolute -bottom-1 -right-1 w-2 h-2 bg-signal-doc" />
    </div>
  );
}

function SourceCard({ color, label, icon, text, detail }) {
  return (
    <div className="bg-night-bg p-6">
      <div className={`${color} mb-4`}>{icon}</div>
      <div className="mono text-[10px] uppercase tracking-widest text-night-textMuted mb-2">
        {text}
      </div>
      <h3 className="font-serif text-2xl mb-3">{label}</h3>
      <p className="text-sm text-night-textMuted leading-relaxed">{detail}</p>
    </div>
  );
}

function MockExecutionTrace() {
  return (
    <div className="space-y-3 mono text-xs">
      <TraceLine dot="text-signal-ctx" text="capabilities_selected: doc_search, paper_search" />
      <TraceLine dot="text-signal-doc" text="tool: search_document_chunks" latency="128ms" />
      <TraceLine dot="text-signal-paper" text="tool: paper_search (arxiv)" latency="612ms" />
      <TraceLine dot="text-signal-doc" text="evidence: 4 chunks · 2 papers" />
      <div className="pt-3 mt-3 border-t border-night-border">
        <div className="text-night-textMuted mb-1">synthesize →</div>
        <p className="text-night-text leading-relaxed font-sans">
          Your document proposes a two-stage retrieval pipeline [1][2]. Recent
          research on agentic RAG suggests a validator step improves
          groundedness [3]
          <span className="terminal-caret" />
        </p>
      </div>
    </div>
  );
}

function TraceLine({ dot, text, latency }) {
  return (
    <div className="flex items-center gap-3">
      <span className={`${dot}`}>▸</span>
      <span className="text-night-textMuted flex-1 truncate">{text}</span>
      {latency && (
        <span className="text-night-textMuted opacity-60 tabular">{latency}</span>
      )}
    </div>
  );
}
