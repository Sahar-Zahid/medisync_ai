import { useEffect } from 'react'
import { useLocation } from 'react-router-dom'

/**
 * Scrolls smoothly to the element whose id matches the current URL hash.
 * Used on the landing page so navbar/footer links that point at "/#section"
 * work both when already on "/" and when arriving from another route.
 */
function useScrollToHash() {
  const location = useLocation()

  useEffect(() => {
    if (location.hash) {
      const id = location.hash.replace('#', '')
      const scrollToTarget = () => {
        const el = document.getElementById(id)
        if (el) {
          el.scrollIntoView({ behavior: 'smooth', block: 'start' })
        }
      }
      // Wait a tick so the landing page has fully mounted/rendered,
      // e.g. when arriving fresh from /login or /signup.
      const timer = setTimeout(scrollToTarget, 0)
      return () => clearTimeout(timer)
    }

    window.scrollTo({ top: 0, behavior: 'smooth' })
  }, [location])
}

export default useScrollToHash
