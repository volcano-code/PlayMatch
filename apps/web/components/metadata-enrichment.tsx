"use client";

import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { metadataStatusLabel } from "@/lib/metadata-enrichment";
import type { EnrichmentStatus } from "@/lib/metadata-enrichment";

export function MetadataEnrichmentPanel() {
  const state = useQuery({
    queryKey: ["metadata-enrichment-status"],
    queryFn: () => api<EnrichmentStatus>("/catalog/enrichment/status"),
    staleTime: 15_000,
    refetchInterval: 30_000,
  });
  return (
    <section className="steam-directory" aria-label="自动资料补全与推荐接入">
      <div className="steam-directory-heading">
        <div>
          <strong>自动资料补全与推荐接入</strong>
          <p>按 Steam AppID 匹配资料，身份冲突不自动入库，人工资料不会被覆盖。</p>
        </div>
        <button className="text-button" disabled={state.isFetching} onClick={() => state.refetch()}>
          刷新资料状态
        </button>
      </div>
      {state.isPending ? (
        <p role="status">正在读取资料状态…</p>
      ) : state.isError ? (
        <p role="alert">资料状态读取失败；不会将失败显示为已完成，请重试。</p>
      ) : (
        <>
          <p role="status">
            <strong>{metadataStatusLabel(state.data.status)}</strong>
          </p>
          <div className="steam-directory-metrics">
            <span>有效推荐资料 {state.data.eligible_metadata.toLocaleString()} 款</span>
            <span>自动补全且有效 {state.data.automatic_metadata.toLocaleString()} 款</span>
            <span>尚未匹配 {state.data.jobs_by_state.unmatched ?? 0} 款</span>
            <span>
              待处理或退避{" "}
              {(state.data.jobs_by_state.pending ?? 0) + (state.data.jobs_by_state.backoff ?? 0)} 款
            </span>
            <span>拒绝或身份冲突 {state.data.jobs_by_state.rejected ?? 0} 款</span>
          </div>
          <p>
            最近成功批次：
            {state.data.last_success_at
              ? new Date(state.data.last_success_at * 1000).toLocaleString("zh-CN")
              : "尚无"}
          </p>
          <p>
            大目录先用 SQL 词项索引召回最多 {state.data.retrieval_candidate_limit}{" "}
            款，再排序与约束核验；不是全库向量检索。
          </p>
          <p>简介保留供应商原文；字段缺失不靠模型猜测。过期或冲突的自动资料不进入推荐。</p>
          {!state.data.configured && (
            <p>需部署者配置 IGDB 并启动 metadata-enrichment；不要在网页填写密钥。</p>
          )}
          {state.data.last_error && (
            <p>最近错误：{state.data.last_error}。请求预算按保守预留计量，不是成功调用数。</p>
          )}
        </>
      )}
    </section>
  );
}
