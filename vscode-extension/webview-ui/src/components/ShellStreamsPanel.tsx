import { memo, useEffect, useRef } from 'react';
import { useApp, useAppDispatch, type ShellStreamState } from '@/state/store';
import { cancelShellStream } from '@/lib/actions';

/** Compact chip shown for one live/historical shell stream. */
function StreamChip({ stream }: { stream: ShellStreamState }) {
  const bodyRef = useRef<HTMLPreElement>(null);
  const dispatch = useAppDispatch();

  // Auto-scroll the tail into view whenever body changes — matches the AI
  // chat's "follow the log" discipline unless the user scrolled up.
  useEffect(() => {
    const el = bodyRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [stream.body]);

  const terminal = stream.status !== 'running';
  return (
    <section
      className={`shell-stream-chip shell-stream-${stream.status}`}
      data-stream-id={stream.streamId}
    >
      <header className="shell-stream-head">
        <span className="shell-stream-title">
          shell · {stream.lines} lines · {stream.status}
        </span>
        {terminal ? (
          <button
            type="button"
            className="shell-stream-dismiss"
            onClick={() => dispatch({ type: 'ui/dismissShellStream', streamId: stream.streamId })}
            title="Remove this finished stream"
            aria-label="Remove this finished stream"
          >
            ×
          </button>
        ) : (
          <button
            type="button"
            className="shell-stream-cancel"
            onClick={() => cancelShellStream(stream.streamId, stream.sessionId)}
            title="Cancel this stream"
          >
            Cancel
          </button>
        )}
      </header>
      <pre ref={bodyRef} className="shell-stream-body">
        {stream.body || '(waiting for output…)'}
        {stream.detail ? `\n${stream.detail}` : ''}
      </pre>
    </section>
  );
}

/** Live shell streams panel (rendered when shell_streams is non-empty). */
function ShellStreamsPanel() {
  const { shellStreams } = useApp();
  const streams = Object.values(shellStreams);
  if (streams.length === 0) return null;
  return (
    <div className="shell-streams-panel" role="region" aria-label="shell streams">
      {streams.map((stream) => (
        <StreamChip key={stream.streamId} stream={stream} />
      ))}
    </div>
  );
}

export default memo(ShellStreamsPanel);
