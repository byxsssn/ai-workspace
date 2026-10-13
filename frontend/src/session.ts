const prefix = "ai-workspace:";

// sessionStorage supports refresh without putting JWTs into long-lived localStorage.
// If storage is disabled, authentication still works in memory for this page.
export function readSession(key: string): string {
  try {
    return sessionStorage.getItem(prefix + key) ?? "";
  } catch {
    return "";
  }
}

export function writeSession(key: string, value: string): void {
  try {
    if (value) sessionStorage.setItem(prefix + key, value);
    else sessionStorage.removeItem(prefix + key);
  } catch {
    /* Storage can be blocked by browser settings. */
  }
}

export function clearSession(): void {
  try {
    Object.keys(sessionStorage)
      .filter((key) => key.startsWith(prefix))
      .forEach((key) => sessionStorage.removeItem(key));
  } catch {
    /* The in-memory session is also cleared by App. */
  }
}
