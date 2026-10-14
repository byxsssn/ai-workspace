const prefix = "ai-workspace:";

// 使用 sessionStorage 支持刷新恢复，避免将 JWT 长期保存在 localStorage。
// 浏览器存储被禁用时，当前页面仍可使用内存中的登录状态。
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
    /* 浏览器设置可能禁止使用存储。 */
  }
}

export function clearSession(): void {
  try {
    Object.keys(sessionStorage)
      .filter((key) => key.startsWith(prefix))
      .forEach((key) => sessionStorage.removeItem(key));
  } catch {
    /* App 也会清除内存中的登录状态。 */
  }
}
