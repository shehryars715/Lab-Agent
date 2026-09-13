// Stroke icons on a shared 24x24 grid, inline rather than from a library.
// Eight paths do not justify a dependency, and inline SVG inherits
// `currentColor`, so they follow the theme for free.

const base = {
  viewBox: '0 0 24 24',
  fill: 'none',
  stroke: 'currentColor',
  strokeWidth: 1.9,
  strokeLinecap: 'round',
  strokeLinejoin: 'round',
  'aria-hidden': true,
}

export const Check = ({ size = 14, strokeWidth = 2.6 }) => (
  <svg {...base} width={size} height={size} strokeWidth={strokeWidth}>
    <path d="m4 12.5 5.2 5L20 6.5" />
  </svg>
)

export const Close = ({ size = 15 }) => (
  <svg {...base} width={size} height={size}>
    <path d="M6 6 18 18M18 6 6 18" />
  </svg>
)

export const ArrowUp = ({ size = 17 }) => (
  <svg {...base} width={size} height={size} strokeWidth={2.2}>
    <path d="M12 19V5" />
    <path d="m6 11 6-6 6 6" />
  </svg>
)

export const Paperclip = ({ size = 18 }) => (
  <svg {...base} width={size} height={size}>
    <path d="M20 11.5 12.4 19a4.6 4.6 0 0 1-6.5-6.5l8-8a3 3 0 0 1 4.3 4.3l-8 8a1.4 1.4 0 0 1-2-2l7.3-7.3" />
  </svg>
)

export const Download = ({ size = 16 }) => (
  <svg {...base} width={size} height={size}>
    <path d="M12 4v11" />
    <path d="m7.5 10.5 4.5 4.5 4.5-4.5" />
    <path d="M5 19h14" />
  </svg>
)

export const Doc = ({ size = 17 }) => (
  <svg {...base} width={size} height={size}>
    <path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z" />
    <path d="M14 3v5h5" />
    <path d="M9 13h6M9 17h4" />
  </svg>
)

export const Archive = ({ size = 17 }) => (
  <svg {...base} width={size} height={size}>
    <rect x="3" y="4" width="18" height="5" rx="1.4" />
    <path d="M5 9v9a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V9" />
    <path d="M10.5 13h3" />
  </svg>
)

export const CodeFile = ({ size = 17 }) => (
  <svg {...base} width={size} height={size}>
    <path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z" />
    <path d="M14 3v5h5" />
    <path d="m10.5 12.5-2 2.5 2 2.5M13.5 12.5l2 2.5-2 2.5" />
  </svg>
)
