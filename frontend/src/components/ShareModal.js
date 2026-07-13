import { useCallback, useEffect, useState } from 'react';
import { toast } from 'sonner';
import { X, Link2, Copy, Check, Trash2 } from 'lucide-react';
import { disableSharing, enableSharing } from '../api';

/**
 * Modal shown when the user clicks the Share button on a thread. Toggles
 * a public read-only link. The URL is a client-side route (/share/:token)
 * that fetches from the public /api/share/:token endpoint.
 */
export default function ShareModal({ threadId, onClose }) {
  const [loading, setLoading] = useState(true);
  const [shareUrl, setShareUrl] = useState('');
  const [copied, setCopied] = useState(false);

  const enable = useCallback(async () => {
    setLoading(true);
    try {
      const res = await enableSharing(threadId);
      const origin = typeof window !== 'undefined' ? window.location.origin : '';
      setShareUrl(`${origin}${res.url_suffix}`);
    } catch (err) {
      toast.error(err?.response?.data?.detail || 'Could not enable sharing');
    } finally {
      setLoading(false);
    }
  }, [threadId]);

  useEffect(() => { enable(); }, [enable]);

  const copyLink = async () => {
    if (!shareUrl) return;
    try {
      await navigator.clipboard.writeText(shareUrl);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch (_) {
      toast.error('Copy failed');
    }
  };

  const revoke = async () => {
    try {
      await disableSharing(threadId);
      toast.success('Link revoked');
      onClose?.();
    } catch (_) {
      toast.error('Revoke failed');
    }
  };

  return (
    <div
      className="fixed inset-0 z-50 bg-black/60 flex items-center justify-center p-4"
      onClick={onClose}
      data-testid="share-modal-backdrop"
    >
      <div
        className="bg-night-surface border border-night-border w-full max-w-md p-6 relative"
        onClick={(e) => e.stopPropagation()}
        data-testid="share-modal"
      >
        <button
          onClick={onClose}
          className="absolute top-3 right-3 p-1 text-night-textMuted hover:text-night-text"
          aria-label="Close"
          data-testid="share-modal-close"
        >
          <X className="w-4 h-4" />
        </button>

        <div className="mono text-[10px] uppercase tracking-widest text-night-textMuted mb-2 flex items-center gap-2">
          <Link2 className="w-3 h-3" />
          share this thread
        </div>
        <h2 className="font-serif text-2xl mb-2">Public read-only link</h2>
        <p className="text-sm text-night-textMuted mb-5 leading-relaxed">
          Anyone with the link can view this conversation and its citations.
          No account required. You can revoke the link at any time — the URL
          stops resolving immediately.
        </p>

        {loading ? (
          <div className="h-11 bg-night-surfaceAlt animate-pulse" />
        ) : (
          <div className="flex items-center gap-2">
            <input
              readOnly
              value={shareUrl}
              className="flex-1 bg-night-bg border border-night-border px-3 py-2 mono text-[11px] text-night-text outline-none"
              onFocus={(e) => e.target.select()}
              data-testid="share-modal-url"
            />
            <button
              onClick={copyLink}
              className="p-2 border border-night-border text-night-text hover:border-night-text"
              aria-label="Copy link"
              data-testid="share-modal-copy"
            >
              {copied ? <Check className="w-4 h-4 text-signal-doc" /> : <Copy className="w-4 h-4" />}
            </button>
          </div>
        )}

        <div className="mt-6 flex items-center justify-between">
          <button
            onClick={revoke}
            className="inline-flex items-center gap-2 text-sm text-signal-web hover:underline underline-offset-2"
            data-testid="share-modal-revoke"
          >
            <Trash2 className="w-3 h-3" />
            Revoke link
          </button>
          <button
            onClick={onClose}
            className="px-4 py-2 bg-night-text text-night-bg text-sm"
            data-testid="share-modal-done"
          >
            Done
          </button>
        </div>
      </div>
    </div>
  );
}
