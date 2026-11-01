import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import App from "./App";
import { rankingFixture } from "./rankings.test-fixtures";
import type { Conversation, LearningRecord, LearningSource } from "./types";

const date = "2026-11-01T08:00:00Z";
const user = { id: "learner", email: "learner@example.com", is_active: true, created_at: date };
const credential = { provider: "openrouter", configured: true, created_at: date, updated_at: date };
const conversation = (id: string, source: LearningSource | null = null, updated_at = date): Conversation => ({
  id, title: `${id} 会话`, created_at: date, updated_at,
  learning_topic: source ? "fastapi" : null, learning_source: source,
});
const record = (source: LearningSource): LearningRecord => ({
  id: "00000000-0000-0000-0000-000000000001", topic_id: "fastapi", topic_name: "FastAPI", category: "后端框架",
  priority: null, source, source_url: source === "notion" ? "https://www.notion.so/example" : null,
  source_updated_at: date, fetched_at: date, learning_status: "学习中", current_progress: "私人进度只在内存中",
  next_step: "练习异步测试", proficiency: "看过", project_practice: false,
  official_docs_url: "https://fastapi.tiangolo.com/", evidence_url: null, warnings: [],
});
const json = (data: unknown, status = 200) => new Response(JSON.stringify(data), { status });

