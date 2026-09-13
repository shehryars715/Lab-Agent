import { Download } from './Icons'

/** One downloadable artifact, as a link.
 *
 *  Extracted from ResultView when the history view needed the same row. Two
 *  copies of a component this small is not a crisis, but two copies that drift
 *  is: the hover behaviour, the arrow, and the deliberate absence of a
 *  `download` attribute all have reasons, and a second copy would quietly lose
 *  them.
 *
 *  No `download` attribute on the anchor, deliberately. The server sets
 *  `Content-Disposition` with the filename, and letting the browser follow the
 *  header means the name is decided in one place instead of two.
 */
export default function DownloadRow({ href, icon, name, sub, primary = false, delay = 0 }) {
  return (
    <a
      className={`${primary ? 'dl-primary' : 'dl-secondary'} stagger`}
      style={{ animationDelay: `${delay}ms` }}
      href={href}
    >
      <span className="dl-icon">{icon}</span>
      <span style={{ minWidth: 0, flex: 1 }}>
        <span className="dl-name" style={{ display: 'block' }}>
          {name}
        </span>
        <span className="dl-sub" style={{ display: 'block' }}>
          {sub}
        </span>
      </span>
      <span className="dl-arrow">
        <Download />
      </span>
    </a>
  )
}
