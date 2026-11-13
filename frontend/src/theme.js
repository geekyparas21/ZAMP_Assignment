// Theme: 'light' | 'dark' | 'system'.
// The chosen mode is persisted; 'system' follows the OS and keeps following it
// if the OS flips while the page is open.

const KEY = 'invoice-pipeline-theme'

export function storedMode() {
  try {
    const value = localStorage.getItem(KEY)
    return value === 'light' || value === 'dark' || value === 'system' ? value : 'system'
  } catch {
    return 'system'
  }
}

export function prefersDark() {
  return window.matchMedia?.('(prefers-color-scheme: dark)').matches ?? false
}

export function resolve(mode) {
  return mode === 'system' ? (prefersDark() ? 'dark' : 'light') : mode
}

export function applyMode(mode) {
  document.documentElement.setAttribute('data-theme', resolve(mode))
  try {
    localStorage.setItem(KEY, mode)
  } catch {
    /* private browsing; the theme simply will not persist */
  }
}
