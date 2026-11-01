import { useCallback, useEffect, useRef, useState } from "react";
import type { FormEvent } from "react";
import { errorMessage } from "./api";
import type { Api } from "./api";
import { ChatPanel } from "./ChatPanel";
import { CredentialSettings } from "./CredentialSettings";
import { LearningMap } from "./LearningMap";
import { readSession, writeSession } from "./session";
import type { Conversation, LearningRecord, User } from "./types";

export function Workspace({ api, user, onLogout }: { api: Api; user: User; onLogout: () => void }) {
  const [view, setView] = useState<"map" | "assistant">("map");
  const [assistantOpened, setAssistantOpened] = useState(false);
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [selectedId, setSelectedId] = useState(() => readSession(`${user.id}:conversation`));
  const [model, setModel] = useState(() => readSession(`${user.id}:model`) || "openai/gpt-6-luna");
  const [title, setTitle] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [entryError, setEntryError] = useState("");
  const [creating, setCreating] = useState(false);
  const [sending, setSending] = useState(false);
  const [configured, setConfigured] = useState(false);
  const [revision, setRevision] = useState(0);
  const [suggestion, setSuggestion] = useState<{ conversationId: string; key: number; text: string } | null>(null);
  const lifetime = useRef(new AbortController());
  const listController = useRef<AbortController | null>(null);
  const actionPending = useRef(false);
  const suggestionSequence = useRef(0);
  const refresh = useCallback(() => setRevision((value) => value + 1), []);

  useEffect(() => {
    lifetime.current = new AbortController();
    return () => lifetime.current.abort();
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    listController.current = controller;
    setLoading(true);
    setError("");
    api.conversations(controller.signal)
      .then((items) => {
        if (controller.signal.aborted) return;
        setConversations(items);
        setSelectedId((current) => items.some((item) => item.id === current) ? current : (items[0]?.id ?? ""));
      })
      .catch((failure: unknown) => { if (!controller.signal.aborted) setError(errorMessage(failure)); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [api, revision]);

  useEffect(() => { writeSession(`${user.id}:conversation`, selectedId); }, [user.id, selectedId]);
  useEffect(() => { writeSession(`${user.id}:model`, model); }, [user.id, model]);

  function openAssistant() {
    setAssistantOpened(true);
    setView("assistant");
  }

  async function create(event: FormEvent) {
    event.preventDefault();
    if (actionPending.current || sending || loading) return;
    actionPending.current = true;
    setCreating(true);
    setError("");
    const signal = lifetime.current.signal;
    try {
      const conversation = await api.createConversation(title.trim() || "新会话", signal);
      if (signal.aborted) return;
      setConversations((current) => [conversation, ...current]);
      setSelectedId(conversation.id);
      setTitle("");
      setSuggestion(null);
      openAssistant();
    } catch (failure) {
      if (!signal.aborted) setError(errorMessage(failure));
    } finally {
      actionPending.current = false;
      if (!signal.aborted) setCreating(false);
    }
  }

  async function continueLearning(record: LearningRecord) {
    if (record.topic_id !== "fastapi" || actionPending.current || sending) return;
    actionPending.current = true;
    setCreating(true);
    setEntryError("");
    listController.current?.abort();
    setLoading(false);
    const signal = lifetime.current.signal;
    try {
      // 进入前重新读取，按持久化上下文续接同来源的最近会话。
      const latest = await api.conversations(signal);
      if (signal.aborted) return;
      const existing = latest.filter((item) => item.learning_topic === "fastapi" && item.learning_source === record.source)
        .sort((a, b) => Date.parse(b.updated_at) - Date.parse(a.updated_at))[0];
      const selected = existing ?? await api.createConversation(
        `FastAPI 学习 · ${record.source === "notion" ? "Notion" : "演示"}`,
        signal,
        { learning_topic: "fastapi", learning_source: record.source },
      );
      if (signal.aborted) return;
      setConversations(existing ? latest : [selected, ...latest]);
      setSelectedId(selected.id);
      setError("");
      setSuggestion({ conversationId: selected.id, key: ++suggestionSequence.current, text: "根据我的学习进度，帮我继续学习 FastAPI。" });
      openAssistant();
    } catch (failure) {
      if (!signal.aborted) setEntryError(errorMessage(failure));
    } finally {
      actionPending.current = false;
      if (!signal.aborted) setCreating(false);
    }
  }

  const selected = conversations.find((item) => item.id === selectedId);
  const locked = creating || sending;
  const learningSource = selected?.learning_topic === "fastapi" ? selected.learning_source ?? null : null;

  return (
    <div className="app-shell">
      <header className="app-header">
        <div className="brand"><span className="brand-mark" aria-hidden="true">a<span>i</span></span><div><h1>AI Workspace</h1><span className="brand-caption">把学习接回实践</span></div></div>
        <nav className="workspace-nav" aria-label="工作区入口">
          <button aria-pressed={view === "map"} disabled={locked} onClick={() => setView("map")}>学习地图</button>
          <button aria-pressed={view === "assistant"} disabled={locked} onClick={openAssistant}>助手</button>
        </nav>
        <div className="account"><span className="muted small">{user.email}</span><button className="secondary" onClick={onLogout}>退出登录</button></div>
      </header>
      <main>
        <div hidden={view !== "map"}>
          <LearningMap api={api} busy={locked} entryError={entryError} onContinue={continueLearning} onSourceChange={() => setEntryError("")} />
        </div>
        {assistantOpened ? (
          <div className="workspace" hidden={view !== "assistant"}>
            <aside className="sidebar">
              <section aria-label="会话列表">
                <div className="section-heading"><h2>我的会话</h2><button className="secondary" disabled={locked || loading} onClick={refresh}>刷新列表</button></div>
                <form onSubmit={create}>
                  <fieldset disabled={locked || loading}>
                    <label htmlFor="conversation-title">会话标题</label>
                    <input id="conversation-title" value={title} onChange={(event) => setTitle(event.target.value)} maxLength={255} placeholder="新会话（可选标题）" />
                    <button type="submit">{creating ? "创建中…" : "新建会话"}</button>
                  </fieldset>
                </form>
                <p className="muted small sidebar-hint">新建普通对话；学习对话从地图进入。</p>
                {error ? <p role="alert" className="error small">{error}</p> : null}
                {loading ? <p role="status" className="muted small">正在读取会话…</p> : null}
                {!loading && !error && conversations.length === 0 ? <p className="muted small">还没有会话，可以新建对话或从地图继续学习。</p> : null}
                <nav className="conversation-list" aria-label="选择会话">
                  {conversations.map((conversation) => (
                    <button key={conversation.id} className="conversation-item" aria-current={selectedId === conversation.id ? "page" : undefined} disabled={locked} onClick={() => { setSelectedId(conversation.id); setSuggestion(null); }}>
                      <span>{conversation.title}</span>
                      {conversation.learning_topic === "fastapi" ? <small className="conversation-kind">FastAPI · {conversation.learning_source === "demo" ? "演示" : "Notion"}</small> : null}
                      <time dateTime={conversation.updated_at}>{new Date(conversation.updated_at).toLocaleString("zh-CN")}</time>
                    </button>
                  ))}
                </nav>
              </section>
              <CredentialSettings api={api} disabled={locked} onConfigured={setConfigured} />
            </aside>
            <section className="chat" aria-label="助手对话">
              <div className="chat-heading">
                <div><p className="eyebrow">{learningSource ? "学习助手" : "通用助手"}</p><h2>{selected?.title ?? "开始聊天"}</h2>{learningSource ? <div className="detail-tags"><span className="assistant-chip">FastAPI</span><span className="source-badge">{learningSource === "notion" ? "Notion 学习记录" : "虚构演示记录"}</span></div> : null}</div>
                <label className="model-picker">模型<input aria-label="模型" list="models" value={model} onChange={(event) => setModel(event.target.value)} disabled={sending} placeholder="例如 openai/gpt-6-luna" autoComplete="off" spellCheck={false} /><datalist id="models"><option value="openai/gpt-6-luna" /><option value="openai/gpt-4.1-mini" /><option value="google/gemini-2.5-flash" /></datalist></label>
              </div>
              <p className="muted small model-note">{learningSource ? "读取学习进度与官方章节后讲解；不会修改 Notion。模型须支持工具调用。" : "可输入完整 OpenRouter 模型 ID；可用性取决于账号和模型。"}</p>
              {selected ? <ChatPanel key={selected.id} api={api} conversationId={selected.id} model={model} configured={configured} learningSource={learningSource} suggestedMessage={suggestion?.conversationId === selected.id ? suggestion : undefined} onBusy={setSending} onSent={refresh} /> : <div className="empty"><h3>从一个问题开始</h3><p>{loading ? "正在加载…" : "在左侧新建会话，或从学习地图继续 FastAPI。"}</p></div>}
            </section>
          </div>
        ) : null}
      </main>
    </div>
  );
}
