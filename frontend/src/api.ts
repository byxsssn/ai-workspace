import type {
  ChatResponse,
  Conversation,
  Credential,
  LearningContext,
  LearningRecordDetail,
  LearningRecordList,
  LearningSource,
  Message,
  TokenResponse,
  User,
} from "./types";

export class ApiError extends Error {
  constructor(
    public readonly status: number,
    message: string,
    public readonly code?: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

// 仅将已知接口错误映射为中文文案，不回显校验输入或上游响应体。
const safeDetails: Record<string, string> = {
  "Invalid email or password": "邮箱或密码错误。",
  "Email is already registered": "该邮箱已注册，请直接登录。",
  "Invalid invitation code": "邀请码无效，请向邀请人获取有效邀请码。",
  "User is inactive": "该账号已停用。",
  "Provider credential not configured": "请先配置 OpenRouter API 密钥。",
  "Conversation not found": "会话不存在或无权访问，请重新选择会话。",
  "Chat provider request failed":
    "模型服务请求失败，请检查 API 密钥、额度和模型名称。",
};

const safeCodes: Record<string, string> = {
  notion_not_configured: "尚未配置 Notion 连接。配置后可读取自己的学习记录，也可以先选择演示数据。",
  notion_catalog_not_configured: "尚未配置 Notion 学习数据库。请先完成学习地图的数据源配置。",
  notion_not_available: "当前账号无权读取这份 Notion 学习记录，请联系连接所有者确认。",
  learning_record_not_found: "这条学习记录不存在、已归档或无权访问，请刷新地图后重试。",
  source_unauthorized: "Notion 连接凭据已失效，请更新连接配置。应用登录仍然有效。",
  source_forbidden: "无法读取来源，请确认 Notion 页面已共享给应用连接。",
  source_not_found: "来源页面不存在或尚未共享给应用连接。",
  source_rate_limited: "来源服务请求过于频繁，请稍后重试。",
  source_timeout: "读取资料超时，请稍后重试。",
  source_unavailable: "资料来源暂时不可用，请稍后重试。",
  source_redirect: "资料来源地址发生跳转，需要先核对配置。",
  source_too_large: "资料超过本次读取限额，请查看原文。",
  invalid_source_response: "未能可靠读取资料，请稍后重试或检查来源。",
  source_schema_mismatch: "学习记录的字段与预期不一致，请检查 Notion 字段配置。",
  learning_agent_failed: "学习助手请求失败，请检查模型支持情况、密钥和额度。",
  learning_agent_timeout: "学习助手处理超时，请先刷新历史再决定是否重发。",
  learning_agent_limit: "本轮已达到调用上限，请缩小问题范围后重试。",
  learning_invalid_citation: "本轮回答的来源未通过校验，请重试或查看官方原文。",
  learning_sources_required: "本轮没有取得足够资料，暂时无法给出有依据的讲解。",
  learning_invalid_answer: "学习助手未返回有效回答，请重试。",
  learning_conversation_changed: "会话历史已变化，本轮回答未保存。请先刷新历史再决定是否重发。",
  conversation_changed: "会话历史已变化，本轮回答未保存。请先刷新历史再决定是否重发。",
  conversation_context_conflict: "会话的学习上下文与请求不一致，请返回地图重新选择会话。",
};

export function errorMessage(error: unknown): string {
  return error instanceof ApiError ? error.message : "操作失败，请稍后重试。";
}

export function createApi(
  token: string | null = null,
  onUnauthorized?: () => void,
) {
  async function request<T>(
    path: string,
    method = "GET",
    body?: unknown,
    signal?: AbortSignal,
  ): Promise<T> {
    let response: Response;
    try {
      response = await fetch(`/api${path}`, {
        method,
        headers: {
          ...(body === undefined ? {} : { "Content-Type": "application/json" }),
          ...(token ? { Authorization: `Bearer ${token}` } : {}),
        },
        body: body === undefined ? undefined : JSON.stringify(body),
        signal,
        cache: "no-store",
        credentials: "omit",
        redirect: "error",
      });
    } catch (error) {
      if (signal?.aborted) throw error;
      throw new ApiError(0, "无法连接服务，请检查网络后重试。");
    }

    if (!response.ok) {
      const payload: unknown = await response.json().catch(() => null);
      const detail =
        payload && typeof payload === "object" && "detail" in payload
          ? payload.detail
          : null;
      const code = detail && typeof detail === "object" && "code" in detail &&
        typeof detail.code === "string" ? detail.code : undefined;
      const knownSourceError = code !== undefined && Object.hasOwn(safeCodes, code);
      if (
        token &&
        (response.status === 401 || response.status === 403) &&
        !knownSourceError &&
        !signal?.aborted
      ) {
        onUnauthorized?.();
      }
      const message =
        knownSourceError ? safeCodes[code] : typeof detail === "string" && Object.hasOwn(safeDetails, detail) ? safeDetails[detail] : undefined;
      const fallback: Record<number, string> = {
        401: token ? "登录已过期，请重新登录。" : "邮箱或密码错误。",
        403: "没有访问权限。",
        404: "请求的内容不存在。",
        409: "会话已发生变化，请先刷新历史再重试。",
        422: "输入不符合要求，请检查填写内容。",
        429: "请求过于频繁，请稍后重试。",
      };
      throw new ApiError(
        response.status,
        message ?? fallback[response.status] ?? "服务暂时不可用，请稍后重试。",
        knownSourceError ? code : undefined,
      );
    }
    if (response.status === 204) return undefined as T;
    try {
      return (await response.json()) as T;
    } catch {
      throw new ApiError(0, "服务返回格式异常，请稍后重试。");
    }
  }

  return {
    register: (email: string, password: string, inviteCode?: string) =>
      request<User>("/users/register", "POST", {
        email,
        password,
        ...(inviteCode ? { invite_code: inviteCode } : {}),
      }),
    login: (email: string, password: string) =>
      request<TokenResponse>("/auth/login", "POST", { email, password }),
    me: (signal?: AbortSignal) =>
      request<User>("/users/me", "GET", undefined, signal),
    credential: (signal?: AbortSignal) =>
      request<Credential>("/providers/openrouter", "GET", undefined, signal),
    saveCredential: (apiKey: string, signal?: AbortSignal) =>
      request<Credential>(
        "/providers/openrouter",
        "PUT",
        { api_key: apiKey },
        signal,
      ),
    deleteCredential: (signal?: AbortSignal) =>
      request<void>("/providers/openrouter", "DELETE", undefined, signal),
    conversations: (signal?: AbortSignal) =>
      request<Conversation[]>("/conversations", "GET", undefined, signal),
    createConversation: (title: string, signal?: AbortSignal, learning?: LearningContext) =>
      request<Conversation>("/conversations", "POST", { title, ...learning }, signal),
    learningRecords: (source: LearningSource = "notion", signal?: AbortSignal) =>
      request<LearningRecordList>(`/learning/records?source=${source}`, "GET", undefined, signal),
    learningRecord: (id: string, source: LearningSource, signal?: AbortSignal) =>
      request<LearningRecordDetail>(
        `/learning/records/${encodeURIComponent(id)}?source=${source}`, "GET", undefined, signal,
      ),
    messages: (id: string, signal?: AbortSignal) =>
      request<Message[]>(
        `/conversations/${encodeURIComponent(id)}/messages`,
        "GET",
        undefined,
        signal,
      ),
    chat: (id: string, model: string, content: string, signal?: AbortSignal) =>
      request<ChatResponse>(
        `/conversations/${encodeURIComponent(id)}/chat`,
        "POST",
        { model, content },
        signal,
      ),
  };
}

export type Api = ReturnType<typeof createApi>;
