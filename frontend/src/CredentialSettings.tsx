import { useEffect, useRef, useState } from "react";
import type { FormEvent } from "react";
import { ApiError, errorMessage } from "./api";
import type { Api } from "./api";

interface Props {
  api: Api;
  disabled: boolean;
  onConfigured: (configured: boolean) => void;
}

export function CredentialSettings({ api, disabled, onConfigured }: Props) {
  const [configured, setConfigured] = useState(false);
  const [apiKey, setApiKey] = useState("");
  const [busy, setBusy] = useState(true);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [attempt, setAttempt] = useState(0);
  const lifetime = useRef(new AbortController());

  useEffect(() => {
    const controller = new AbortController();
    lifetime.current = controller;
    setBusy(true);
    setError("");
    api
      .credential(controller.signal)
      .then((credential) => {
        if (controller.signal.aborted) return;
        setConfigured(credential.configured);
        onConfigured(credential.configured);
      })
      .catch((error: unknown) => {
        if (controller.signal.aborted) return;
        setConfigured(false);
        onConfigured(false);
        if (!(error instanceof ApiError && error.status === 404))
          setError(errorMessage(error));
      })
      .finally(() => {
        if (!controller.signal.aborted) setBusy(false);
      });
    return () => controller.abort();
  }, [api, onConfigured, attempt]);

  async function save(event: FormEvent) {
    event.preventDefault();
    if (busy || disabled || !apiKey.trim()) return;
    const secret = apiKey.trim();
    setApiKey("");
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const credential = await api.saveCredential(
        secret,
        lifetime.current.signal,
      );
      setConfigured(credential.configured);
      onConfigured(credential.configured);
      setNotice("API Key 已保存。");
    } catch (error) {
      if (!lifetime.current.signal.aborted) setError(errorMessage(error));
    } finally {
      setBusy(false);
    }
  }

  async function remove() {
    if (busy || disabled) return;
    setBusy(true);
    setError("");
    setNotice("");
    try {
      await api.deleteCredential(lifetime.current.signal);
      setConfigured(false);
      onConfigured(false);
      setNotice("API Key 已移除。");
    } catch (error) {
      if (!lifetime.current.signal.aborted) setError(errorMessage(error));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="credentials" aria-label="API Key 配置">
      <h2>OpenRouter API Key</h2>
      <p role="status" className={configured ? "success small" : "muted small"}>
        {busy ? "正在处理…" : configured ? "已配置" : "未配置"}
      </p>
      <form onSubmit={save}>
        <fieldset disabled={busy || disabled}>
          <label htmlFor="api-key">
            {configured ? "替换 API Key" : "API Key"}
          </label>
          <input
            id="api-key"
            type="password"
            autoComplete="off"
            spellCheck={false}
            maxLength={4096}
            value={apiKey}
            onChange={(event) => setApiKey(event.target.value)}
            placeholder="sk-or-…"
            required
          />
          <button type="submit" disabled={!apiKey.trim()}>
            保存密钥
          </button>
          {configured && (
            <button type="button" className="secondary" onClick={remove}>
              移除密钥
            </button>
          )}
        </fieldset>
      </form>
      <p className="muted small">
        密钥由后端加密保存，浏览器不会保存或回显密钥。
      </p>
      {notice && (
        <p role="status" className="small success">
          {notice}
        </p>
      )}
      {error && (
        <>
          <p role="alert" className="error small">
            {error}
          </p>
          <button
            className="secondary"
            disabled={busy || disabled}
            onClick={() => setAttempt((value) => value + 1)}
          >
            重新读取配置
          </button>
        </>
      )}
    </section>
  );
}
