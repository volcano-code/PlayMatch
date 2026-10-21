"use client";
import { useEffect, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api, formatMoney, readableTime } from "@/lib/api";
import { notificationUrl } from "@/lib/notification-labels";
import type { NotificationList } from "@/lib/types";

export function NotificationInbox({
  ownerId,
  onLogin,
}: {
  ownerId: string | null;
  onLogin: () => void;
}) {
  const [page, setPage] = useState(0);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState("");
  const client = useQueryClient();
  const alive = useRef(true);
  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
    };
  }, []);
  const inbox = useQuery({
    queryKey: ["notifications", ownerId, page],
    enabled: Boolean(ownerId),
    queryFn: ({ signal }) =>
      api<NotificationList>(`/notifications?limit=20&offset=${page * 20}`, { signal }),
    retry: false,
    refetchInterval: 5000,
  });
  useEffect(() => {
    if (inbox.data && page > 0 && page * 20 >= inbox.data.total)
      setPage(Math.max(0, Math.ceil(inbox.data.total / 20) - 1));
  }, [inbox.data, page]);
  async function change(id: string, action: "read" | "delete") {
    setBusy(id);
    setError("");
    try {
      await api(`/notifications/${encodeURIComponent(id)}${action === "read" ? "/read" : ""}`, {
        method: action === "read" ? "POST" : "DELETE",
      });
    } catch (cause) {
      if (alive.current) setError(cause instanceof Error ? cause.message : "操作未确认，请刷新。");
    } finally {
      if (alive.current) {
        setBusy(null);
        await client.invalidateQueries({ queryKey: ["notifications", ownerId] });
      }
    }
  }
  return (
    <section className="saved-section" aria-labelledby="notification-heading">
      <div className="section-heading">
        <div>
          <span className="eyebrow">YOUR PRICE OBSERVATIONS</span>
          <h1 id="notification-heading">站内通知</h1>
          <p>只记录你明确开启、主动检查后命中的目标价。</p>
        </div>
        {ownerId && (
          <button
            className="button button-secondary"
            onClick={() => inbox.refetch()}
            disabled={inbox.isFetching}
          >
            刷新通知
          </button>
        )}
      </div>
      <div className="notice">
        <p>
          不持续抓价、不发送邮件。通知是历史观测，版本与激活资格未核验；购买前需重新核价。关闭提醒不会删除已生成的通知。
        </p>
      </div>
      {!ownerId ? (
        <div className="empty-state">
          <h3>登录后查看你的通知</h3>
          <button className="button button-primary" onClick={onLogin}>
            登录或注册
          </button>
        </div>
      ) : inbox.isPending ? (
        <p role="status">正在读取站内通知…</p>
      ) : inbox.isError ? (
        <div className="notice error" role="alert">
          <p>通知读取失败：{inbox.error.message}</p>
        </div>
      ) : (
        <>
          <p className="catalog-footnote" aria-live="polite">
            共 {inbox.data?.total || 0} 条 · 未读 {inbox.data?.unread || 0} 条
          </p>
          {inbox.data?.items.length === 0 && (
            <div className="empty-state">
              <h3>还没有站内通知</h3>
              <p>在收藏中设置 USD 目标价、明确开启提醒，再点击查价检查。</p>
            </div>
          )}
          <div className="notification-list">
            {inbox.data?.items.map((item) => (
              <article className="notification-card" key={item.id} data-testid="notification-card">
                <div className="section-heading compact">
                  <h3>{item.payload.game_title}</h3>
                  <span className="tag">{item.read_at ? "已读" : "未读"}</span>
                </div>
                <p className="notification-price">
                  观测报价 {formatMoney(item.payload.price_minor, "USD")}
                </p>
                <p>
                  目标价 {formatMoney(item.payload.target_minor, "USD")} · {item.payload.store}
                </p>
                <p className="fine-print">
                  观测于 {readableTime(item.payload.observed_at)} · 非当前价格保证 · 购买资格未核验
                </p>
                <div className="watch-actions">
                  {notificationUrl(item.payload.offer_url) && (
                    <a
                      className="text-button"
                      href={notificationUrl(item.payload.offer_url)}
                      target="_blank"
                      rel="noreferrer noopener"
                    >
                      前往商店重新核验
                    </a>
                  )}
                  {!item.read_at && (
                    <button
                      className="text-button"
                      disabled={busy !== null}
                      onClick={() => change(item.id, "read")}
                    >
                      标为已读
                    </button>
                  )}
                  <button
                    className="text-button"
                    disabled={busy !== null}
                    onClick={() => change(item.id, "delete")}
                  >
                    删除通知
                  </button>
                </div>
              </article>
            ))}
          </div>
          {(inbox.data?.total || 0) > 20 && (
            <div className="catalog-tabs" aria-label="通知分页">
              <button disabled={page === 0 || inbox.isFetching} onClick={() => setPage(page - 1)}>
                上一页通知
              </button>
              <span>第 {page + 1} 页</span>
              <button
                disabled={(page + 1) * 20 >= (inbox.data?.total || 0) || inbox.isFetching}
                onClick={() => setPage(page + 1)}
              >
                下一页通知
              </button>
            </div>
          )}
        </>
      )}
      {error && (
        <div className="notice error" role="alert">
          <p>{error}</p>
        </div>
      )}
    </section>
  );
}
