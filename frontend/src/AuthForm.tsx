import { useState } from "react";
import type { FormEvent } from "react";
import { createApi, errorMessage } from "./api";

const api = createApi();

export function AuthForm({
  onLogin,
  notice,
}: {
  onLogin: (token: string) => void;
  notice: string;
}) {
  const [registering, setRegistering] = useState(false);
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [success, setSuccess] = useState("");

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (busy) return;
    setBusy(true);
    setError("");
    setSuccess("");
    try {
      if (registering) {
        await api.register(email.trim(), password);
        setRegistering(false);
        setSuccess("注册成功，请登录。");
      } else {
        const result = await api.login(email.trim(), password);
        onLogin(result.access_token);
      }
    } catch (error) {
      setError(errorMessage(error));
    } finally {
      setPassword("");
      setBusy(false);
    }
  }

  return (
    <main className="auth-card">
      <h1>AI Workspace</h1>
      <p className="muted">使用自己的 OpenRouter API Key 开始聊天。</p>
      <h2>{registering ? "创建账号" : "登录"}</h2>
      {notice && <p role="status">{notice}</p>}
      {success && (
        <p role="status" className="success">
          {success}
        </p>
      )}
      {error && (
        <p role="alert" className="error">
          {error}
        </p>
      )}
      <form onSubmit={submit}>
        <fieldset disabled={busy}>
          <label htmlFor="email">邮箱</label>
          <input
            id="email"
            type="email"
            autoComplete="username"
            value={email}
            onChange={(event) => setEmail(event.target.value)}
            required
          />
          <label htmlFor="password">密码</label>
          <input
            id="password"
            type="password"
            autoComplete={registering ? "new-password" : "current-password"}
            minLength={8}
            maxLength={128}
            value={password}
            onChange={(event) => setPassword(event.target.value)}
            required
          />
          <p className="muted small">密码长度为 8–128 个字符。</p>
          <button type="submit">
            {busy ? "处理中…" : registering ? "注册" : "登录"}
          </button>
          <button
            type="button"
            className="secondary"
            onClick={() => {
              setRegistering(!registering);
              setError("");
              setSuccess("");
              setPassword("");
            }}
          >
            {registering ? "已有账号，去登录" : "没有账号，去注册"}
          </button>
        </fieldset>
      </form>
    </main>
  );
}
