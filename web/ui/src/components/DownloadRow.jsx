import { Download } from './Icons'

/** One downloadable artifact, as a link.
 *
 *  Shared by the live result and the history view. Two copies of a component
 *  this small is not a crisis, but two copies that drift is: the hover
 *  behaviour, the arrow and the deliberate absence of a `download` attribute
 *  all have reasons, and a second copy would quietly lose them.
 *
 *  No `download` attribute on the anchor, deliberately. The server sets
 *  `Content-Disposition` with the filename, and letting the browser follow the
 *  header means the name is decided in one place instead of two.
 */
export default function DownloadRow({ href, icon, name, sub, primary = false }) {
  return (
    <a className="dl" data-primary={primary || undefined} href={href}>
      <span className="dl-icon" aria-hidden="true">
        {icon}
      </span>
      <span className="dl-text">
        <span className="dl-name">{name}</span>
        <span className="dl-sub">{sub}</span>
      </span>
      <span className="dl-go" aria-hidden="true">
        <Download />
      </span>
    </a>
  )
}
