import { afterEach, describe, expect, it, vi } from "vitest";
import { createApi } from "./api";

afterEach(() => vi.unstubAllGlobals());

describe("API 请求边界", () => {
  it("使用同源代理、Bearer 鉴权和约定的非流式聊天字段", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValue(
        new Response(
          JSON.stringify({ content: "Answer", model: "model", usage: null }),
        ),
      );
    vi.stubGlobal("fetch", fetchMock);
    await createApi("test-jwt").chat(
      "conversation-id",
      "provider/model",
      "  original\ncontent  ",
    );
    const [url, options] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/conversations/conversation-id/chat");
    expect(options.headers.Authorization).toBe("Bearer test-jwt");
    expect(JSON.parse(options.body)).toEqual({
      model: "provider/model",
      content: "  original\ncontent  ",
    });
    expect(options).toMatchObject({
      cache: "no-store",
      credentials: "omit",
      redirect: "error",
    });
  });

  it("不展示校验错误中回显的密钥", async () => {
    vi.stubGlobal(
      "fetch",
      vi
        .fn()
        .mockResolvedValue(
          new Response(
            JSON.stringify({ detail: [{ input: "secret-test-key" }] }),
            { status: 422 },
          ),
        ),
    );
    await expect(
      createApi("test-jwt").saveCredential("secret-test-key"),
    ).rejects.toThrow("输入不符合要求");
  });

  it("遇到 401 清除登录状态且不重试请求", async () => {
    const expired = vi.fn();
    const fetchMock = vi
      .fn()
      .mockResolvedValue(new Response("{}", { status: 401 }));
    vi.stubGlobal("fetch", fetchMock);
    await expect(
      createApi("test-jwt", expired).conversations(),
    ).rejects.toThrow("登录已过期");
    expect(expired).toHaveBeenCalledOnce();
    expect(fetchMock).toHaveBeenCalledOnce();
  });

  it("Notion 权限错误使用固定文案，不暴露上游内容或清除应用登录", async () => {
    const expired = vi.fn();
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({ detail: { code: "source_forbidden", message: "private-token-body" } }), { status: 403 })));
    await expect(createApi("test-jwt", expired).learningRecords()).rejects.toThrow("无法读取来源");
    expect(expired).not.toHaveBeenCalled();
  });

  it("详情请求编码记录 ID，显式选择来源并传递取消信号", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response("{}"));
    vi.stubGlobal("fetch", fetchMock);
    const controller = new AbortController();
    await createApi("test-jwt").learningRecord("id/with?chars", "demo", controller.signal);
    expect(fetchMock.mock.calls[0][0]).toBe("/api/learning/records/id%2Fwith%3Fchars?source=demo");
    expect(fetchMock.mock.calls[0][1].signal).toBe(controller.signal);
  });

  it.each([
    ["conversation_changed", "会话历史已变化"],
    ["conversation_context_conflict", "会话的学习上下文与请求不一致"],
  ])("显示统一聊天接口的 %s 错误", async (code, message) => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({ detail: { code, message: "private-upstream" } }), { status: 409 })));
    await expect(createApi("test-jwt").chat("id", "model", "question")).rejects.toThrow(message);
  });
});
