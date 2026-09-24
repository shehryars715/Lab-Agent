import { useEffect, useState } from 'react'

// A hash router, because three routes do not justify a dependency and a hash
// needs no server cooperation: FastAPI serves index.html at "/" and never sees
// "#/lab/abc", so a refresh or a pasted link lands on the right screen with
// nothing added to the backend.
//
//   #/            home -- the composer
//   #/lab/<id>    one lab: live, or read back from disk
//   #/about       what this is

export function parse(hash) {
  const h = String(hash || '').replace(/^#/, '')
  const lab = h.match(/^\/lab\/([^/?#]+)/)
  if (lab) {
    try {
      return { name: 'lab', id: decodeURIComponent(lab[1]) }
    } catch {
      return { name: 'home' }
    }
  }
  if (h.startsWith('/about')) return { name: 'about' }
  return { name: 'home' }
}

export const href = {
  home: '#/',
  about: '#/about',
  lab: (id) => `#/lab/${encodeURIComponent(id)}`,
}

export function go(target) {
  if (window.location.hash !== target) window.location.hash = target
}

export function useRoute() {
  const [route, setRoute] = useState(() => parse(window.location.hash))
  useEffect(() => {
    const on = () => setRoute(parse(window.location.hash))
    window.addEventListener('hashchange', on)
    return () => window.removeEventListener('hashchange', on)
  }, [])
  return route
}
