import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import { AuthForm } from "./AuthForm";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
});

it("sends the invitation only on registration and clears secrets after a rejected attempt", async () => {
  vi.stubEnv("VITE_REGISTRATION_REQUIRES_INVITE", "true");
  sessionStorage.clear();
  localStorage.clear();
  const fetchMock = vi.fn().mockResolvedValue(new Response(
    JSON.stringify({ detail: "Invalid invitation code" }), { status: 403 },
  ));
  vi.stubGlobal("fetch", fetchMock);
  render(<AuthForm onLogin={vi.fn()} notice="" />);
  expect(screen.queryByLabelText("邀请码")).toBeNull();
  await userEvent.click(screen.getByRole("button", { name: "没有账号，去注册" }));
  const invite = screen.getByLabelText("邀请码") as HTMLInputElement;
  expect(invite.required).toBe(true);
  await userEvent.type(screen.getByLabelText("邮箱"), "invited@example.com");
  await userEvent.type(screen.getByLabelText("密码"), "test-password");
  await userEvent.type(invite, "private-test-invitation");
  await userEvent.click(screen.getByRole("button", { name: "注册" }));
  await screen.findByText("邀请码无效，请向邀请人获取有效邀请码。");
  expect(JSON.parse(fetchMock.mock.calls[0][1].body)).toEqual({
    email: "invited@example.com", password: "test-password", invite_code: "private-test-invitation",
  });
  expect((screen.getByLabelText("密码") as HTMLInputElement).value).toBe("");
  expect(invite.value).toBe("");
  expect(sessionStorage.length).toBe(0);
  expect(localStorage.length).toBe(0);
  await userEvent.type(invite, "another-private-code");
  await userEvent.click(screen.getByRole("button", { name: "已有账号，去登录" }));
  fetchMock.mockResolvedValue(new Response(JSON.stringify({ access_token: "test-token" }), { status: 200 }));
  await userEvent.type(screen.getByLabelText("密码"), "test-password");
  await userEvent.click(screen.getByRole("button", { name: "登录" }));
  expect(JSON.parse(fetchMock.mock.calls[1][1].body)).toEqual({ email: "invited@example.com", password: "test-password" });
});
