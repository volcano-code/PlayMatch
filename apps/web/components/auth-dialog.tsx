"use client";
import { useState, type FormEvent } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { ArrowRight, LoaderCircle, LockKeyhole } from "lucide-react";
import { api } from "@/lib/api";
import type { User } from "@/lib/types";
import { Modal } from "./modal";

export function AuthDialog({
  open,
  onClose,
  onSuccess,
}: {
  open: boolean;
  onClose: () => void;
  onSuccess: () => void;
}) {
  const [mode, setMode] = useState<"login" | "register">("login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const client = useQueryClient();
  const mutation = useMutation({
    mutationFn: () =>
      api<User>(`/auth/${mode}`, {
        method: "POST",
        body: JSON.stringify({ email: email.trim(), password }),
      }),
    onSuccess: (user) => {
      client.setQueryData(["me"], user);
      client.invalidateQueries({ queryKey: ["watchlist"] });
      client.invalidateQueries({ queryKey: ["feedback"] });
      setPassword("");
      onClose();
      onSuccess();
    },
  });
  function submit(event: FormEvent) {
    event.preventDefault();
    mutation.mutate();
  }
  return (
    <Modal
      open={open}
      onClose={onClose}
      title={mode === "login" ? "继续你的冒险" : "建立你的游戏清单"}
    >
      <p className="muted auth-intro">保存心动的游戏，让下一次推荐更了解你的喜好。</p>
      <div className="segmented" role="group" aria-label="账户操作">
        <button
          className={mode === "login" ? "selected" : ""}
          onClick={() => {
            setMode("login");
            mutation.reset();
          }}
        >
          登录
        </button>
        <button
          className={mode === "register" ? "selected" : ""}
          onClick={() => {
            setMode("register");
            mutation.reset();
          }}
        >
          注册
        </button>
      </div>
      <form className="stack-form" onSubmit={submit}>
        <label>
          邮箱
          <input
            type="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            required
            autoComplete="email"
            placeholder="you@example.com"
            maxLength={254}
          />
        </label>
        <label>
          密码
          <input
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            required
            minLength={10}
            maxLength={128}
            autoComplete={mode === "register" ? "new-password" : "current-password"}
            placeholder="至少 10 位字符"
          />
        </label>
        {mutation.isError && (
          <p className="error-message" role="alert">
            {mutation.error.message}
          </p>
        )}
        <button className="button button-primary full" type="submit" disabled={mutation.isPending}>
          {mutation.isPending ? (
            <LoaderCircle className="spin" size={17} />
          ) : (
            <ArrowRight size={17} />
          )}
          {mutation.isPending ? "正在提交…" : mode === "login" ? "登录账户" : "创建账户"}
        </button>
      </form>
      <p className="fine-print">
        <LockKeyhole size={13} />
        密码仅通过账户接口提交，会话由安全 Cookie 管理。
      </p>
    </Modal>
  );
}
