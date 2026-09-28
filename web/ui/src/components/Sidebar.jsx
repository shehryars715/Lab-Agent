import { useEffect, useMemo, useRef, useState } from 'react'
import { groupByDate, searchLabs } from '../lib/labs'
import { href } from '../lib/router'
import { THEMES } from '../lib/prefs'
import StatusMark, { STATUS_WORDS } from './StatusMark'
import {
  Close,
  Info,
  Logo,
  Monitor,
  Moon,
  NewLab,
  PanelLeft,
  Pencil,
  Search,
  SignOut,
  Sun,
  Trash,
} from './Icons'

const THEME_META = {
  dark: { label: 'Dark', Icon: Moon },
  light: { label: 'Light', Icon: Sun },
  system: { label: 'System', Icon: Monitor },
}

const isMac = typeof navigator !== 'undefined' && /Mac|iP(hone|ad)/.test(navigator.platform)
export const MOD = isMac ? '⌘' : 'Ctrl'

/** The labs list, and the only navigation this app has.
 *
 *  Three shapes, one component: the full panel, a 60px icon rail when the
 *  panel is collapsed on desktop, and a drawer over a scrim on narrow screens.
 *  The rail and the panel are stacked in the same box and cross-fade, so the
 *  collapse is a transform and two opacities -- never an animated width.
 */
