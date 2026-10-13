import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import App from "./App";

const user = {
  id: "user-1",
  email: "test@example.com",
  is_active: true,
  created_at: "2026-10-12T09:00:00Z",
};
const conversations = [
  {
    id: "first",
    title: "First conversation",
    created_at: "2026-10-12T09:00:00Z",
    updated_at: "2026-10-12T09:00:00Z",
  },
  {
    id: "second",
    title: "Second conversation",
    created_at: "2026-10-12T09:00:00Z",
    updated_at: "2026-10-12T09:00:00Z",
  },
];
const credential = {
  provider: "openrouter",
  configured: true,
  created_at: "",
  updated_at: "",
};
const message = (id: string, content: string) => ({
  id,
  role: "assistant",
  content,
  created_at: "2026-10-12T09:00:00Z",
});
const json = (data: unknown, status = 200) =>
  new Response(JSON.stringify(data), { status });

function mockApi(
  extra?: (
    url: string,
    options: RequestInit,
  ) => Response | Promise<Response> | undefined,
) {
  const fetchMock = vi.fn((url: string, options: RequestInit) => {
    const custom = extra?.(url, options);
    if (custom) return Promise.resolve(custom);
    if (url === "/api/users/me") return Promise.resolve(json(user));
    if (url === "/api/conversations")
      return Promise.resolve(json(conversations));
    if (url === "/api/providers/openrouter")
      return Promise.resolve(json(credential));
    if (url.endsWith("/messages"))
      return Promise.resolve(json([message("m1", "Saved answer")]));
    throw new Error("Unexpected test route");
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

beforeEach(() => {
  sessionStorage.clear();
  localStorage.clear();
  sessionStorage.setItem("ai-workspace:token", "test-jwt");
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

it("restores the selected conversation and history after a page remount, then clears the session on logout", async () => {
  mockApi();
  sessionStorage.setItem("ai-workspace:user-1:conversation", "second");
  const page = render(<App />);
  await screen.findByText("Saved answer");
  expect(
    screen
      .getByRole("button", { name: /Second conversation/ })
      .getAttribute("aria-current"),
  ).toBe("page");
  page.unmount();
  render(<App />);
  await screen.findByText("Saved answer");
  await userEvent.click(screen.getByRole("button", { name: "退出登录" }));
  expect(sessionStorage.length).toBe(0);
  expect(localStorage.length).toBe(0);
  expect(screen.getByLabelText("密码")).toBeTruthy();
});

it("clears expired JWTs and shows login", async () => {
  mockApi((url) => (url === "/api/users/me" ? json({}, 401) : undefined));
  render(<App />);
  await screen.findByText("登录已失效，请重新登录。");
  expect(sessionStorage.getItem("ai-workspace:token")).toBeNull();
});

it("keeps newer history when a slow response arrives after switching conversations", async () => {
  let resolveFirst!: (response: Response) => void;
  const slow = new Promise<Response>((resolve) => {
    resolveFirst = resolve;
  });
  const fetchMock = mockApi((url) =>
    url.includes("/first/messages")
      ? slow
      : url.includes("/second/messages")
        ? json([message("new", "New history")])
        : undefined,
  );
  render(<App />);
  await waitFor(() =>
    expect(
      fetchMock.mock.calls.some(([url]) => url.includes("/first/messages")),
    ).toBe(true),
  );
  await userEvent.click(
    screen.getByRole("button", { name: /Second conversation/ }),
  );
  await screen.findByText("New history");
  await act(async () => {
    resolveFirst(json([message("old", "Old history")]));
  });
  expect(screen.queryByText("Old history")).toBeNull();
  expect(screen.getByText("New history")).toBeTruthy();
});

it("prevents duplicate sends, preserves drafts on failure, and renders model HTML as text", async () => {
  let resolveChat!: (response: Response) => void;
  const pending = new Promise<Response>((resolve) => {
    resolveChat = resolve;
  });
  const html = "<img src=x onerror=alert(1)>";
  const fetchMock = mockApi((url) =>
    url.endsWith("/chat")
      ? pending
      : url.endsWith("/messages")
        ? json([message("html", html)])
        : undefined,
  );
  const { container } = render(<App />);
  await screen.findByText(html);
  expect(container.querySelector(".message-content img")).toBeNull();
  await userEvent.type(screen.getByLabelText("消息"), "Keep this draft");
  const form = screen.getByLabelText("消息").closest("form")!;
  fireEvent.submit(form);
  fireEvent.submit(form);
  expect(
    fetchMock.mock.calls.filter(([url]) => url.endsWith("/chat")),
  ).toHaveLength(1);
  await act(async () => {
    resolveChat(json({ detail: "Chat provider request failed" }, 502));
  });
  await screen.findByText(/请先刷新历史确认结果/);
  expect((screen.getByLabelText("消息") as HTMLTextAreaElement).value).toBe(
    "Keep this draft",
  );
});

it("clears the API Key field after submission without putting it in browser storage", async () => {
  const fetchMock = mockApi();
  render(<App />);
  await screen.findByText("Saved answer");
  await screen.findByText("已配置");
  await userEvent.type(screen.getByLabelText("替换 API Key"), "dummy-test-key");
  await userEvent.click(screen.getByRole("button", { name: "保存密钥" }));
  await screen.findByText("API Key 已保存。");
  expect(
    (screen.getByLabelText("替换 API Key") as HTMLInputElement).value,
  ).toBe("");
  expect(JSON.stringify(sessionStorage)).not.toContain("dummy-test-key");
  expect(localStorage.length).toBe(0);
  const save = fetchMock.mock.calls.find(
    ([url, options]) =>
      url === "/api/providers/openrouter" && options.method === "PUT",
  );
  expect(JSON.parse(save![1].body as string)).toEqual({
    api_key: "dummy-test-key",
  });
});
