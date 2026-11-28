// The app has one other page besides the advisor, so a router would be more
// than it needs: App reads the path once. Cloudflare Pages serves index.html
// for every path that has no file (no top-level 404.html = SPA mode), and so
// do `vite` and `vite preview`, so /privatnost needs no _redirects file.

/** The "Privatnost" page. The start-screen footer link opens it. */
export const PRIVACY_PATH = '/privatnost'

/** True for /privatnost, with or without a trailing slash. */
export function isPrivacyPath(pathname: string): boolean {
  return pathname.replace(/\/+$/, '') === PRIVACY_PATH
}
