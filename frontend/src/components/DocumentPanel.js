import { useCallback, useEffect, useRef, useState } from 'react';
import { toast } from 'sonner';
import { Upload, RefreshCw, FileText, Loader2, X, Check } from 'lucide-react';
import { listDocuments, retryDocument, uploadDocumentsBulk } from '../api';
import { formatBytes, formatDistanceToNow, statusColor } from '../lib/timeAgo';

export default function DocumentPanel({ documents, onDocumentsChange, selectedIds, onSelectedChange }) {
  const [loading, setLoading] = useState(true);
  const [uploading, setUploading] = useState(false);
  const [dragOver, setDragOver] = useState(false);
  const inputRef = useRef(null);

  const refresh = useCallback(async () => {
    try {
      const docs = await listDocuments();
      onDocumentsChange(docs);
      setLoading(false);
      return docs;
    } catch (_) {
      setLoading(false);
      return [];
    }
  }, [onDocumentsChange]);

  // Poll while any doc is still processing/queued.
  useEffect(() => {
    refresh();
    const id = setInterval(() => {
      if (documents?.some((d) => d.status === 'processing' || d.status === 'queued')) {
        refresh();
      }
    }, 3000);
    return () => clearInterval(id);
  }, [refresh, documents]);

  const upload = useCallback(async (fileList) => {
    const files = Array.from(fileList || []).filter(Boolean);
    if (!files.length) return;

    // Client-side quick filter for very obvious issues; the server does the
    // authoritative check.
    const oversize = files.filter((f) => f.size > 25 * 1024 * 1024);
    if (oversize.length) {
      toast.error(`${oversize.length} file(s) exceed the 25 MB limit and were skipped.`);
    }
    const ok = files.filter((f) => f.size <= 25 * 1024 * 1024);
    if (!ok.length) return;

    setUploading(true);
    try {
      const res = await uploadDocumentsBulk(ok);
      const accepted = (res?.accepted || []).length;
      const rejected = (res?.rejected || []).length;
      if (accepted) toast.success(`Queued ${accepted} document(s) for ingestion`);
      if (rejected) {
        toast.error(
          `${rejected} file(s) rejected: ${(res.rejected || []).map((r) => `${r.filename} — ${r.reason}`).join(' · ')}`,
        );
      }
      await refresh();
    } catch (err) {
      const detail = err?.response?.data?.detail;
      toast.error(typeof detail === 'string' ? detail : 'Upload failed');
    } finally {
      setUploading(false);
    }
  }, [refresh]);

  const onDrop = useCallback((e) => {
    e.preventDefault();
    setDragOver(false);
    const files = e.dataTransfer?.files;
    if (files?.length) upload(files);
  }, [upload]);

  const onRetry = useCallback(async (id) => {
    try {
      await retryDocument(id);
      toast.success('Retry queued');
      await refresh();
    } catch (_) {
      toast.error('Retry failed');
    }
  }, [refresh]);

  const toggleSelect = useCallback((id) => {
    if (selectedIds.includes(id)) onSelectedChange(selectedIds.filter((x) => x !== id));
    else onSelectedChange([...selectedIds, id]);
  }, [selectedIds, onSelectedChange]);

  return (
    <div className="p-3 space-y-3">
      <div
        onDragOver={(e) => { e.preventDefault(); setDragOver(true); }}
        onDragLeave={() => setDragOver(false)}
        onDrop={onDrop}
        onClick={() => inputRef.current?.click()}
        className={`border border-dashed p-6 text-center cursor-pointer transition-colors ${dragOver ? 'border-night-text bg-night-surfaceAlt' : 'border-night-border hover:border-night-textMuted'}`}
        data-testid="upload-dropzone"
      >
        <input
          ref={inputRef}
          type="file"
          accept="application/pdf"
          multiple
          onChange={(e) => upload(e.target.files)}
          className="hidden"
          data-testid="upload-file-input"
        />
        <div className="flex flex-col items-center gap-2">
          {uploading ? (
            <Loader2 className="w-5 h-5 animate-spin text-night-text" />
          ) : (
            <Upload className="w-5 h-5 text-night-textMuted" />
          )}
          <div className="mono text-[10px] uppercase tracking-widest text-night-textMuted">
            {uploading ? 'uploading…' : 'drop pdfs here or click'}
          </div>
          <div className="text-[11px] text-night-textMuted opacity-60">Max 25 MB · PDF only · multi-file OK</div>
        </div>
      </div>

      {selectedIds.length > 0 && (
        <div className="text-[11px] mono text-signal-doc border border-signal-doc/40 bg-signal-doc/5 px-3 py-2 flex items-center justify-between" data-testid="selected-docs-info">
          <span>SCOPE · {selectedIds.length} doc selected</span>
          <button
            onClick={() => onSelectedChange([])}
            className="text-night-textMuted hover:text-night-text"
            data-testid="clear-doc-selection"
          >
            <X className="w-3 h-3" />
          </button>
        </div>
      )}

      {loading ? (
        <div className="space-y-2" data-testid="documents-loading">
          {[0, 1].map((i) => (
            <div key={i} className="h-16 bg-night-surfaceAlt animate-pulse" />
          ))}
        </div>
      ) : documents.length === 0 ? (
        <div className="text-center py-6" data-testid="documents-empty">
          <FileText className="w-6 h-6 mx-auto mb-2 text-night-textMuted opacity-40" />
          <div className="text-xs text-night-textMuted">
            No documents yet. Upload a PDF to get started.
          </div>
        </div>
      ) : (
        <ul className="space-y-2" data-testid="documents-list">
          {documents.map((d) => (
            <DocumentCard
              key={d.id}
              doc={d}
              selected={selectedIds.includes(d.id)}
              onToggle={() => toggleSelect(d.id)}
              onRetry={() => onRetry(d.id)}
            />
          ))}
        </ul>
      )}
    </div>
  );
}

