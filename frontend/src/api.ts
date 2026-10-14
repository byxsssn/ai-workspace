import type {
  ChatResponse,
  Conversation,
  Credential,
  Message,
  TokenResponse,
  User,
} from "./types";

export class ApiError extends Error {
  constructor(
    public readonly status: number,
    message: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

// Only known messages are displayed. Never echo raw validation inputs or upstream bodies.
const safeDetails: Record<string, string> = {
  "Invalid email or password": "邮箱或密码错误。",
  "Email is already registered": "该邮箱已注册，请直接登录。",
  "Invalid invitation code": "邀请码无效，请向邀请人获取有效邀请码。",
  "User is inactive": "该账号已停用。",
  "Provider credential not configured": "请先配置 OpenRouter API Key。",
  "Conversation not found": "会话不存在或无权访问，请重新选择会话。",
  "Chat provider request failed":
    "模型服务请求失败，请检查 API Key、额度和模型名称。",
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
      if (
        token &&
        (response.status === 401 || response.status === 403) &&
        !signal?.aborted
      ) {
        onUnauthorized?.();
      }
      const payload: unknown = await response.json().catch(() => null);
      const detail =
        payload && typeof payload === "object" && "detail" in payload
          ? payload.detail
          : null;
      const message =
        typeof detail === "string" ? safeDetails[detail] : undefined;
      const fallback: Record<number, string> = {
        401: token ? "登录已过期，请重新登录。" : "邮箱或密码错误。",
        403: "没有访问权限。",
        404: "请求的内容不存在。",
        422: "输入不符合要求，请检查填写内容。",
        429: "请求过于频繁，请稍后重试。",
      };
      throw new ApiError(
        response.status,
        message ?? fallback[response.status] ?? "服务暂时不可用，请稍后重试。",
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
    createConversation: (title: string, signal?: AbortSignal) =>
      request<Conversation>("/conversations", "POST", { title }, signal),
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
