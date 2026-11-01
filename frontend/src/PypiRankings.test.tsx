import { StrictMode } from "react";
import { act, cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import App from "./App";
import { PypiRankings } from "./PypiRankings";
import { rankingFixture } from "./rankings.test-fixtures";

const json = (data: unknown, status = 200) => new Response(JSON.stringify(data), { status });
const date = "2026-11-01T08:00:00Z";
const owner = { id: "comparison-user", email: "compare@example.com", is_active: true, created_at: date };
const conversation = { id: "comparison", title: "PyPI 比较 · fastapi / flask", created_at: date, updated_at: date, learning_topic: null, learning_source: null };

beforeEach(() => { sessionStorage.clear(); localStorage.clear(); });
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

it("匿名访问只请求公共榜单，展示统计月、候选范围与缺失值", async () => {
  const fetchMock = vi.fn(() => Promise.resolve(json(rankingFixture)));
  vi.stubGlobal("fetch", fetchMock);
  render(<App />);
  await screen.findByText("400,000,000");
  expect(screen.getByText("2026-09")).toBeTruthy();
  expect(screen.getByText("本期未收录")).toBeTruthy();
  expect(screen.getByText("显示 5 / 5 个候选包")).toBeTruthy();
  expect(fetchMock).toHaveBeenCalledTimes(1);
  expect(fetchMock.mock.calls[0]).toEqual(["/api/rankings/pypi", expect.objectContaining({ headers: {}, credentials: "omit" })]);
  expect(screen.queryByLabelText("密码")).toBeNull();
  expect(sessionStorage.length).toBe(0);
});

it("搜索、分类和排序都使用同一份快照，名次不随筛选重排", async () => {
  const fetchMock = vi.fn(() => Promise.resolve(json(rankingFixture)));
  vi.stubGlobal("fetch", fetchMock);
  render(<PypiRankings onCompare={vi.fn()} />);
  await screen.findByText("400,000,000");
  await userEvent.click(screen.getByRole("button", { name: "Web 框架" }));
  expect(screen.getByText("显示 3 / 5 个候选包")).toBeTruthy();
  await userEvent.selectOptions(screen.getByLabelText("排序"), "downloads-asc");
  const first = screen.getAllByRole("row")[1];
  expect(within(first).getByRole("link", { name: "Django" })).toBeTruthy();
  expect(within(first).getByText("4")).toBeTruthy();
  await userEvent.type(screen.getByLabelText("搜索包名或用途"), "does-not-exist");
  expect(screen.getByText("没有匹配的候选包")).toBeTruthy();
  await userEvent.click(screen.getByRole("button", { name: "重置筛选" }));
  expect(screen.getByText("显示 5 / 5 个候选包")).toBeTruthy();
  expect(fetchMock).toHaveBeenCalledTimes(1);
});

it("最多选择三个包，跨筛选保留选择并可清空", async () => {
  vi.stubGlobal("fetch", vi.fn(() => Promise.resolve(json(rankingFixture))));
  const compare = vi.fn();
  render(<PypiRankings onCompare={compare} />);
  await userEvent.click(await screen.findByRole("checkbox", { name: "选择 FastAPI" }));
  await userEvent.click(screen.getByRole("checkbox", { name: "选择 Flask" }));
  await userEvent.click(screen.getByRole("checkbox", { name: "选择 LangChain" }));
  expect((screen.getByRole("checkbox", { name: "选择 Django" }) as HTMLInputElement).disabled).toBe(true);
  await userEvent.click(screen.getByRole("button", { name: "AI 与 Agent" }));
  await userEvent.click(screen.getByRole("button", { name: "让助手比较" }));
  expect(compare).toHaveBeenCalledWith({ projects: ["fastapi", "flask", "langchain"], period: "2026-09" });
  await userEvent.click(screen.getByRole("button", { name: "清空选择" }));
  expect(screen.queryByRole("button", { name: "让助手比较" })).toBeNull();
});

it("首次失败可重试，上游过期明确提示，重新读取失败仍保留已展示快照", async () => {
  const fetchMock = vi.fn().mockResolvedValueOnce(json({ detail: { code: "rankings_unavailable" } }, 503))
    .mockResolvedValueOnce(json({ ...rankingFixture, stale: true, warnings: ["上游暂时不可用，保留旧快照。"] }))
    .mockResolvedValueOnce(json({}, 503));
  vi.stubGlobal("fetch", fetchMock);
  render(<PypiRankings onCompare={vi.fn()} />);
  await screen.findByText("暂时无法取得排行榜数据，请稍后重试。");
  await userEvent.click(screen.getByRole("button", { name: "重新读取" }));
  await screen.findByText("上游暂时不可用，保留旧快照。");
  await userEvent.click(screen.getByRole("button", { name: "重新读取" }));
  await screen.findByText(/当前保留上次读取的数据/);
  expect(screen.getByText("400,000,000")).toBeTruthy();
});

it.each([false, true])("匿名比较登录后只预填，失败可手动重试，切页保留草稿，退出清理（首次失败：%s）", async (failFirst) => {
  let createAttempts = 0;
  const fetchMock = vi.fn((url: string, options: RequestInit) => {
    if (url === "/api/rankings/pypi") return Promise.resolve(json(rankingFixture));
    if (url === "/api/auth/login") return Promise.resolve(json({ access_token: "comparison-token", token_type: "bearer" }));
    if (url === "/api/users/me") return Promise.resolve(json(owner));
    if (url === "/api/conversations") {
      if (options.method !== "POST") return Promise.resolve(json([]));
      createAttempts++;
      return Promise.resolve(failFirst && createAttempts === 1 ? json({}, 503) : json(conversation));
    }
    if (url.endsWith("/messages")) return Promise.resolve(json([]));
    if (url === "/api/providers/openrouter") return Promise.resolve(json({ provider: "openrouter", configured: true, created_at: date, updated_at: date }));
    throw new Error(`Unexpected route: ${url}`);
  });
  vi.stubGlobal("fetch", fetchMock);
  render(<StrictMode><App /></StrictMode>);
  await userEvent.click(await screen.findByRole("checkbox", { name: "选择 FastAPI" }));
  await userEvent.click(screen.getByRole("checkbox", { name: "选择 Flask" }));
  await userEvent.click(screen.getByRole("button", { name: "让助手比较" }));
  await userEvent.type(screen.getByLabelText("邮箱"), owner.email);
  await userEvent.type(screen.getByLabelText("密码"), "test-password");
  await userEvent.click(screen.getByRole("button", { name: "登录" }));
  if (failFirst) await userEvent.click(await screen.findByRole("button", { name: "重试比较" }));
  const draft = await screen.findByRole("textbox", { name: "消息" });
  await waitFor(() => expect((draft as HTMLTextAreaElement).value).toContain("fastapi、flask"));
  expect((draft as HTMLTextAreaElement).value).toContain("2026-09");
  const creations = fetchMock.mock.calls.filter(([url, options]) => url === "/api/conversations" && options.method === "POST");
  expect(creations).toHaveLength(failFirst ? 2 : 1);
  expect(JSON.parse(creations[0][1].body as string)).toEqual({ title: conversation.title });
  expect(fetchMock.mock.calls.some(([url]) => url.endsWith("/chat"))).toBe(false);
  await userEvent.clear(draft);
  await userEvent.type(draft, "这是我的私人草稿");
  await userEvent.click(screen.getByRole("button", { name: "PyPI 排行榜" }));
  await userEvent.click(screen.getByRole("button", { name: "助手" }));
  expect((screen.getByRole("textbox", { name: "消息" }) as HTMLTextAreaElement).value).toBe("这是我的私人草稿");
  await userEvent.click(screen.getByRole("button", { name: "退出登录" }));
  expect(screen.queryByLabelText("消息")).toBeNull();
  expect(JSON.stringify(sessionStorage)).not.toContain("私人草稿");
  expect(sessionStorage.length).toBe(0);
  expect(localStorage.length).toBe(0);
});

it("旧页面迟到的请求不会覆盖重新挂载的榜单", async () => {
  let finish!: (response: Response) => void;
  const slow = new Promise<Response>((resolve) => { finish = resolve; });
  vi.stubGlobal("fetch", vi.fn().mockReturnValueOnce(slow).mockResolvedValueOnce(json(rankingFixture)));
  const first = render(<PypiRankings onCompare={vi.fn()} />);
  first.unmount();
  render(<PypiRankings onCompare={vi.fn()} />);
  await screen.findByText("2026-09");
  await act(async () => { finish(json({ ...rankingFixture, period: "2025-01" })); });
  expect(screen.queryByText("2025-01")).toBeNull();
});