export default function Sidebar({
  items,
  loading,
  historyFailed,
  openKey,
  collapsed,
  onToggleCollapsed,
  narrow,
  drawerOpen,
  onCloseDrawer,
  theme,
  onTheme,
  onNewLab,
  onHide,
  onRename,
  searchRef,
  route,
  onSignOut = null,
}) {
  const [query, setQuery] = useState('')
  const filtered = useMemo(() => searchLabs(items, query), [items, query])
  const groups = useMemo(() => groupByDate(filtered), [filtered])
  const panelRef = useRef(null)
  const railMode = collapsed && !narrow
  // Only marks that say something: the lab on screen and anything still
  // running. Eight identical ticks would be navigation that tells you nothing.
  const railItems = items
    .filter((i) => i.key === openKey || i.status === 'working' || i.status === 'waiting')
    .slice(0, 5)

  // The drawer is a modal dialog while it is open: Escape closes it, focus
  // moves in, and Tab cycles inside it rather than wandering off behind the
  // scrim (the page behind is also made inert by App).
  useEffect(() => {
    if (!narrow || !drawerOpen) return undefined
    const onKey = (e) => {
      if (e.key === 'Escape') {
        onCloseDrawer()
        return
      }
      if (e.key !== 'Tab') return
      const items = Array.from(
        panelRef.current?.querySelectorAll('a[href], button:not([disabled]), input') ?? [],
      ).filter((el) => el.offsetParent !== null)
      if (!items.length) return
      const first = items[0]
      const last = items.at(-1)
      if (e.shiftKey && document.activeElement === first) {
        e.preventDefault()
        last.focus()
      } else if (!e.shiftKey && document.activeElement === last) {
        e.preventDefault()
        first.focus()
      }
    }
    window.addEventListener('keydown', onKey)
    const first = panelRef.current?.querySelector('button, a, input')
    first?.focus()
    return () => window.removeEventListener('keydown', onKey)
  }, [narrow, drawerOpen, onCloseDrawer])

  const nextTheme = THEMES[(THEMES.indexOf(theme) + 1) % THEMES.length]
  const ThemeIcon = THEME_META[theme]?.Icon ?? Moon

  return (
    <>
      {narrow && (
        <div
          className={`scrim ${drawerOpen ? 'is-open' : ''}`}
          onClick={onCloseDrawer}
          aria-hidden="true"
        />
      )}
      <aside
        className="sidebar"
        data-mode={narrow ? 'drawer' : railMode ? 'rail' : 'panel'}
        data-open={narrow ? String(drawerOpen) : undefined}
        aria-label="Labs"
        {...(narrow && drawerOpen ? { role: 'dialog', 'aria-modal': 'true' } : null)}
        {...(narrow && !drawerOpen ? { inert: true } : null)}
      >
        {/* ---------------------------------------------------- icon rail */}
        <div className="rail" aria-hidden={!railMode} {...(!railMode ? { inert: true } : null)}>
          <button
            type="button"
            className="rail-btn rail-logo"
            onClick={onToggleCollapsed}
            aria-label="Expand sidebar"
            title="Expand sidebar"
          >
            <Logo size={24} />
            <span className="rail-logo-hover" aria-hidden="true">
              <PanelLeft size={18} />
            </span>
          </button>
          <button
            type="button"
            className="rail-btn"
            onClick={onNewLab}
            aria-label={`New lab (${MOD}+K)`}
            title={`New lab  ${MOD}+K`}
          >
            <NewLab />
          </button>
          <button
            type="button"
            className="rail-btn"
            onClick={() => {
              onToggleCollapsed()
              window.setTimeout(() => searchRef.current?.focus(), 60)
            }}
            aria-label="Search labs"
            title="Search labs  /"
          >
            <Search size={18} />
          </button>
          {/* The lab you are on and anything still running stay one tap away
              while collapsed; the full history is one expand away. */}
          {railItems.length > 0 && (
            <ul className="rail-labs" aria-label="Open and running labs">
              {railItems.map((item) => (
                <li key={item.key}>
                  <a
                    className="rail-btn"
                    href={href.lab(item.key)}
                    aria-current={item.key === openKey ? 'page' : undefined}
                    aria-label={`${item.title}, ${STATUS_WORDS[item.status] ?? item.status}`}
                    title={item.title}
                  >
                    <StatusMark status={item.status} />
                  </a>
                </li>
              ))}
            </ul>
          )}
          <div className="rail-spacer" />
          <a
            className="rail-btn"
            href={href.about}
            aria-label="About"
            title="About"
            aria-current={route.name === 'about' ? 'page' : undefined}
          >
            <Info />
          </a>
          <button
            type="button"
            className="rail-btn"
            onClick={() => onTheme(nextTheme)}
            aria-label={`Theme: ${THEME_META[theme].label}. Switch to ${THEME_META[nextTheme].label}`}
            title={`Theme: ${THEME_META[theme].label}`}
          >
            <ThemeIcon size={18} />
          </button>
          {onSignOut && (
            <button type="button" className="rail-btn" onClick={onSignOut} aria-label="Sign out" title="Sign out">
              <SignOut />
            </button>
          )}
        </div>

        {/* ---------------------------------------------------- full panel */}
        <div
          className="panel"
          ref={panelRef}
          aria-hidden={railMode}
          {...(railMode ? { inert: true } : null)}
        >
          <div className="panel-head">
            <a className="wordmark" href={href.home} aria-label="Labs-Agent, home">
              <Logo size={22} />
              <span>Labs-Agent</span>
            </a>
            {narrow ? (
              <button
                type="button"
                className="icon-btn"
                onClick={onCloseDrawer}
                aria-label="Close sidebar"
              >
                <Close size={18} />
              </button>
            ) : (
              <button
                type="button"
                className="icon-btn"
                onClick={onToggleCollapsed}
                aria-label="Collapse sidebar"
                title="Collapse sidebar"
              >
                <PanelLeft />
              </button>
            )}
          </div>

          <button type="button" className="new-lab" onClick={onNewLab}>
            <NewLab size={17} />
            <span>New lab</span>
            <kbd aria-hidden="true">{MOD} K</kbd>
          </button>

          <label className="search">
            <Search />
            <input
              ref={searchRef}
              type="search"
              aria-label="Search labs"
              value={query}
              placeholder="Search labs"
              onChange={(e) => setQuery(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Escape') {
                  setQuery('')
                  e.currentTarget.blur()
                }
              }}
            />
            {!query && <kbd aria-hidden="true">/</kbd>}
          </label>

          <nav className="labs" aria-label="Your labs">
            {loading && <ListSkeleton />}

            {!loading && items.length === 0 && (
              <div className="labs-empty">
                <p className="labs-empty-title">No labs yet</p>
                <p>Your labs land here as you start them, grouped by day.</p>
              </div>
            )}

            {!loading && items.length > 0 && filtered.length === 0 && (
              <div className="labs-empty">
                <p className="labs-empty-title">Nothing matches “{query.trim()}”</p>
                <p>Try a lab number or a word from the title.</p>
              </div>
            )}

            {groups.map((group) => (
              <section className="labs-group" key={group.label}>
                <h2 className="labs-group-title">{group.label}</h2>
                <ul>
                  {group.items.map((item, i) => (
                    <LabItem
                      key={item.key}
                      item={item}
                      index={i}
                      active={item.key === openKey}
                      onHide={onHide}
                      onRename={onRename}
                    />
                  ))}
                </ul>
              </section>
            ))}

            {historyFailed && (
              <p className="labs-note">
                Couldn’t reach the server for older runs. The labs above are from this browser.
              </p>
            )}
          </nav>

          <div className="panel-foot">
            <a
              className="foot-link"
              href={href.about}
              aria-current={route.name === 'about' ? 'page' : undefined}
            >
              <Info size={17} />
              <span>About</span>
            </a>
            {onSignOut && (
              <button type="button" className="foot-link foot-icon" onClick={onSignOut} aria-label="Sign out" title="Sign out">
                <SignOut size={17} />
              </button>
            )}
            <div className="theme-switch" role="radiogroup" aria-label="Theme">
              {THEMES.map((t) => {
                const { Icon, label } = THEME_META[t]
                return (
                  <button
                    key={t}
                    type="button"
                    role="radio"
                    aria-checked={theme === t}
                    className="theme-opt"
                    onClick={() => onTheme(t)}
                    title={label}
                  >
                    <Icon size={15} />
                    <span className="sr-only">{label}</span>
                  </button>
                )
              })}
            </div>
          </div>
        </div>
      </aside>
    </>
  )
}

