// Stroke icons on a shared 24x24 grid, drawn inline rather than pulled from a
// library: twenty-odd paths do not justify a dependency, and inline SVG
// inherits `currentColor`, so every icon follows the theme for free. One stroke
// weight for the whole set, so no icon looks borrowed from another family.

const base = {
  viewBox: '0 0 24 24',
  fill: 'none',
  stroke: 'currentColor',
  strokeWidth: 1.8,
  strokeLinecap: 'round',
  strokeLinejoin: 'round',
  'aria-hidden': true,
  focusable: false,
}

const icon = (paths, defaultSize = 18) =>
  function Icon({ size = defaultSize, strokeWidth, className }) {
    return (
      <svg
        {...base}
        width={size}
        height={size}
        className={className}
        {...(strokeWidth ? { strokeWidth } : null)}
      >
        {paths}
      </svg>
    )
  }

export const Check = icon(<path d="m4.5 12.5 4.8 4.8L19.5 7" />, 14)
export const Close = icon(<path d="M6.5 6.5 17.5 17.5M17.5 6.5 6.5 17.5" />, 15)
export const ArrowUp = icon(
  <>
    <path d="M12 19V5.5" />
    <path d="m6.5 11 5.5-5.5 5.5 5.5" />
  </>,
  17,
)
export const ArrowLeft = icon(
  <>
    <path d="M19 12H5.5" />
    <path d="m11 6.5-5.5 5.5 5.5 5.5" />
  </>,
)
export const Paperclip = icon(
  <path d="M20 11.5 12.4 19a4.6 4.6 0 0 1-6.5-6.5l8-8a3 3 0 0 1 4.3 4.3l-8 8a1.4 1.4 0 0 1-2-2l7.3-7.3" />,
)
export const Download = icon(
  <>
    <path d="M12 4v11" />
    <path d="m7.5 10.5 4.5 4.5 4.5-4.5" />
    <path d="M5 19.5h14" />
  </>,
  16,
)
export const Doc = icon(
  <>
    <path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z" />
    <path d="M14 3v5h5" />
    <path d="M9 13h6M9 17h4" />
  </>,
)
export const Archive = icon(
  <>
    <rect x="3" y="4" width="18" height="5" rx="1.4" />
    <path d="M5 9v9a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V9" />
    <path d="M10.5 13h3" />
  </>,
)
export const Notebook = icon(
  <>
    <rect x="5" y="3" width="14" height="18" rx="2" />
    <path d="M5 8h14M5 14h14" />
    <path d="M9 3v18" />
  </>,
)
export const CodeFile = icon(
  <>
    <path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z" />
    <path d="M14 3v5h5" />
    <path d="m10.5 12.5-2 2.5 2 2.5M13.5 12.5l2 2.5-2 2.5" />
  </>,
)
export const Data = icon(
  <>
    <ellipse cx="12" cy="6" rx="7" ry="2.8" />
    <path d="M5 6v6c0 1.5 3.1 2.8 7 2.8s7-1.3 7-2.8V6" />
    <path d="M5 12v6c0 1.5 3.1 2.8 7 2.8s7-1.3 7-2.8v-6" />
  </>,
  16,
)
export const NewLab = icon(
  <>
    <path d="M11 4.5H6.5a2 2 0 0 0-2 2v11a2 2 0 0 0 2 2h11a2 2 0 0 0 2-2V13" />
    <path d="M17.6 3.9a1.9 1.9 0 0 1 2.7 2.7L13 13.9l-3.4.8.8-3.4z" />
  </>,
)
export const Search = icon(
  <>
    <circle cx="11" cy="11" r="6.5" />
    <path d="m20 20-4.2-4.2" />
  </>,
  16,
)
export const PanelLeft = icon(
  <>
    <rect x="3.5" y="4.5" width="17" height="15" rx="2.5" />
    <path d="M9.5 4.5v15" />
  </>,
)
export const Menu = icon(<path d="M4.5 7h15M4.5 12h15M4.5 17h9" />, 20)
export const Pencil = icon(
  <>
    <path d="M15.2 5.3a2.1 2.1 0 0 1 3 3L8.4 18.1 4.5 19.5l1.4-3.9z" />
    <path d="m13.5 7 3.5 3.5" />
  </>,
  15,
)
export const Trash = icon(
  <>
    <path d="M4.5 7h15" />
    <path d="M9.5 7V5.2c0-.7.5-1.2 1.2-1.2h2.6c.7 0 1.2.5 1.2 1.2V7" />
    <path d="M6.5 7l.8 11.3A2 2 0 0 0 9.3 20h5.4a2 2 0 0 0 2-1.7L17.5 7" />
  </>,
  15,
)
export const Info = icon(
  <>
    <circle cx="12" cy="12" r="8.5" />
    <path d="M12 11v5.5" />
    <path d="M12 7.6v.1" strokeWidth="2.4" />
  </>,
)
export const Sun = icon(
  <>
    <circle cx="12" cy="12" r="3.8" />
    <path d="M12 2.8v2M12 19.2v2M4.6 4.6 6 6M18 18l1.4 1.4M2.8 12h2M19.2 12h2M4.6 19.4 6 18M18 6l1.4-1.4" />
  </>,
  16,
)
export const Moon = icon(<path d="M19.5 14.6A7.8 7.8 0 0 1 9.4 4.5 7.8 7.8 0 1 0 19.5 14.6z" />, 16)
export const Monitor = icon(
  <>
    <rect x="3.5" y="4.5" width="17" height="11.5" rx="2" />
    <path d="M9 20h6M12 16v4" />
  </>,
  16,
)
export const Eye = icon(
  <>
    <path d="M2.8 12S6.3 5.8 12 5.8 21.2 12 21.2 12 17.7 18.2 12 18.2 2.8 12 2.8 12z" />
    <circle cx="12" cy="12" r="2.8" />
  </>,
  16,
)
export const Chevron = icon(<path d="m9 6 6 6-6 6" />, 14)
export const Retry = icon(
  <>
    <path d="M4.5 12a7.5 7.5 0 0 1 12.9-5.2L19.5 9" />
    <path d="M19.5 4.5V9H15" />
    <path d="M19.5 12a7.5 7.5 0 0 1-12.9 5.2L4.5 15" />
    <path d="M4.5 19.5V15H9" />
  </>,
  16,
)
export const Folder = icon(
  <path d="M3.5 7.5a2 2 0 0 1 2-2h3.8l2 2.2h7.2a2 2 0 0 1 2 2v7.8a2 2 0 0 1-2 2h-13a2 2 0 0 1-2-2z" />,
  16,
)
export const Keyboard = icon(
  <>
    <rect x="2.5" y="6" width="19" height="12" rx="2.2" />
    <path d="M6.5 10h.01M10 10h.01M14 10h.01M17.5 10h.01M7.5 14h9" strokeWidth="2" />
  </>,
  16,
)

/** The mark: a disc with an L and a quarter-round cut out of it. One colour,
 *  taken from the text around it, so it follows the theme. Same geometry as
 *  public/favicon.svg and logos/export/icon.svg; keep them in step. */
export const Logo = ({ size = 22 }) => (
  <svg viewBox="80 112 288 288" width={size} height={size} aria-hidden="true" focusable="false">
    <path
      fill="currentColor"
      fillRule="evenodd"
      d="M224 128a128 128 0 1 0 0 256a128 128 0 1 0 0-256Z M168 184h36v100h76v36h-112Z M232 248v-48a48 48 0 0 1 48 48Z"
    />
  </svg>
)