function DocumentCard({ doc, selected, onToggle, onRetry }) {
  const ready = doc.status === 'ready';
  const active = doc.status === 'processing' || doc.status === 'queued';
  return (
    <li
      className={`border p-3 transition-colors ${selected ? 'border-signal-doc bg-signal-doc/5' : 'border-night-border hover:border-night-textMuted'}`}
      data-testid={`document-card-${doc.id}`}
    >
      <div className="flex items-start gap-2">
        <button
          onClick={onToggle}
          disabled={!ready}
          className={`mt-0.5 w-4 h-4 flex items-center justify-center border ${selected ? 'bg-signal-doc border-signal-doc text-night-bg' : 'border-night-border'} ${!ready && 'opacity-30'}`}
          aria-label={selected ? 'Deselect' : 'Select'}
          data-testid={`document-select-${doc.id}`}
        >
          {selected && <Check className="w-3 h-3" />}
        </button>
        <div className="flex-1 min-w-0">
          <div className="text-sm truncate" title={doc.filename}>{doc.filename}</div>
          <div className="mono text-[10px] text-night-textMuted uppercase tracking-widest mt-1 flex items-center gap-1.5">
            <span>{formatBytes(doc.size_bytes)}</span>
            {doc.page_count != null && <><span className="opacity-40">·</span><span>{doc.page_count}p</span></>}
            <span className="opacity-40">·</span>
            <span>{formatDistanceToNow(doc.created_at)}</span>
          </div>
          <div className="mt-2 flex items-center gap-2 flex-wrap">
            <span
              className={`mono text-[10px] uppercase tracking-widest border px-2 py-0.5 ${statusColor(doc.status)}`}
              data-testid={`document-status-${doc.id}`}
            >
              {active && <span className="inline-block w-1 h-1 bg-current mr-1 animate-pulseDot" />}
              {doc.status}
            </span>
            {doc.status === 'failed' && (
              <button
                onClick={onRetry}
                className="mono text-[10px] uppercase tracking-widest text-night-textMuted hover:text-night-text underline underline-offset-2"
                data-testid={`document-retry-${doc.id}`}
              >
                <RefreshCw className="w-3 h-3 inline mr-1" />
                retry
              </button>
            )}
          </div>
          {doc.error && (
            <div className="text-[11px] text-signal-web mt-2 line-clamp-2" data-testid={`document-error-${doc.id}`}>
              {doc.error}
            </div>
          )}
          {ready && doc.summary && (
            <details className="mt-2 group">
              <summary className="mono text-[10px] uppercase tracking-widest text-night-textMuted cursor-pointer hover:text-night-text list-none flex items-center gap-1">
                <span>summary</span>
                <span className="opacity-40 group-open:rotate-90 transition-transform">▸</span>
              </summary>
              <p className="text-[12px] text-night-textMuted mt-1.5 leading-relaxed">
                {doc.summary}
              </p>
            </details>
          )}
        </div>
      </div>
    </li>
  );
}
