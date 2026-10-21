"use client";

import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { steamQuery, steamStatusLabel } from "@/lib/steam-directory";
import type { SteamDirectoryPage, SteamDirectoryStatus } from "@/lib/steam-directory";

function observedTime(value: number | null) {
  return value ? new Date(value * 1000).toLocaleString("zh-CN") : "尚未完成";
}

export function SteamDirectoryPanel() {
  const [expanded, setExpanded] = useState(false);
  const [draft, setDraft] = useState("");
  const [query, setQuery] = useState("");
  const [cursors, setCursors] = useState([0]);
  const cursor = cursors[cursors.length - 1];
  const status = useQuery({
    queryKey: ["steam-directory-status"],
    queryFn: () => api<SteamDirectoryStatus>("/catalog/steam/status"),
    staleTime: 15_000,
    refetchInterval: 30_000,
  });
  const games = useQuery({
    queryKey: ["steam-directory", query, cursor],
    queryFn: () => api<SteamDirectoryPage>(steamQuery(query, cursor)),
    enabled: expanded,
    staleTime: 30_000,
  });
  return (
    <section className="steam-directory" aria-label="Steam 自动同步目录">
      <div className="steam-directory-heading">
        <div>
          <strong>Steam 自动同步目录</strong>
          <p>覆盖发现与推荐资料分开统计，不以名称条数冒充完整推荐库。</p>
        </div>
        <button
          className="text-button"
          onClick={() => {
            status.refetch();
            if (expanded) games.refetch();
          }}
        >
          刷新同步状态
        </button>
      </div>
      {status.isPending ? (
        <p role="status">正在读取同步状态…</p>
      ) : status.isError ? (
        <p role="alert">同步状态暂时不可用，请重试。已有推荐目录仍可使用。</p>
      ) : (
        <>
          <p role="status">
            <strong>{steamStatusLabel(status.data.status)}</strong>
          </p>
          <div className="steam-directory-metrics">
            <span>已发现 {status.data.discovered_games.toLocaleString()} 款</span>
            <span>已有推荐资料 {status.data.with_recommendation_metadata.toLocaleString()} 款</span>
            <span>资料待补全 {status.data.metadata_pending.toLocaleString()} 款</span>
          </div>
          <p>
            最近成功：{observedTime(status.data.last_success_at)} · 最近全量：
            {observedTime(status.data.last_full_success_at)}
          </p>
          <p>
            本轮已提交 {status.data.pages_committed} 页 /{" "}
            {status.data.rows_seen_in_cycle.toLocaleString()} 条。上游更新存在延迟，不承诺秒级同步。
          </p>
          {!status.data.configured && (
            <p>需由部署者配置服务端 Steam Key 并启动 catalog-sync。不要把密钥输入网页。</p>
          )}
          {status.data.last_error && (
            <p>最近错误：{status.data.last_error}。失败不推进同步完成时间。</p>
          )}
        </>
      )}
      <button
        className="text-button"
        aria-expanded={expanded}
        onClick={() => setExpanded(!expanded)}
      >
        {expanded ? "收起 Steam 目录" : "浏览 Steam 发现目录"}
      </button>
      {expanded && (
        <div>
          <form
            className="steam-directory-search"
            onSubmit={(event) => {
              event.preventDefault();
              setQuery(draft.trim());
              setCursors([0]);
            }}
          >
            <label htmlFor="steam-name-search">游戏名称前缀或精确 AppID</label>
            <input
              id="steam-name-search"
              maxLength={100}
              value={draft}
              onChange={(event) => setDraft(event.target.value)}
              placeholder="例如 Portal 或 620"
            />
            <button className="text-button" type="submit">
              搜索 Steam 目录
            </button>
          </form>
          <p>此处不猜测中文、玩法或购买资格；资料待补全的游戏暂不进入主题推荐。</p>
          {games.isPending ? (
            <p role="status">正在查询数据库…</p>
          ) : games.isError ? (
            <p role="alert">
              目录读取失败。
              <button className="text-button" onClick={() => games.refetch()}>
                重试 Steam 目录
              </button>
            </p>
          ) : !games.data.items.length ? (
            <p>没有匹配的已同步记录。首次同步前，此处为空。</p>
          ) : (
            <ul className="steam-directory-list">
              {games.data.items.map((game) => (
                <li key={game.appid}>
                  <div>
                    <strong>{game.name}</strong>
                    <small>
                      AppID {game.appid} ·{" "}
                      {game.metadata_status === "available" ? "已有推荐资料" : "资料待补全"}
                    </small>
                  </div>
                  <a
                    href={`https://store.steampowered.com/app/${game.appid}/`}
                    target="_blank"
                    rel="noopener noreferrer"
                  >
                    官方商店
                  </a>
                </li>
              ))}
            </ul>
          )}
          <nav aria-label="Steam 目录分页" className="steam-directory-search">
            <button
              className="text-button"
              disabled={cursors.length === 1 || games.isFetching}
              onClick={() => setCursors(cursors.slice(0, -1))}
            >
              上一批
            </button>
            <button
              className="text-button"
              disabled={!games.data?.next_appid || games.isFetching || games.isError}
              onClick={() => {
                if (games.data?.next_appid) setCursors([...cursors, games.data.next_appid]);
              }}
            >
              下一批
            </button>
          </nav>
        </div>
      )}
    </section>
  );
}
