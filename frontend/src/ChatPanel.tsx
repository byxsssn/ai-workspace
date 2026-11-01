import { useEffect, useRef, useState } from "react";
import type { FormEvent } from "react";
import { ApiError, errorMessage } from "./api";
import type { Api } from "./api";
import { SourceLink, sourceTime } from "./SourceLink";
import type { ChatResponse, LearningSource, Message } from "./types";

interface Props {
  api: Api;
  conversationId: string;
  model: string;
  configured: boolean;
  learningSource?: LearningSource | null;
  suggestedMessage?: { key: number; text: string };
  onBusy: (busy: boolean) => void;
  onSent: () => void;
}

export function ChatPanel({
  api,
  conversationId,
  model,
  configured,
  learningSource = null,
  suggestedMessage,
  onBusy,
  onSent,
}: Props) {
  const [messages, setMessages] = useState<Message[]>([]);
  const [loading, setLoading] = useState(true);
  const [sending, setSending] = useState(false);
  const [historyError, setHistoryError] = useState("");
  const [sendError, setSendError] = useState("");
  const [draft, setDraft] = useState("");
  const [lastReply, setLastReply] = useState<ChatResponse | null>(null);
  const [attempt, setAttempt] = useState(0);
  const lifetime = useRef(new AbortController());
  const end = useRef<HTMLDivElement>(null);
  const submitting = useRef(false);

  useEffect(() => {
    if (suggestedMessage?.text) setDraft((current) => current || suggestedMessage.text);
  }, [suggestedMessage?.key, suggestedMessage?.text]);

  useEffect(() => {
    const controller = new AbortController();
    lifetime.current = controller;
    setLoading(true);
    setHistoryError("");
    api
      .messages(conversationId, controller.signal)
      .then((result) => {
        if (!controller.signal.aborted) setMessages(result);
      })
      .catch((error: unknown) => {
        if (!controller.signal.aborted) setHistoryError(errorMessage(error));
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [api, conversationId, attempt]);

  useEffect(() => {
    end.current?.scrollIntoView?.({ block: "nearest" });
  }, [messages]);

  async function send(event: FormEvent) {
    event.preventDefault();
    if (
      submitting.current ||
      loading ||
      historyError ||
      !configured ||
      !draft.trim() ||
      !model.trim()
    )
      return;
    submitting.current = true;
    setSending(true);
    onBusy(true);
    setSendError("");
    setLastReply(null);
    const signal = lifetime.current.signal;
    try {
      const result = await api.chat(
        conversationId,
        model.trim(),
        draft,
        signal,
      );
      if (signal.aborted) return;
      setLastReply(result);
      setDraft("");
      onSent();
      // 后端已提交两条消息，重新读取其真实 ID 和时间戳。
      try {
        const history = await api.messages(conversationId, signal);
        if (!signal.aborted) setMessages(history);
      } catch {
        if (!signal.aborted)
          setHistoryError(
            "聊天已成功，但历史读取失败。请刷新历史，无需重复发送。",
          );
      }
    } catch (error) {
      if (!signal.aborted) {
        const uncertain =
          error instanceof ApiError &&
          (error.status === 0 || error.status >= 500);
        setSendError(
          errorMessage(error) +
            (uncertain ? " 请先刷新历史确认结果，再决定是否重发。" : ""),
        );
      }
    } finally {
      submitting.current = false;
      if (!signal.aborted) {
        setSending(false);
        onBusy(false);
      }
    }
  }

  const labels: Record<string, string> = {
    user: "你",
    assistant: "助手",
    system: "系统",
  };

  return (
    <>
      {learningSource ? <p className={`notice small ${learningSource === "demo" ? "demo-notice" : ""}`}>{learningSource === "demo" ? "本会话使用虚构学习进度，不代表你的实际情况。" : "本会话使用你的 Notion 学习记录。"}点击发送后才会调用模型并产生费用。</p> : null}
      <div className="history-toolbar">
        <span className="muted small">{messages.length} 条消息</span>
        <button
          className="secondary"
          disabled={sending || loading}
          onClick={() => setAttempt((value) => value + 1)}
        >
          刷新历史
        </button>
      </div>
      {historyError && (
        <p role="alert" className="error">
          {historyError}
        </p>
      )}
      <div className="messages" aria-label="历史消息" aria-busy={loading}>
        {loading ? (
          <p role="status" className="empty">
            正在读取历史…
          </p>
        ) : !historyError && messages.length === 0 ? (
          <p className="empty">开始这一段对话吧。</p>
        ) : null}
        {messages.map((message) => (
          <article
            key={message.id}
            className={`message ${message.role === "user" ? "from-user" : ""}`}
          >
            <div className="message-meta">
              <strong>{labels[message.role] ?? message.role}</strong>
              <time dateTime={message.created_at}>
                {new Date(message.created_at).toLocaleString("zh-CN")}
              </time>
            </div>
            {/* 使用 React 纯文本渲染，不执行模型或用户输入中的 HTML。 */}
            <div className="message-content">{message.content}</div>
          </article>
        ))}
        <div ref={end} />
      </div>
      {lastReply && (
        <p role="status" className="small muted">
          本轮模型：{lastReply.model} · Token 用量：
          {lastReply.usage?.total_tokens ?? "未返回"}
        </p>
      )}
      {lastReply?.status === "needs_clarification" ? <p className="notice small">本轮需要补充信息或进一步核对资料，请查看回答中的说明。</p> : null}
      {lastReply?.sources?.length ? <section className="reply-sources" aria-label="本轮引用来源"><h3>本轮引用</h3><ul>{lastReply.sources.map((citation) => <li key={citation.source_id}><SourceLink url={citation.url}>{citation.title}</SourceLink>{!citation.url ? <span>{citation.title}</span> : null}<span className="muted small">{citation.kind === "official_document" ? "官方章节" : "学习记录"} · {sourceTime(citation.fetched_at)}{citation.truncated ? " · 内容不完整" : ""}</span></li>)}</ul></section> : null}
      {lastReply?.tool_calls?.length ? <details className="tool-summary"><summary>本轮资料读取 · {lastReply.tool_calls.length} 次</summary><ul>{lastReply.tool_calls.map((call, index) => <li key={index}>{call.tool === "get_learning_state" ? "学习进度" : `官方章节 · ${call.section_id ?? "未知"}`}<span className={call.status === "error" ? "error" : "muted"}>{call.status === "success" ? "读取成功" : "读取失败"}{call.cached ? " · 本轮缓存" : ""}</span></li>)}</ul><p className="muted small">摘要仅保留在当前页面；回答正文与来源文字已保存到历史。</p></details> : null}
      <form className="composer" onSubmit={send}>
        <label htmlFor="message">消息</label>
        <textarea
          id="message"
          rows={4}
          value={draft}
          onChange={(event) => setDraft(event.target.value)}
          readOnly={sending}
          placeholder={learningSource ? "围绕 FastAPI 提问，点击发送后开始学习" : "输入消息，点击发送"}
          maxLength={learningSource ? 8000 : undefined}
          required
        />
        {sendError && (
          <p role="alert" className="error">
            {sendError}
          </p>
        )}
        <div className="composer-footer">
          <span role="status" className="muted small">
            {sending
              ? "正在等待模型回复，请勿刷新页面…"
              : !configured
                ? "请先配置 API 密钥。"
                : "回复完成后一次性显示。"}
          </span>
          <button
            type="submit"
            disabled={
              sending ||
              loading ||
              !!historyError ||
              !configured ||
              !draft.trim() ||
              !model.trim()
            }
          >
            {sending ? "生成中…" : "发送"}
          </button>
        </div>
      </form>
    </>
  );
}
