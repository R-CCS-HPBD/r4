import { useId } from 'react';

export type MascotState = 'idle' | 'thinking' | 'done' | 'error';

/**
 * Aurora mascot — the small bot face shared with the companion TUI identity
 * (◕ᴗ◕). Gradient comes from packages/r4-design/tokens.json (Aurora).
 * Animations live in index.css and respect prefers-reduced-motion.
 */
export default function Mascot({
  state = 'idle',
  size = 22,
}: {
  state?: MascotState;
  size?: number;
}) {
  const uid = useId().replace(/[^a-zA-Z0-9]/g, '');
  const gradientId = `r4m-grad-${uid}`;

  return (
    <svg
      className={`r4-mascot r4-mascot-${state}`}
      width={size}
      height={size}
      viewBox="0 0 24 24"
      role="img"
      aria-label={`R4 agent ${state}`}
    >
      <defs>
        <linearGradient
          id={gradientId}
          x1="0"
          y1="0"
          x2="24"
          y2="24"
          gradientUnits="userSpaceOnUse"
        >
          <stop offset="0" stopColor="#7C6CFF" />
          <stop offset="1" stopColor="#2DD4EE" />
        </linearGradient>
      </defs>
      <rect x="1.5" y="1.5" width="21" height="21" rx="6.5" fill={`url(#${gradientId})`} />
      {state === 'error' ? (
        <g
          className="r4-mascot-eyes"
          stroke="#fff"
          strokeWidth="1.8"
          strokeLinecap="round"
          fill="none"
        >
          <path d="M7.5 9.5l3 3m0-3l-3 3" />
          <path d="M13.5 9.5l3 3m0-3l-3 3" />
        </g>
      ) : state === 'done' ? (
        <g
          className="r4-mascot-eyes"
          stroke="#fff"
          strokeWidth="1.8"
          strokeLinecap="round"
          fill="none"
        >
          <path d="M7.5 12.5q1.5-2.2 3 0" />
          <path d="M13.5 12.5q1.5-2.2 3 0" />
        </g>
      ) : (
        <g className="r4-mascot-eyes" fill="#fff">
          <circle cx="9" cy="11" r="1.6" />
          <circle cx="15" cy="11" r="1.6" />
        </g>
      )}
      <path
        d={state === 'error' ? 'M8.5 17.5q3.5-3 7 0' : 'M8.5 15q3.5 2.8 7 0'}
        stroke="#fff"
        strokeWidth="1.6"
        strokeLinecap="round"
        fill="none"
      />
    </svg>
  );
}
