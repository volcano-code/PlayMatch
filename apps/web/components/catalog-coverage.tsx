"use client";

import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import type { CatalogCoverage } from "@/lib/types";

export function CatalogCoveragePanel() {
  const coverage = useQuery({
    queryKey: ["catalog-coverage"],
    queryFn: () => api<CatalogCoverage>("/catalog/coverage"),
    staleTime: 60_000,
  });
  return (
    <section className="notice" aria-label="目录覆盖与证据范围">
      <div>
        <strong>目录覆盖与证据范围</strong>
        {coverage.isPending ? (
          <p>正在读取实际目录…</p>
        ) : coverage.isError ? (
          <p>
            暂时无法读取覆盖统计，不影响继续浏览。
            <button className="text-button" onClick={() => coverage.refetch()}>
              重试覆盖统计
            </button>
          </p>
        ) : (
          <>
            <p>
              当前目录 {coverage.data.total_games} 款 · 简体中文界面有据{" "}
              {coverage.data.chinese_interface_verified} 款 · 在线合作有据{" "}
              {coverage.data.online_coop_verified} 款 · 本地合作有据{" "}
              {coverage.data.local_coop_verified} 款
            </p>
            <p>
              {coverage.data.horror_unknown} 款仍缺恐怖内容判定；未核验不等于不包含。
              检索别名与简介含开发助手整理内容，不是独立玩家评测。
              目录不是全量游戏库，资料核对不等于版本或购买资格核验。
            </p>
          </>
        )}
      </div>
    </section>
  );
}
