import { useCallback, useEffect, useRef, useState } from "react";
import type { FormEvent } from "react";
import { errorMessage } from "./api";
import type { Api } from "./api";
import { ChatPanel } from "./ChatPanel";
import { AppShell } from "./AppShell";
import type { WorkspaceView } from "./AppShell";
import { CredentialSettings } from "./CredentialSettings";
import { LearningMap } from "./LearningMap";
import { PypiRankings } from "./PypiRankings";
import { readSession, writeSession } from "./session";
import type { Conversation, LearningRecord, RankingComparison, User } from "./types";

export function Workspace({ api, user, onLogout, initialView, initialComparison, onComparisonConsumed }: {
  api: Api; user: User; onLogout: () => void;
  initialView: WorkspaceView;
  initialComparison: RankingComparison | null;
  onComparisonConsumed: () => void;
}) {
  const [view, setView] = useState<WorkspaceView>(initialView);
  const [mapOpened, setMapOpened] = useState(initialView === "map");
  const [assistantOpened, setAssistantOpened] = useState(initialView === "assistant");
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [selectedId, setSelectedId] = useState(() => readSession(`${user.id}:conversation`));
  const [model, setModel] = useState(() => readSession(`${user.id}:model`) || "openai/gpt-6-luna");
  const [title, setTitle] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [entryError, setEntryError] = useState("");
  const [comparisonError, setComparisonError] = useState("");
  const [failedComparison, setFailedComparison] = useState<RankingComparison | null>(null);
  const [creating, setCreating] = useState(false);
  const [sending, setSending] = useState(false);
  const [configured, setConfigured] = useState(false);
  const [revision, setRevision] = useState(0);
  const [suggestion, setSuggestion] = useState<{ conversationId: string; key: number; text: string } | null>(null);
  const lifetime = useRef(new AbortController());
  const listController = useRef<AbortController | null>(null);
  const actionPending = useRef(false);
  const suggestionSequence = useRef(0);
  const initialComparisonHandled = useRef(false);
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

  function navigate(next: WorkspaceView) {
    if (next === "map") setMapOpened(true);
    if (next === "assistant") setAssistantOpened(true);
    setView(next);
  }

  const compare = useCallback(async (selection: RankingComparison) => {
    if (actionPending.current || sending) return;
    actionPending.current = true;
    setCreating(true);
    setComparisonError("");
    setFailedComparison(null);
    onComparisonConsumed();
    listController.current?.abort();
    setLoading(false);
    const signal = lifetime.current.signal;
    try {
      const conversation = await api.createConversation(`PyPI 比较 · ${selection.projects.join(" / ")}`, signal);
      if (signal.aborted) return;
      setConversations((current) => [conversation, ...current]);
      setSelectedId(conversation.id);
      setSuggestion({ conversationId: conversation.id, key: ++suggestionSequence.current, text: `请查询本站的 PyPI 月度下载数据，比较 ${selection.projects.join("、")}。我查看的榜单统计月份是 ${selection.period}；请说明实际查询的数据月份、来源和收录范围，区分下载量事实与选型建议。` });
      setAssistantOpened(true);
      setView("assistant");
    } catch (failure) {
      if (!signal.aborted) {
        setComparisonError(errorMessage(failure));
        setFailedComparison(selection);
      }
    } finally {
      actionPending.current = false;
      if (!signal.aborted) setCreating(false);
    }
  }, [api, sending, onComparisonConsumed]);

  useEffect(() => {
    // 登录后等首轮会话读取结束再处理一次公开比较意图，避免 StrictMode 重复创建。
    if (!initialComparison || loading || initialComparisonHandled.current) return;
    initialComparisonHandled.current = true;
    void compare(initialComparison);
  }, [initialComparison, loading, compare]);

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
    <AppShell view={view} onNavigate={navigate} email={user.email} onLogout={onLogout} busy={locked} sidebar={
              <section className="sidebar-conversations" aria-label="会话列表">
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
                    <button key={conversation.id} className="conversation-item" aria-current={selectedId === conversation.id ? "page" : undefined} disabled={locked} onClick={() => { setSelectedId(conversation.id); setSuggestion(null); openAssistant(); }}>
                      <span>{conversation.title}</span>
                      {conversation.learning_topic === "fastapi" ? <small className="conversation-kind">FastAPI · {conversation.learning_source === "demo" ? "演示" : "Notion"}</small> : null}
                      <time dateTime={conversation.updated_at}>{new Date(conversation.updated_at).toLocaleString("zh-CN")}</time>
                    </button>
                  ))}
                </nav>
              </section>
    }>
      {comparisonError ? <div className="notice error" role="alert"><p>{comparisonError}</p>{failedComparison ? <button className="secondary" disabled={locked} onClick={() => void compare(failedComparison)}>重试比较</button> : null}</div> : null}
      <div hidden={view !== "rankings"}><PypiRankings busy={locked} onCompare={compare} /></div>
      {mapOpened ? <div hidden={view !== "map"}><LearningMap api={api} busy={locked} entryError={entryError} onContinue={continueLearning} onSourceChange={() => setEntryError("")} /></div> : null}
      <section className="settings-page" aria-label="账户与设置" hidden={view !== "settings"}><p className="eyebrow">个人空间 / 设置</p><h2>账户与模型连接</h2><p className="muted">{user.email}</p><CredentialSettings api={api} disabled={locked} onConfigured={setConfigured} /></section>
      {assistantOpened ? (
          <div hidden={view !== "assistant"}>
            <section className="chat" aria-label="助手对话">
              <div className="chat-heading">
                <div><p className="eyebrow">{learningSource ? "学习助手" : "通用助手"}</p><h2>{selected?.title ?? "开始聊天"}</h2>{learningSource ? <div className="detail-tags"><span className="assistant-chip">FastAPI</span><span className="source-badge">{learningSource === "notion" ? "Notion 学习记录" : "虚构演示记录"}</span></div> : null}</div>
                <label className="model-picker">模型<input aria-label="模型" list="models" value={model} onChange={(event) => setModel(event.target.value)} disabled={sending} placeholder="例如 openai/gpt-6-luna" autoComplete="off" spellCheck={false} /><datalist id="models"><option value="openai/gpt-6-luna" /><option value="openai/gpt-4.1-mini" /><option value="google/gemini-2.5-flash" /></datalist></label>
              </div>
              <p className="muted small model-note">{learningSource ? "读取学习进度与官方章节后讲解；不会修改 Notion。模型须支持工具调用。" : "可查询本站 PyPI 榜单；请使用支持工具调用的 OpenRouter 模型。"}</p>
              {!configured ? <p className="notice small">发送前需要配置自己的 API 密钥。<button className="text-button" onClick={() => setView("settings")}>前往账户与设置</button></p> : null}
              {selected ? <ChatPanel key={selected.id} api={api} conversationId={selected.id} model={model} configured={configured} learningSource={learningSource} suggestedMessage={suggestion?.conversationId === selected.id ? suggestion : undefined} onBusy={setSending} onSent={refresh} /> : <div className="empty"><h3>从一个问题开始</h3><p>{loading ? "正在加载…" : "在左侧新建会话，或从学习地图继续 FastAPI。"}</p></div>}
            </section>
          </div>
        ) : null}
    </AppShell>
  );
}
