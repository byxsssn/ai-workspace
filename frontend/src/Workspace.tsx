import { useCallback, useEffect, useRef, useState } from "react";
import type { FormEvent } from "react";
import { errorMessage } from "./api";
import type { Api } from "./api";
import { ChatPanel } from "./ChatPanel";
import { CredentialSettings } from "./CredentialSettings";
import { readSession, writeSession } from "./session";
import type { Conversation, User } from "./types";

export function Workspace({
  api,
  user,
  onLogout,
}: {
  api: Api;
  user: User;
  onLogout: () => void;
}) {
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [selectedId, setSelectedId] = useState(() =>
    readSession(`${user.id}:conversation`),
  );
  const [model, setModel] = useState(
    () => readSession(`${user.id}:model`) || "openai/gpt-6-luna",
  );
  const [title, setTitle] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [creating, setCreating] = useState(false);
  const [sending, setSending] = useState(false);
  const [configured, setConfigured] = useState(false);
  const [revision, setRevision] = useState(0);
  const lifetime = useRef(new AbortController());
  const refresh = useCallback(() => setRevision((value) => value + 1), []);

  useEffect(() => {
    lifetime.current = new AbortController();
    return () => lifetime.current.abort();
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setError("");
    api
      .conversations(controller.signal)
      .then((items) => {
        if (controller.signal.aborted) return;
        setConversations(items);
        setSelectedId((current) =>
          items.some((item) => item.id === current)
            ? current
            : (items[0]?.id ?? ""),
        );
      })
      .catch((error: unknown) => {
        if (!controller.signal.aborted) setError(errorMessage(error));
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [api, revision]);

  useEffect(() => {
    writeSession(`${user.id}:conversation`, selectedId);
  }, [user.id, selectedId]);
  useEffect(() => {
    writeSession(`${user.id}:model`, model);
  }, [user.id, model]);

  async function create(event: FormEvent) {
    event.preventDefault();
    if (creating || sending || loading) return;
    setCreating(true);
    setError("");
    try {
      const conversation = await api.createConversation(
        title.trim() || "新会话",
        lifetime.current.signal,
      );
      setConversations((current) => [conversation, ...current]);
      setSelectedId(conversation.id);
      setTitle("");
    } catch (error) {
      if (!lifetime.current.signal.aborted) setError(errorMessage(error));
    } finally {
      setCreating(false);
    }
  }

  const selected = conversations.find((item) => item.id === selectedId);
  const locked = creating || sending;

  return (
    <div className="app-shell">
      <header className="app-header">
        <h1>AI Workspace</h1>
        <div>
          <span className="muted small">{user.email}</span>
          <button className="secondary" onClick={onLogout}>
            退出登录
          </button>
        </div>
      </header>
      <div className="workspace">
        <aside className="sidebar">
          <section aria-label="会话列表">
            <div className="section-heading">
              <h2>会话</h2>
              <button
                className="secondary"
                disabled={locked || loading}
                onClick={refresh}
              >
                刷新列表
              </button>
            </div>
            <form onSubmit={create}>
              <fieldset disabled={locked || loading}>
                <label htmlFor="conversation-title">会话标题</label>
                <input
                  id="conversation-title"
                  value={title}
                  onChange={(event) => setTitle(event.target.value)}
                  maxLength={255}
                  placeholder="新会话（可选标题）"
                />
                <button type="submit">
                  {creating ? "创建中…" : "新建会话"}
                </button>
              </fieldset>
            </form>
            {error && (
              <p role="alert" className="error small">
                {error}
              </p>
            )}
            {loading && (
              <p role="status" className="muted small">
                正在读取会话…
              </p>
            )}
            {!loading && !error && conversations.length === 0 && (
              <p className="muted small">还没有会话，先新建一个。</p>
            )}
            <nav className="conversation-list" aria-label="选择会话">
              {conversations.map((conversation) => (
                <button
                  key={conversation.id}
                  className="conversation-item"
                  aria-current={
                    selectedId === conversation.id ? "page" : undefined
                  }
                  disabled={locked}
                  onClick={() => setSelectedId(conversation.id)}
                >
                  <span>{conversation.title}</span>
                  <time dateTime={conversation.updated_at}>
                    {new Date(conversation.updated_at).toLocaleString("zh-CN")}
                  </time>
                </button>
              ))}
            </nav>
          </section>
          <CredentialSettings
            api={api}
            disabled={locked}
            onConfigured={setConfigured}
          />
        </aside>
        <main className="chat">
          <div className="chat-heading">
            <h2>{selected?.title ?? "开始聊天"}</h2>
            <label className="model-picker">
              模型
              <input
                aria-label="模型"
                list="models"
                value={model}
                onChange={(event) => setModel(event.target.value)}
                disabled={sending}
                placeholder="例如 openai/gpt-6-luna"
                autoComplete="off"
                spellCheck={false}
              />
              <datalist id="models">
                <option value="openai/gpt-6-luna" />
                <option value="openai/gpt-4.1-mini" />
                <option value="google/gemini-2.5-flash" />
              </datalist>
            </label>
          </div>
          <p className="muted small">
            可选择示例或输入完整 OpenRouter 模型 ID；可用性取决于账号和模型。
          </p>
          {selected ? (
            <ChatPanel
              key={selected.id}
              api={api}
              conversationId={selected.id}
              model={model}
              configured={configured}
              onBusy={setSending}
              onSent={refresh}
            />
          ) : (
            <p className="empty">
              {loading ? "正在加载…" : "在左侧新建或选择一个会话。"}
            </p>
          )}
        </main>
      </div>
    </div>
  );
}
