import { useCallback, useEffect, useState } from 'react'

// Per-browser conveniences: the theme and whether the sidebar is collapsed.
// Every storage access is guarded -- a private window can throw on read, and
// not remembering a preference is never a reason for the page to fail.

export function readPref(key, fallback) {
  try {
    const value = window.localStorage.getItem(key)
    return value ?? fallback
  } catch {
    return fallback
  }
}

export function writePref(key, value) {
  try {
    window.localStorage.setItem(key, value)
  } catch {
    /* forgetting a preference is fine */
  }
}

const THEME_KEY = 'labsagent.theme'
export const THEMES = ['dark', 'light', 'system']

function resolve(pref) {
  if (pref === 'system') {
    return window.matchMedia?.('(prefers-color-scheme: light)').matches ? 'light' : 'dark'
  }
  return pref === 'light' ? 'light' : 'dark'
}

function apply(pref, animate) {
  const root = document.documentElement
  const next = resolve(pref)
  if (root.dataset.theme === next) return
  // One beat of colour cross-fade, then the class goes so hovers stay snappy.
  if (animate && !window.matchMedia?.('(prefers-reduced-motion: reduce)').matches) {
    root.classList.add('theme-shift')
    window.setTimeout(() => root.classList.remove('theme-shift'), 320)
  }
  root.dataset.theme = next
}

/** Dark unless told otherwise: see the scene note in styles/tokens.css. */
export function useTheme() {
  const [pref, setPref] = useState(() => {
    const stored = readPref(THEME_KEY, 'dark')
    return THEMES.includes(stored) ? stored : 'dark'
  })

  useEffect(() => {
    apply(pref, true)
    writePref(THEME_KEY, pref)
    if (pref !== 'system') return undefined
    const mq = window.matchMedia?.('(prefers-color-scheme: light)')
    const on = () => apply('system', true)
    mq?.addEventListener?.('change', on)
    return () => mq?.removeEventListener?.('change', on)
  }, [pref])

  return [pref, setPref]
}

const RAIL_KEY = 'labsagent.sidebar'

export function useCollapsed() {
  const [collapsed, setCollapsed] = useState(() => readPref(RAIL_KEY, 'open') === 'rail')
  const toggle = useCallback(() => {
    setCollapsed((c) => {
      writePref(RAIL_KEY, c ? 'open' : 'rail')
      return !c
    })
  }, [])
  return [collapsed, toggle]
}

/** True below the drawer breakpoint. Mirrors the 900px media query in shell.css. */
export function useNarrow() {
  const query = '(max-width: 899px)'
  const [narrow, setNarrow] = useState(() => window.matchMedia?.(query).matches ?? false)
  useEffect(() => {
    const mq = window.matchMedia?.(query)
    if (!mq) return undefined
    const on = () => setNarrow(mq.matches)
    mq.addEventListener?.('change', on)
    return () => mq.removeEventListener?.('change', on)
  }, [])
  return narrow
}
