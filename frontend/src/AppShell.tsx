import type { ReactNode } from "react";

export type WorkspaceView = "rankings" | "map" | "assistant" | "settings" | "login";

export function AppShell({ view, onNavigate, children, sidebar, email, onLogout, busy = false }: {
  view: WorkspaceView;
  onNavigate: (view: WorkspaceView) => void;
  children: ReactNode;
  sidebar?: ReactNode;
  email?: string;
  onLogout?: () => void;
  busy?: boolean;
}) {
  return (
    <div className="site-shell">
      <aside className="site-sidebar">
        <div className="site-brand"><span className="site-brand-mark" aria-hidden="true">a<span>i</span></span><div><h1>AI Workspace</h1><p>探索 · 比较 · 成长</p></div></div>
        <nav className="site-navigation" aria-label="工作区入口">
          <p className="navigation-label">探索</p>
          <button aria-pressed={view === "rankings"} disabled={busy} onClick={() => onNavigate("rankings")}><span aria-hidden="true">▤</span>PyPI 排行榜</button>
          <p className="navigation-label">个人空间</p>
          <button aria-pressed={view === "map"} disabled={busy} onClick={() => onNavigate("map")}><span aria-hidden="true">▦</span>学习地图</button>
          <button aria-pressed={view === "assistant"} disabled={busy} onClick={() => onNavigate("assistant")}><span aria-hidden="true">◇</span>助手</button>
        </nav>
        {sidebar}
        <div className="site-account">
          {email ? <><button className="secondary" aria-pressed={view === "settings"} disabled={busy} onClick={() => onNavigate("settings")}>账户与设置</button><p className="account-email">{email}</p><button className="text-button" onClick={onLogout}>退出登录</button></> : <><p className="muted small">公开数据，直接浏览。<br />登录后进入个人工作台。</p><button onClick={() => onNavigate("login")}>登录 / 注册</button></>}
        </div>
      </aside>
      <main className="site-main">{children}</main>
    </div>
  );
}
