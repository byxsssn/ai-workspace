import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { createApi, errorMessage } from "./api";
import { AuthForm } from "./AuthForm";
import { clearSession, readSession, writeSession } from "./session";
import type { User } from "./types";
import { Workspace } from "./Workspace";

export default function App() {
  const [token, setToken] = useState(() => readSession("token"));
  const currentToken = useRef(token);
  const [user, setUser] = useState<User | null>(null);
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");
  const [attempt, setAttempt] = useState(0);

  const logout = useCallback((message = "") => {
    currentToken.current = "";
    clearSession();
    setToken("");
    setUser(null);
    setError("");
    setNotice(message);
  }, []);

  const api = useMemo(
    () =>
      createApi(token, () => {
        // A late 401 from an old request must not clear a newer login.
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
      <AuthForm
        notice={notice}
        onLogin={(value) => {
          currentToken.current = value;
          writeSession("token", value);
          setUser(null);
          setNotice("");
          setError("");
          setToken(value);
        }}
      />
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
          返回登录
        </button>
      </main>
    );

  return (
    <Workspace key={token} api={api} user={user} onLogout={() => logout()} />
  );
}
