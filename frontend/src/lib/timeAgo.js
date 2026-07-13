export function formatDistanceToNow(iso) {
  if (!iso) return '';
  const t = typeof iso === 'string' ? new Date(iso).getTime() : new Date(iso).getTime();
  const diff = Date.now() - t;
  if (Number.isNaN(diff)) return '';
  const sec = Math.round(diff / 1000);
  if (sec < 5) return 'just now';
  if (sec < 60) return `${sec}s ago`;
  const min = Math.round(sec / 60);
  if (min < 60) return `${min}m ago`;
  const hr = Math.round(min / 60);
  if (hr < 24) return `${hr}h ago`;
  const d = Math.round(hr / 24);
  if (d < 7) return `${d}d ago`;
  const w = Math.round(d / 7);
  if (w < 5) return `${w}w ago`;
  const mo = Math.round(d / 30);
  if (mo < 12) return `${mo}mo ago`;
  const y = Math.round(d / 365);
  return `${y}y ago`;
}

export function formatBytes(n) {
  if (!Number.isFinite(n) || n <= 0) return '0 B';
  const units = ['B', 'KB', 'MB', 'GB'];
  let i = 0;
  let v = n;
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024;
    i += 1;
  }
  return `${v.toFixed(v >= 100 || i === 0 ? 0 : 1)} ${units[i]}`;
}

export function statusColor(status) {
  switch (status) {
    case 'ready':
      return 'text-signal-doc border-signal-doc/40 bg-signal-doc/5';
    case 'processing':
      return 'text-blue-400 border-blue-400/40 bg-blue-400/5';
    case 'queued':
      return 'text-amber-400 border-amber-400/40 bg-amber-400/5';
    case 'failed':
      return 'text-signal-web border-signal-web/40 bg-signal-web/5';
    default:
      return 'text-night-textMuted border-night-border bg-night-surfaceAlt';
  }
}
