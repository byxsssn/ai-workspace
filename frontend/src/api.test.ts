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
});
