export interface EnrichmentStatus {
  status: string;
  enabled: boolean;
  configured: boolean;
  worker_recent: boolean;
  jobs_by_state: Record<string, number>;
  eligible_metadata: number;
  automatic_metadata: number;
  last_success_at: number | null;
  last_error: string | null;
  next_request_at: number;
  reserved_requests_today: number;
  request_budget: number;
  refresh_seconds: number;
  retrieval_candidate_limit: number;
  notes: string[];
}

export function metadataStatusLabel(status: string): string {
  const labels: Record<string, string> = {
    disabled: "自动资料补全未启用",
    not_configured: "缺少服务端 IGDB 配置",
    worker_not_running: "补全进程尚无近期心跳",
    backoff: "补全退避中",
    processing: "正在处理一个补全批次",
    never_enriched: "尚无成功补全批次",
    ready: "最近批次已成功，不代表全库已补齐",
  };
  return labels[status] ?? "资料补全状态未知";
}
