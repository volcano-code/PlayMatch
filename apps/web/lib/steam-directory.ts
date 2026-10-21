export type SteamDirectoryStatus = {
  status: string;
  enabled: boolean;
  configured: boolean;
  worker_recent: boolean;
  discovered_games: number;
  with_recommendation_metadata: number;
  metadata_pending: number;
  last_success_at: number | null;
  last_full_success_at: number | null;
  cycle_mode: string;
  pages_committed: number;
  rows_seen_in_cycle: number;
  cursor: number;
  last_error: string | null;
  poll_interval_seconds: number;
  automatic_metadata_enrichment: false;
  automatic_price_polling: false;
};
export type SteamDirectoryPage = {
  items: {
    appid: number;
    name: string;
    store_url: string;
    observed_at: number;
    source_modified: number | null;
    metadata_status: "available" | "pending";
    game_id: string | null;
  }[];
  next_appid: number | null;
  search_mode: "name_prefix_or_exact_appid";
};
export function steamStatusLabel(status: string): string {
  const labels: Record<string, string> = {
    disabled: "同步未启用",
    not_configured: "未配置 Steam Key",
    worker_not_running: "未检测到同步进程心跳",
    backoff: "上游失败或额度不足，等待重试",
    syncing: "正在分批同步",
    never_synced: "尚未完成首次同步",
    stale: "同步已滞后",
    up_to_date: "近期已完成接口遍历",
  };
  return labels[status] ?? "未知同步状态";
}
export function steamQuery(q: string, cursor: number): string {
  return `/catalog/steam/games?${new URLSearchParams({ q: q.trim(), after_appid: String(cursor), limit: "24" })}`;
}