function setup(initial: Conversation[] = []) {
  let conversations = initial;
  let answer = "";
  const fetchMock = vi.fn((url: string, options: RequestInit) => {
    if (url === "/api/rankings/pypi") return Promise.resolve(json(rankingFixture));
    if (url === "/api/users/me") return Promise.resolve(json(user));
    if (url === "/api/providers/openrouter") return Promise.resolve(json(credential));
    if (url.startsWith("/api/learning/records?")) {
      const source = url.includes("source=demo") ? "demo" : "notion";
      return Promise.resolve(json({ source, items: [record(source)], fetched_at: date, has_more: false, warnings: [] }));
    }
    if (url === "/api/conversations") {
      if (options.method === "POST") {
        const body = JSON.parse(options.body as string);
        const created = { ...conversation("created", body.learning_source ?? null), title: body.title };
        conversations = [created, ...conversations];
        return Promise.resolve(json(created, 201));
      }
      return Promise.resolve(json(conversations));
    }
    if (url.endsWith("/messages")) return Promise.resolve(json(answer ? [{ id: "answer", role: "assistant", content: answer, created_at: date }] : []));
    if (url.endsWith("/chat")) {
      answer = "这是已保存的讲解。\n来源：https://fastapi.tiangolo.com/advanced/async-tests/";
      return Promise.resolve(json({
        content: answer, model: "openai/gpt-6-luna", usage: { prompt_tokens: 100, completion_tokens: 40, total_tokens: 140 },
        source: "notion", status: "answered",
        sources: [{ source_id: "doc:async-tests", kind: "official_document", title: "Async Tests", url: "https://fastapi.tiangolo.com/advanced/async-tests/", fetched_at: date, content_sha256: "abc", truncated: false }],
        tool_calls: [{ tool: "read_official_doc", section_id: "async-tests", status: "success", error_code: null, cached: false }],
      }));
    }
    throw new Error(`Unexpected test route: ${url}`);
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

beforeEach(() => { sessionStorage.clear(); localStorage.clear(); sessionStorage.setItem("ai-workspace:token", "test-jwt"); });
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

it("默认公开榜单，主动进入地图后读取学习记录，不提前发送模型请求", async () => {
  const fetchMock = setup([conversation("ordinary")]);
  sessionStorage.setItem("ai-workspace:learner:conversation", "ordinary");
  render(<App />);
  await screen.findByRole("heading", { name: "精选 Python 包月度下载榜" });
  expect(fetchMock.mock.calls.some(([url]) => url.startsWith("/api/learning/records"))).toBe(false);
  await userEvent.click(screen.getByRole("button", { name: "学习地图" }));
  await screen.findByRole("heading", { name: "FastAPI" });
  expect(screen.getByRole("button", { name: "学习地图" }).getAttribute("aria-pressed")).toBe("true");
  expect(screen.queryByRole("textbox", { name: "消息" })).toBeNull();
  expect(fetchMock.mock.calls.some(([url]) => /\/messages$|\/chat$/.test(url))).toBe(false);
  expect(localStorage.length).toBe(0);
  expect(JSON.stringify(sessionStorage)).not.toContain("私人进度");
});

it("FastAPI 入口新建持久化上下文，只预填问题不自动付费发送", async () => {
  const fetchMock = setup();
  render(<App />);
  await userEvent.click(await screen.findByRole("button", { name: "学习地图" }));
  await userEvent.click(await screen.findByRole("button", { name: "继续学习 FastAPI" }));
  const input = await screen.findByRole("textbox", { name: "消息" });
  expect((input as HTMLTextAreaElement).value).toBe("根据我的学习进度，帮我继续学习 FastAPI。");
  expect(screen.getByText("Notion 学习记录")).toBeTruthy();
  const create = fetchMock.mock.calls.filter(([url, options]) => url === "/api/conversations" && options.method === "POST");
  expect(create).toHaveLength(1);
  expect(JSON.parse(create[0][1].body as string)).toEqual({ title: "FastAPI 学习 · Notion", learning_topic: "fastapi", learning_source: "notion" });
  expect(fetchMock.mock.calls.filter(([url]) => url.endsWith("/chat"))).toHaveLength(0);
});

it("优先续接同来源最近的学习会话，不混用演示和 Notion 上下文", async () => {
  const fetchMock = setup([
    conversation("demo-latest", "demo", "2026-11-01T10:00:00Z"),
    conversation("notion-older", "notion", "2026-10-30T08:00:00Z"),
    conversation("notion-latest", "notion"),
  ]);
  render(<App />);
  await userEvent.click(await screen.findByRole("button", { name: "学习地图" }));
  await userEvent.click(await screen.findByRole("button", { name: "继续学习 FastAPI" }));
  await screen.findByRole("heading", { name: "notion-latest 会话" });
  expect(fetchMock.mock.calls.some(([url]) => url === "/api/conversations/notion-latest/messages")).toBe(true);
  expect(fetchMock.mock.calls.some(([, options]) => options.method === "POST")).toBe(false);
  await userEvent.click(screen.getByRole("button", { name: "学习地图" }));
  await userEvent.selectOptions(screen.getByLabelText("记录来源"), "demo");
  await screen.findByRole("heading", { name: "FastAPI" });
  await userEvent.click(screen.getByRole("button", { name: "继续学习 FastAPI" }));
  await screen.findByRole("heading", { name: "demo-latest 会话" });
  expect(screen.getByText("虚构演示记录")).toBeTruthy();
  expect(fetchMock.mock.calls.some(([, options]) => options.method === "POST")).toBe(false);
});

it("学习与普通聊天共用原发送接口，刷新后仍有来源文字和上下文标签", async () => {
  const fetchMock = setup();
  const page = render(<App />);
  await userEvent.click(await screen.findByRole("button", { name: "学习地图" }));
  await userEvent.click(await screen.findByRole("button", { name: "继续学习 FastAPI" }));
  await waitFor(() => expect((screen.getByRole("button", { name: "发送" }) as HTMLButtonElement).disabled).toBe(false));
  await userEvent.click(screen.getByRole("button", { name: "发送" }));
  await screen.findByText(/这是已保存的讲解/);
  expect(screen.getByRole("link", { name: /Async Tests/ }).getAttribute("href")).toBe("https://fastapi.tiangolo.com/advanced/async-tests/");
  expect(screen.getByText(/本轮资料读取 · 1 次/)).toBeTruthy();
  const chatCalls = fetchMock.mock.calls.filter(([url]) => url.endsWith("/chat"));
  expect(chatCalls).toHaveLength(1);
  expect(chatCalls[0][0]).toBe("/api/conversations/created/chat");
  expect(JSON.parse(chatCalls[0][1].body as string)).toEqual({ model: "openai/gpt-6-luna", content: "根据我的学习进度，帮我继续学习 FastAPI。" });
  page.unmount();
  render(<App />);
  await userEvent.click(await screen.findByRole("button", { name: "助手" }));
  await screen.findByText(/这是已保存的讲解/);
  expect(screen.getByText("Notion 学习记录")).toBeTruthy();
  expect(screen.queryByText(/本轮资料读取/)).toBeNull();
  expect((screen.getByLabelText("消息") as HTMLTextAreaElement).value).toBe("");
  expect(fetchMock.mock.calls.filter(([url]) => url.endsWith("/chat"))).toHaveLength(1);
  expect(JSON.stringify(sessionStorage)).not.toContain("这是已保存的讲解");
  expect(localStorage.length).toBe(0);
});

it("仍能从同一会话列表新建普通聊天，不附带学习上下文", async () => {
  const fetchMock = setup();
  render(<App />);
  await userEvent.click(await screen.findByRole("button", { name: "助手" }));
  await userEvent.type(screen.getByLabelText("会话标题"), "普通讨论");
  await userEvent.click(screen.getByRole("button", { name: "新建会话" }));
  await screen.findByRole("heading", { name: "普通讨论" });
  const create = fetchMock.mock.calls.find(([url, options]) => url === "/api/conversations" && options.method === "POST");
  expect(JSON.parse(create![1].body as string)).toEqual({ title: "普通讨论" });
  expect(screen.getByText("通用助手")).toBeTruthy();
  expect((screen.getByLabelText("消息") as HTMLTextAreaElement).value).toBe("");
});
