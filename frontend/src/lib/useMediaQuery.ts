/* Generic matchMedia hook. The sidebar's useIsMobile (lib/a11y/useIsMobile.ts)
 * is pinned to the sidebar's 767px cutover, so a component whose responsive
 * breakpoint belongs to its own stylesheet uses this with its own query —
 * keep each call site's query in sync with the stylesheet that owns the same
 * cutover, exactly as useIsMobile does with Sidebar.module.css. */
import { useEffect, useState } from 'react';

export function useMediaQuery(query: string): boolean {
  const [matches, setMatches] = useState(
    () => typeof window !== 'undefined' && window.matchMedia(query).matches,
  );

  useEffect(() => {
    const mql = window.matchMedia(query);
    const onChange = () => setMatches(mql.matches);
    onChange();
    mql.addEventListener('change', onChange);
    return () => mql.removeEventListener('change', onChange);
  }, [query]);

  return matches;
}
