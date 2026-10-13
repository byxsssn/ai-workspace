import { afterEach, describe, expect, it, vi } from "vitest";
import { createApi } from "./api";

afterEach(() => vi.unstubAllGlobals());

describe("API boundary", () => {
  it("uses same-origin proxy, bearer auth and the exact non-streaming chat contract", async () => {
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

  it("does not display reflected secrets in validation errors", async () => {
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

  it("invalidates authenticated sessions on 401, without retrying a request", async () => {
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
