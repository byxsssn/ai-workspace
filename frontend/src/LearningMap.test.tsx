import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import { createApi } from "./api";
import { LearningMap } from "./LearningMap";
import type { LearningRecord, LearningRecordList, LearningSource } from "./types";

const date = "2026-11-01T09:00:00Z";
const fastapi: LearningRecord = {
  id: "00000000-0000-0000-0000-000000000001", topic_id: "fastapi", topic_name: "FastAPI",
  category: "后端框架", priority: "优先", source: "notion", source_url: "https://www.notion.so/fastapi",
  source_updated_at: date, fetched_at: date, learning_status: "学习中", current_progress: "读完基础测试",
  next_step: "写一个异步测试", proficiency: "看过", project_practice: false,
  official_docs_url: "https://fastapi.tiangolo.com/", evidence_url: null, warnings: [],
};
const redis: LearningRecord = {
  ...fastapi, id: "00000000-0000-0000-0000-000000000002", topic_id: null, topic_name: "Redis",
  category: "数据库", priority: null, learning_status: "待学习", current_progress: null, next_step: null,
  proficiency: null, project_practice: null,
};
const json = (data: unknown, status = 200) => new Response(JSON.stringify(data), { status });
const list = (source: LearningSource = "notion", items = [fastapi, redis]): LearningRecordList => ({
  source, items: items.map((record) => ({ ...record, source })), fetched_at: date, has_more: false, warnings: [],
});
function mount(onUnauthorized = vi.fn()) {
  const onContinue = vi.fn();
  const page = render(<LearningMap api={createApi("test", onUnauthorized)} busy={false} entryError="" onContinue={onContinue} onSourceChange={vi.fn()} />);
  return { ...page, onContinue };
}
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

it("默认读取 Notion，按真实类型和状态筛选且不请求全部详情", async () => {
  const fetchMock = vi.fn().mockResolvedValue(json(list()));
  vi.stubGlobal("fetch", fetchMock);
  mount();
  await screen.findByRole("heading", { name: "FastAPI" });
  expect(fetchMock).toHaveBeenCalledOnce();
  expect(fetchMock.mock.calls[0][0]).toBe("/api/learning/records?source=notion");
  expect(screen.getByRole("region", { name: "后端框架主题" })).toBeTruthy();
  expect(screen.getByText("实践情况未知")).toBeTruthy();
  await userEvent.selectOptions(screen.getByLabelText("类型"), "数据库");
  expect(screen.queryByRole("heading", { name: "FastAPI" })).toBeNull();
  expect(screen.getByRole("heading", { name: "Redis" })).toBeTruthy();
  await userEvent.selectOptions(screen.getByLabelText("学习状态"), "学习中");
  expect(screen.getByText("没有匹配的主题")).toBeTruthy();
  await userEvent.click(screen.getByRole("button", { name: "清除筛选" }));
  await userEvent.type(screen.getByLabelText("搜索主题"), "异步测试");
  expect(screen.getByRole("heading", { name: "FastAPI" })).toBeTruthy();
  expect(screen.queryByRole("heading", { name: "Redis" })).toBeNull();
});

it("详情显示完整性和原文时间，笔记按文本呈现并拒绝危险链接", async () => {
  const html = '<img src=x onerror="alert(1)">';
  vi.stubGlobal("fetch", vi.fn((url: string) => Promise.resolve(json(url.includes(`${redis.id}?`) ? {
    ...redis, official_docs_url: "javascript:alert(1)", evidence_url: "data:text/html,secret",
    notes_markdown: html, notes_complete: false,
  } : list()))));
  const { container, onContinue } = mount();
  await userEvent.click(await screen.findByRole("button", { name: "查看 Redis 详情" }));
  await screen.findByText(html);
  expect(container.querySelector(".notes-content img")).toBeNull();
  expect(screen.queryByRole("link", { name: /官方文档/ })).toBeNull();
  expect(screen.queryByRole("link", { name: /实践成果/ })).toBeNull();
  expect(screen.getByRole("link", { name: /Notion 原文/ }).getAttribute("rel")).toBe("noopener noreferrer");
  expect(screen.getByText(/笔记未完整读取/)).toBeTruthy();
  expect(screen.getByText(/当前学习助手先支持 FastAPI/)).toBeTruthy();
  expect(screen.getByText(/原文更新/)).toBeTruthy();
  expect(onContinue).not.toHaveBeenCalled();
});

