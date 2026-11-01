import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { createApi, errorMessage } from "./api";
import { AuthForm } from "./AuthForm";
import { AppShell } from "./AppShell";
import type { WorkspaceView } from "./AppShell";
import { PypiRankings } from "./PypiRankings";
import { clearSession, readSession, writeSession } from "./session";
import type { RankingComparison, User } from "./types";
import { Workspace } from "./Workspace";

export default function App() {
  const [token, setToken] = useState(() => readSession("token"));
  const currentToken = useRef(token);
  const [user, setUser] = useState<User | null>(null);
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");
  const [attempt, setAttempt] = useState(0);
  const [authVisible, setAuthVisible] = useState(false);
  const [destination, setDestination] = useState<WorkspaceView>("rankings");
  const [comparison, setComparison] = useState<RankingComparison | null>(null);
  const clearComparison = useCallback(() => setComparison(null), []);

  const logout = useCallback((message = "") => {
    currentToken.current = "";
    clearSession();
    setToken("");
    setUser(null);
    setError("");
    setNotice(message);
    setAuthVisible(Boolean(message));
    setComparison(null);
    setDestination("rankings");
  }, []);

  const api = useMemo(
    () =>
      createApi(token, () => {
        // 旧请求迟到的 401 响应不得清除后来建立的登录状态。
        if (currentToken.current === token) logout("登录已失效，请重新登录。");
      }),
    [token, logout],
  );

  useEffect(() => {
    if (!token) return;
    const controller = new AbortController();
    setError("");
    api
      .me(controller.signal)
      .then((result) => {
        if (!controller.signal.aborted) setUser(result);
      })
      .catch((error: unknown) => {
        if (!controller.signal.aborted) setError(errorMessage(error));
      });
    return () => controller.abort();
  }, [api, token, attempt]);

  if (!token)
    return (
      <AppShell view={authVisible ? "login" : "rankings"} onNavigate={(view) => {
        if (view !== "login") { setDestination(view); setComparison(null); }
        setAuthVisible(view !== "rankings");
      }}>
        <div hidden={authVisible}><PypiRankings onCompare={(selection) => { setComparison(selection); setDestination("rankings"); setAuthVisible(true); }} /></div>
        {authVisible ? <div className="login-page"><button className="text-button" onClick={() => { setAuthVisible(false); setComparison(null); setDestination("rankings"); }}>← 返回公开榜单</button><AuthForm
          notice={notice || (comparison ? `登录后将准备 ${comparison.projects.join("、")} 的比较问题，点击发送后才会调用模型。` : "")}
          onLogin={(value) => {
            currentToken.current = value;
            writeSession("token", value);
            setUser(null);
            setNotice("");
            setError("");
            setToken(value);
          }}
        /></div> : null}
      </AppShell>
    );

  if (!user)
    return (
      <main className="auth-card">
        <h1>AI Workspace</h1>
        {error ? (
          <>
            <p role="alert" className="error">
              {error}
            </p>
            <button onClick={() => setAttempt((value) => value + 1)}>
              重试
            </button>
          </>
        ) : (
          <p role="status">正在恢复登录…</p>
        )}
        <button className="secondary" onClick={() => logout()}>
          返回公开榜单
        </button>
      </main>
    );

  return (
    <Workspace key={token} api={api} user={user} onLogout={() => logout()} initialView={destination} initialComparison={comparison} onComparisonConsumed={clearComparison} />
  );
}
