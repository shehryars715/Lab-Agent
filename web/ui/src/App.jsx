import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { createRun, loadIdentity } from './api'
import { Menu, NewLab } from './components/Icons'
import Sidebar from './components/Sidebar'
import { useBackgroundStatus, useHistory, useLabStore } from './hooks/useLabs'
import {
  createLab,
  filesFor,
  getLab,
  hideLab,
  mergeLabs,
  provisionalTitle,
  rememberFiles,
  renameLab,
  unhideLab,
  updateLab,
} from './lib/labs'
import { composeInstructions } from './lib/formats'
import { useCollapsed, useNarrow, useTheme } from './lib/prefs'
import { go, href, useRoute } from './lib/router'
import About from './pages/About'
import Home from './pages/Home'
import Lab from './pages/Lab'

/** A network failure has no `detail` to show -- say what it most likely is. */
function friendly(error) {
  const msg = error?.message ?? String(error)
  if (/Failed to fetch|NetworkError|Load failed/i.test(msg)) {
    return 'I can’t reach the server. Is it running? (uv run python -m uvicorn web.server.app:app)'
  }
  return msg
}

/** `onSignOut` is null unless the server requires a sign-in (see AuthGate). */
export default function App({ onSignOut = null }) {
  const route = useRoute()
  const [theme, setTheme] = useTheme()
  const [collapsed, toggleCollapsed] = useCollapsed()
  const narrow = useNarrow()
  const [drawerOpen, setDrawerOpen] = useState(false)
  const [toast, setToast] = useState(null)
  const [announcement, setAnnouncement] = useState('')
  const [prefill, setPrefill] = useState(null)
  const searchRef = useRef(null)
  const composerRef = useRef(null)
  const menuRef = useRef(null)

  const store = useLabStore()
  const history = useHistory()
  const items = useMemo(() => mergeLabs(store, history.runs ?? []), [store, history.runs])
  const openKey = route.name === 'lab' ? route.id : null

  useBackgroundStatus(store, openKey, history.refresh)

  // A lab that just reached disk should show up in history-backed places.
  const settledCount = store.order.filter((id) => store.labs[id]?.runId).length
  useEffect(() => {
    if (settledCount) history.refresh()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [settledCount])

  // FLIP for the sidebar collapse. The main column's margin changes in one
  // step (animating a margin would re-lay-out every frame), so the centred
  // content would jump sideways by half the difference. Instead it is drawn
  // where it WAS and slid to where it IS -- a transform, in step with the
  // sidebar's own slide.
  const hostRef = useRef(null)
  const lastCollapsed = useRef(collapsed)
  useLayoutEffect(() => {
    if (lastCollapsed.current === collapsed) return
    lastCollapsed.current = collapsed
    const el = hostRef.current
    if (!el || narrow || window.matchMedia?.('(prefers-reduced-motion: reduce)').matches) return
    const css = getComputedStyle(document.documentElement)
    const shift = (parseFloat(css.getPropertyValue('--sidebar-w')) - parseFloat(css.getPropertyValue('--rail-w'))) / 2
    el.animate([{ transform: `translateX(${collapsed ? shift : -shift}px)` }, { transform: 'none' }], {
      duration: 380,
      easing: 'cubic-bezier(0.25, 1, 0.5, 1)',
    })
  }, [collapsed, narrow])

  // Close the drawer whenever the route changes: tapping a lab is navigation.
  useEffect(() => {
    setDrawerOpen(false)
  }, [route.name, openKey])

  const closeDrawer = useCallback(() => {
    setDrawerOpen(false)
    // After the render that lifts `inert` from the page -- focusing an inert
    // element is silently ignored, which would drop focus on the floor.
    window.requestAnimationFrame(() => menuRef.current?.focus())
  }, [])

  const announce = useCallback((text) => {
    // Clear then set, so the same sentence twice is still announced twice.
    setAnnouncement('')
    window.setTimeout(() => setAnnouncement(text), 40)
  }, [])

  const showToast = useCallback((text, action) => {
    setToast({ text, action, id: Date.now() })
  }, [])

  useEffect(() => {
    if (!toast) return undefined
    const t = window.setTimeout(() => setToast(null), toast.action ? 7000 : 5000)
    return () => window.clearTimeout(t)
  }, [toast])

  const newLab = useCallback(() => {
    setPrefill(null)
    go(href.home)
    setDrawerOpen(false)
    window.setTimeout(() => composerRef.current?.focus(), 30)
  }, [])

  // ---------------------------------------------------------- shortcuts
  useEffect(() => {
    const onKey = (e) => {
      const mod = e.metaKey || e.ctrlKey
      const typing = /^(INPUT|TEXTAREA|SELECT)$/.test(e.target.tagName) || e.target.isContentEditable
      if (mod && e.key.toLowerCase() === 'k') {
        e.preventDefault()
        newLab()
      } else if (mod && e.key.toLowerCase() === 'b' && !narrow) {
        e.preventDefault()
        toggleCollapsed()
      } else if (e.key === '/' && !typing && !mod) {
        e.preventDefault()
        if (narrow) setDrawerOpen(true)
        else if (collapsed) toggleCollapsed()
        window.setTimeout(() => searchRef.current?.focus(), narrow || collapsed ? 80 : 0)
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [newLab, narrow, collapsed, toggleCollapsed])

  // ---------------------------------------------------------- actions

  /** Start a lab. Returns an error string for the composer, or null. */
  const start = useCallback(async ({ text, formats, file, data }, reuseId = null) => {
    const instructions = composeInstructions(text, formats)
    let jobId
    try {
      ;({ job_id: jobId } = await createRun({ file, data, instructions, seed: loadIdentity() }))
    } catch (e) {
      return friendly(e)
    }
    const fields = {
      message: text,
      formats,
      fileName: file?.name ?? null,
      dataNames: (data ?? []).map((d) => d.name),
    }
    let id = reuseId
    if (id) {
      updateLab(id, {
        ...fields,
        jobId,
        runId: null,
        status: 'working',
        error: null,
        passed: null,
        total: null,
        credits: null,
        finishedAt: null,
        revisions: [],
        createdAt: Date.now(),
      })
    } else {
      id = createLab({ ...fields, title: provisionalTitle(fields), extra: { jobId } })
    }
    rememberFiles(id, { file, data })
    setPrefill(null)
    go(href.lab(id))
    return null
  }, [])

  const editLab = useCallback((id) => {
    const lab = getLab(id)
    if (!lab) return
    const kept = filesFor(id)
    setPrefill({
      nonce: Date.now(),
      text: lab.message,
      formats: lab.formats,
      file: kept?.file ?? null,
      data: kept?.data ?? [],
    })
    go(href.home)
    if (lab.fileName && !kept?.file) {
      showToast(`Attach ${lab.fileName} again — browsers don’t keep files across a reload.`)
    }
  }, [showToast])

  const retryLab = useCallback(
    async (id) => {
      const lab = getLab(id)
      if (!lab) return
      const kept = filesFor(id)
      if ((lab.fileName && !kept?.file) || (lab.dataNames?.length && !kept?.data?.length)) {
        editLab(id)
        return
      }
      const failure = await start(
        { text: lab.message, formats: lab.formats, file: kept?.file ?? null, data: kept?.data ?? [] },
        id,
      )
      if (failure) showToast(failure)
    },
    [start, editLab, showToast],
  )

  const hide = useCallback(
    (key, title) => {
      hideLab(key)
      if (openKey === key) go(href.home)
      showToast(`Removed “${title}” from your list. Its files stay in runs/.`, {
        label: 'Undo',
        run: () => unhideLab(key),
      })
    },
    [openKey, showToast],
  )

  // ---------------------------------------------------------- render

  let page
  let pageTitle = 'New lab'
  if (route.name === 'about') {
    page = <About />
    pageTitle = 'About'
  } else if (route.name === 'lab') {
    page = (
      <Lab
        id={route.id}
        store={store}
        announce={announce}
        onRetry={retryLab}
        onEdit={editLab}
        onRemove={hide}
      />
    )
    pageTitle = items.find((i) => i.key === route.id)?.title ?? 'Lab'
  } else {
    page = (
      <Home
        onStart={start}
        composerRef={composerRef}
        prefill={prefill}
        firstTime={history.runs !== null && items.length === 0 && store.order.length === 0}
      />
    )
  }

  useEffect(() => {
    document.title = route.name === 'home' ? 'Labs-Agent' : `${pageTitle} — Labs-Agent`
  }, [route.name, pageTitle])

  return (
    <div className="shell" data-sidebar={narrow ? 'drawer' : collapsed ? 'rail' : 'panel'}>
      <a className="skip-link" href="#main">
        Skip to content
      </a>

      <Sidebar
        items={items}
        loading={history.runs === null && store.order.length === 0}
        historyFailed={history.failed}
        openKey={openKey}
        collapsed={collapsed}
        onToggleCollapsed={toggleCollapsed}
        narrow={narrow}
        drawerOpen={drawerOpen}
        onCloseDrawer={closeDrawer}
        theme={theme}
        onTheme={setTheme}
        onNewLab={newLab}
        onHide={hide}
        onRename={renameLab}
        searchRef={searchRef}
        route={route}
        onSignOut={onSignOut}
      />

      <div className="main" {...(narrow && drawerOpen ? { inert: true } : null)}>
        {narrow && (
          <header className="topbar">
            <button
              ref={menuRef}
              type="button"
              className="icon-btn"
              onClick={() => setDrawerOpen(true)}
              aria-label="Open sidebar"
              aria-expanded={drawerOpen}
            >
              <Menu />
            </button>
            <span className="topbar-title">{pageTitle}</span>
            <button type="button" className="icon-btn" onClick={newLab} aria-label="New lab">
              <NewLab />
            </button>
          </header>
        )}
        <main ref={hostRef} id="main" className="page-host" key={route.name === 'lab' ? `lab:${route.id}` : route.name}>
          {page}
        </main>
      </div>

      <div className="sr-only" aria-live="polite" aria-atomic="true">
        {announcement}
      </div>

      {toast && (
        <div className="toast" role="status" key={toast.id}>
          <span>{toast.text}</span>
          {toast.action && (
            <button
              type="button"
              className="toast-action"
              onClick={() => {
                toast.action.run()
                setToast(null)
              }}
            >
              {toast.action.label}
            </button>
          )}
        </div>
      )}
    </div>
  )
}
