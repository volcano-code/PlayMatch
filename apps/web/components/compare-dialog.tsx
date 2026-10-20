"use client";
import { useQueries } from "@tanstack/react-query";
import { ArrowUpRight } from "lucide-react";
import type { Country, Game, OfferResponse } from "@/lib/types";
import { api, formatMoney, safeExternalUrl } from "@/lib/api";
import { Modal } from "./modal";
import { GameArtwork } from "./game-card";

export function CompareDialog({
  open,
  games,
  country,
  onClose,
}: {
  open: boolean;
  games: Game[];
  country: Country;
  onClose: () => void;
}) {
  const offers = useQueries({
    queries: games.map((game) => ({
      queryKey: ["offers", game.id, country],
      queryFn: () =>
        api<OfferResponse>(`/games/${encodeURIComponent(game.id)}/offers?country=${country}`),
      enabled: open,
      retry: false,
      staleTime: 60_000,
    })),
  });
  const chinese = { verified: "中文已核验", unsupported: "不支持中文", unknown: "中文待核验" };
  const coop = {
    online: "在线合作",
    local: "本地合作",
    none: "无合作模式",
    unknown: "合作模式待核验",
  };
  return (
    <Modal open={open} onClose={onClose} title="好游戏之间，也可以慢慢选" wide>
      <p className="muted compare-intro">
        把适合你的地方并排来看。资料未确认的项目会保留为待核验。
      </p>
      <div
        className="compare-grid"
        style={{ gridTemplateColumns: `repeat(${games.length}, minmax(220px, 1fr))` }}
      >
        {games.map((game, index) => {
          const response = offers[index].data;
          const eligible = response?.offers
            .filter((offer) => offer.eligible_for_best)
            .sort((a, b) => a.price_minor - b.price_minor);
          return (
            <article className="compare-column" key={game.id}>
              <GameArtwork game={game} />
              <h3>{game.title_zh || game.title}</h3>
              <p className="english-title">{game.title}</p>
              <dl>
                <div>
                  <dt>主题与体验</dt>
                  <dd>{game.themes.join(" · ") || "暂无资料"}</dd>
                </div>
                <div>
                  <dt>中文支持</dt>
                  <dd>{chinese[game.chinese_support]}</dd>
                </div>
                <div>
                  <dt>合作模式</dt>
                  <dd>{coop[game.coop]}</dd>
                </div>
                <div>
                  <dt>恐怖元素</dt>
                  <dd>
                    {game.horror === "yes"
                      ? "包含恐怖元素"
                      : game.horror === "no"
                        ? "未标记恐怖元素"
                        : "待核验"}
                  </dd>
                </div>
                <div>
                  <dt>参考游玩时长</dt>
                  <dd>
                    {game.estimated_hours !== null
                      ? `资料估计 ${game.estimated_hours} 小时`
                      : "暂无可靠资料"}
                  </dd>
                </div>
                <div>
                  <dt>{country === "US" ? "美国报价 · USD" : "中国大陆报价"}</dt>
                  <dd>
                    {country === "CN"
                      ? "国区价格暂未接入"
                      : offers[index].isPending
                        ? "正在查询…"
                        : offers[index].isError
                          ? "报价暂不可用"
                          : eligible?.[0]
                            ? formatMoney(eligible[0].price_minor, eligible[0].currency)
                            : response?.offers.length
                              ? "有观测报价，适用性待核验"
                              : "暂无有效报价"}
                  </dd>
                </div>
              </dl>
              {safeExternalUrl(game.store_url) && (
                <a
                  className="button button-secondary full"
                  href={safeExternalUrl(game.store_url)}
                  target="_blank"
                  rel="noreferrer noopener"
                >
                  官方商店 <ArrowUpRight size={15} />
                </a>
              )}
            </article>
          );
        })}
      </div>
    </Modal>
  );
}