function LabItem({ item, index, active, onHide, onRename }) {
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState(item.title)
  const inputRef = useRef(null)

  useEffect(() => {
    if (editing) {
      inputRef.current?.focus()
      inputRef.current?.select()
    }
  }, [editing])

  function commit() {
    const clean = draft.trim()
    if (clean && clean !== item.title) onRename(item.key, clean)
    setEditing(false)
  }

  const statusWord = STATUS_WORDS[item.status] ?? item.status

  return (
    <li
      className={`lab-item ${active ? 'is-active' : ''} ${editing ? 'is-editing' : ''}`}
      style={{ '--i': Math.min(index, 12) }}
    >
      {editing ? (
        <div className="lab-rename">
          <StatusMark status={item.status} />
          <input
            ref={inputRef}
            value={draft}
            maxLength={120}
            aria-label="Rename lab"
            onChange={(e) => setDraft(e.target.value)}
            onBlur={commit}
            onKeyDown={(e) => {
              if (e.key === 'Enter') commit()
              if (e.key === 'Escape') {
                setDraft(item.title)
                setEditing(false)
              }
            }}
          />
        </div>
      ) : (
        <>
          <a
            className="lab-link"
            href={href.lab(item.key)}
            aria-current={active ? 'page' : undefined}
            title={item.title}
          >
            <StatusMark status={item.status} />
            <span className="lab-title">{item.title}</span>
            <span className="sr-only">, {statusWord}</span>
          </a>
          <span className="lab-actions">
            <button
              type="button"
              className="lab-action"
              onClick={() => {
                setDraft(item.title)
                setEditing(true)
              }}
              aria-label={`Rename ${item.title}`}
              title="Rename"
            >
              <Pencil />
            </button>
            <button
              type="button"
              className="lab-action"
              onClick={() => onHide(item.key, item.title)}
              aria-label={`Remove ${item.title} from this list`}
              title="Remove from list"
            >
              <Trash />
            </button>
          </span>
        </>
      )}
    </li>
  )
}

function ListSkeleton() {
  return (
    <div className="labs-skeleton" aria-hidden="true">
      <span className="skel skel-label" />
      {[72, 88, 60, 80, 66].map((w, i) => (
        <span key={i} className="skel skel-row" style={{ width: `${w}%` }} />
      ))}
    </div>
  )
}