it("Notion 配置失败保持明确错误，只在用户选择后使用演示数据", async () => {
  const fetchMock = vi.fn((url: string) => Promise.resolve(url.includes("source=notion") ?
    json({ detail: { code: "notion_catalog_not_configured", message: "private-upstream-text" } }, 503) : json(list("demo"))));
  vi.stubGlobal("fetch", fetchMock);
  mount();
  await screen.findByText(/尚未配置 Notion 学习数据库/);
  expect(screen.queryByText("private-upstream-text")).toBeNull();
  expect(screen.queryByRole("heading", { name: "FastAPI" })).toBeNull();
  expect(fetchMock).toHaveBeenCalledOnce();
  await userEvent.selectOptions(screen.getByLabelText("记录来源"), "demo");
  await screen.findByRole("heading", { name: "FastAPI" });
  expect(screen.getByText(/正在查看虚构演示数据/)).toBeTruthy();
  expect(fetchMock.mock.calls[1][0]).toBe("/api/learning/records?source=demo");
});

it("切换来源后丢弃迟到列表，并中止旧请求", async () => {
  let resolveNotion!: (response: Response) => void;
  const pending = new Promise<Response>((resolve) => { resolveNotion = resolve; });
  const fetchMock = vi.fn((url: string) => url.includes("source=notion") ? pending : Promise.resolve(json(list("demo", [{ ...fastapi, topic_name: "演示 FastAPI" }]))));
  vi.stubGlobal("fetch", fetchMock);
  mount();
  await userEvent.selectOptions(screen.getByLabelText("记录来源"), "demo");
  await screen.findByRole("heading", { name: "演示 FastAPI" });
  await act(async () => { resolveNotion(json(list("notion", [{ ...fastapi, topic_name: "迟到的私人记录" }]))); });
  expect(screen.queryByText("迟到的私人记录")).toBeNull();
  expect(screen.getByRole("heading", { name: "演示 FastAPI" })).toBeTruthy();
  expect((fetchMock.mock.calls[0] as unknown as [string, RequestInit])[1].signal?.aborted).toBe(true);
});

it("切换详情后忽略迟到笔记，来源切换也清除旧详情", async () => {
  let resolveDetail!: (response: Response) => void;
  const pending = new Promise<Response>((resolve) => { resolveDetail = resolve; });
  const fetchMock = vi.fn((url: string) => {
    if (url.includes(`${fastapi.id}?`)) return pending;
    if (url.includes(`${redis.id}?`)) return Promise.resolve(json({ ...redis, notes_markdown: "Redis 笔记", notes_complete: true }));
    return Promise.resolve(json(list(url.includes("source=demo") ? "demo" : "notion")));
  });
  vi.stubGlobal("fetch", fetchMock);
  mount();
  await userEvent.click(await screen.findByRole("button", { name: "查看 FastAPI 详情" }));
  await userEvent.click(screen.getByRole("button", { name: "查看 Redis 详情" }));
  await screen.findByText("Redis 笔记");
  await act(async () => { resolveDetail(json({ ...fastapi, notes_markdown: "迟到笔记", notes_complete: true })); });
  expect(screen.queryByText("迟到笔记")).toBeNull();
  await userEvent.selectOptions(screen.getByLabelText("记录来源"), "demo");
  await waitFor(() => expect(screen.queryByText("Redis 笔记")).toBeNull());
  expect(screen.queryByRole("complementary", { name: "学习记录详情" })).toBeNull();
});

it("部分列表明确提示覆盖范围，不将条目数当作总量或完成率", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(json({ ...list(), has_more: true, warnings: ["record_limit_reached"] })));
  mount();
  await screen.findByText(/当前列表不是完整学习地图/);
  expect(screen.getByText("已读取主题")).toBeTruthy();
  expect(screen.queryByText(/\d+%/)).toBeNull();
});

it("演示标记不被误报为资料不完整", async () => {
  vi.stubGlobal("fetch", vi.fn((url: string) => Promise.resolve(json(url.includes(`${fastapi.id}?`) ? {
    ...fastapi, source: "demo", notes_markdown: "虚构笔记", notes_complete: true, warnings: ["demo_data_not_personal_progress"],
  } : { ...list(url.includes("source=demo") ? "demo" : "notion"), warnings: ["demo_data_not_personal_progress"] }))));
  mount();
  await userEvent.selectOptions(screen.getByLabelText("记录来源"), "demo");
  await userEvent.click(await screen.findByRole("button", { name: "查看 FastAPI 详情" }));
  await screen.findByText("虚构笔记");
  expect(screen.getByText(/正在查看虚构演示数据/)).toBeTruthy();
  expect(screen.queryByText(/部分记录或字段可能不完整/)).toBeNull();
  expect(screen.queryByText(/部分字段可能缺失/)).toBeNull();
});
