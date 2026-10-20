"use client";
import { useState, type FormEvent } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  ArrowUpRight,
  Bookmark,
  CircleAlert,
  Clock3,
  ExternalLink,
  RefreshCw,
  ShieldCheck,
} from "lucide-react";
import type { Country, Game, OfferResponse, Recommendation } from "@/lib/types";
import { api, formatMoney, moneyToMinor, readableTime, safeExternalUrl } from "@/lib/api";
import { Modal } from "./modal";
import { GameArtwork } from "./game-card";

export function OfferDrawer({
  game,
  country,
  recommendation,
  onClose,
  onSave,
}: {
  game: Game | null;
  country: Country;
  recommendation?: Recommendation;
  onClose: () => void;
  onSave: (game: Game, target: number | null) => Promise<void>;
}) {
  const [target, setTarget] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const offers = useQuery({
    queryKey: ["offers", game?.id, country],
    enabled: Boolean(game),
    queryFn: () =>
      api<OfferResponse>(`/games/${encodeURIComponent(game!.id)}/offers?country=${country}`),
    retry: false,
    staleTime: 60_000,
  });
  async function save(event: FormEvent) {
    event.preventDefault();
    if (!game) return;
    setError("");
    try {
      const minor = moneyToMinor(target);
      setSaving(true);
      await onSave(game, minor);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "保存失败，请重试。");
    } finally {
      setSaving(false);
    }
  }
  const data = offers.data;
  return (
    <Modal open={Boolean(game)} onClose={onClose} title="游戏详情与报价" drawer>
      {game && (
        <>
          <GameArtwork game={game} className="drawer-art" />
          <div className="drawer-title">
            <span className="eyebrow">{game.genres.join(" / ")}</span>
            <h2>{game.title_zh || game.title}</h2>
            <p className="muted">{game.title}</p>
          </div>
          <p className="detail-description">{game.description}</p>
          {recommendation && (
            <section className="detail-section">
              <h3>为什么推荐给你</h3>
              <ul className="reason-list">
                {recommendation.reasons.map((reason, i) => (
                  <li key={i}>
                    <ShieldCheck size={16} />
                    {reason}
                  </li>
                ))}
              </ul>
              {recommendation.tradeoffs.length > 0 && (
                <div className="notice">
                  <CircleAlert size={17} />
                  <div>
                    {recommendation.tradeoffs.map((tradeoff, i) => (
                      <p key={i}>{tradeoff}</p>
                    ))}
                  </div>
                </div>
              )}
            </section>
          )}
          <section className="detail-section">
            <div className="section-heading compact">
              <div>
                <span className="eyebrow">购买前，多核验一步</span>
                <h3>{country === "US" ? "美国地区报价 · USD" : "中国大陆报价 · CNY"}</h3>
              </div>
              <button
                className="icon-button"
                onClick={() => offers.refetch()}
                disabled={offers.isFetching}
                aria-label="刷新报价"
              >
                <RefreshCw size={17} className={offers.isFetching ? "spin" : ""} />
              </button>
            </div>
            {offers.isPending && (
              <div className="loading-block">
                <RefreshCw className="spin" size={22} />
                <p>正在向价格来源查询…</p>
              </div>
            )}
            {offers.isError && (
              <div className="notice error">
                <CircleAlert size={17} />
                <div>
                  <p>{offers.error.message}</p>
                  <button className="text-button" onClick={() => offers.refetch()}>
                    重新查询
                  </button>
                </div>
              </div>
            )}
            {data && (
              <>
                {data.notices.map((notice, i) => (
                  <div className="notice" key={i}>
                    <CircleAlert size={16} />
                    <p>{notice}</p>
                  </div>
                ))}
                {!data.supported && (
                  <div className="empty-price">
                    <span className="country-symbol">CN</span>
                    <h4>暂未接入国区价格</h4>
                    <p>
                      可以继续发现与收藏游戏。美元报价不会折算成国区售价；请前往商店核对实际购买地区与金额。
                    </p>
                  </div>
                )}
                {data.supported && data.offers.length === 0 && (
                  <div className="empty-price">
                    <h4>暂未获得有效报价</h4>
                    <p>没有找到价格不代表游戏未出售。你可以稍后重试，或查看官方商店。</p>
                  </div>
                )}
                <div className="offer-list">
                  {data.offers.map((offer) => (
                    <article className="offer-row" key={offer.id}>
                      <div className="offer-store">
                        <div>
                          <strong>{offer.store}</strong>
                          {data.best_offer_id === offer.id && offer.eligible_for_best && (
                            <span className="tag tag-quiet-mint">已核验报价中最低</span>
                          )}
                        </div>
                        <span>{offer.edition || "版本待核验"}</span>
                        <small>
                          {offer.activation_status === "verified"
                            ? "激活地区已核验"
                            : "激活地区待核验"}{" "}
                          · {offer.country}
                        </small>
                      </div>
                      <div className="offer-price">
                        <strong>{formatMoney(offer.price_minor, offer.currency)}</strong>
                        {offer.regular_price_minor > offer.price_minor && (
                          <del>{formatMoney(offer.regular_price_minor, offer.currency)}</del>
                        )}
                        {safeExternalUrl(offer.url) && (
                          <a
                            className="offer-link"
                            href={safeExternalUrl(offer.url)}
                            target="_blank"
                            rel="noreferrer noopener"
                          >
                            查看商店 <ArrowUpRight size={14} />
                          </a>
                        )}
                      </div>
                    </article>
                  ))}
                </div>
                {data.fetched_at && (
                  <p className="fine-print">
                    <Clock3 size={13} />
                    本系统查询于 {readableTime(data.fetched_at)}
                    。税费、版本和最终结算金额以商店为准。
                  </p>
                )}
              </>
            )}
            {safeExternalUrl(game.store_url) && (
              <a
                className="button button-secondary full store-button"
                href={safeExternalUrl(game.store_url)}
                target="_blank"
                rel="noreferrer noopener"
              >
                打开 Steam 商店 <ExternalLink size={15} />
              </a>
            )}
          </section>
          <section className="detail-section watch-target">
            <h3>把它加入愿望清单</h3>
            <p className="muted">保存目标价，留给下一次购买决定。</p>
            <form onSubmit={save}>
              <label className="sr-only" htmlFor="target-price">
                目标价格
              </label>
              <div className="target-input">
                <span>{country === "US" ? "USD" : "CNY"}</span>
                <input
                  id="target-price"
                  inputMode="decimal"
                  value={target}
                  onChange={(e) => setTarget(e.target.value)}
                  placeholder="目标价，可留空"
                />
              </div>
              <button className="button button-primary" disabled={saving}>
                <Bookmark size={16} />
                {saving ? "保存中…" : "保存目标价"}
              </button>
            </form>
            {error && (
              <p className="error-message" role="alert">
                {error}
              </p>
            )}
            <p className="fine-print">当前仅保存收藏与目标价，尚未启用自动价格监控或通知。</p>
          </section>
          <section className="detail-section">
            <h3>资料与来源</h3>
            <p className="fine-print">
              {game.metadata_status === "curated"
                ? "人工整理的起始目录；具体属性以所列来源及核验状态为准。"
                : "来自已接入的游戏资料来源。"}
            </p>
            <ul className="evidence-list">
              {game.evidence.map((entry, i) => (
                <li key={i}>
                  {safeExternalUrl(entry.url) ? (
                    <a href={safeExternalUrl(entry.url)} target="_blank" rel="noreferrer noopener">
                      {entry.label}
                      <ExternalLink size={13} />
                    </a>
                  ) : (
                    <span>{entry.label}</span>
                  )}
                  <small>
                    {entry.field} ·{" "}
                    {entry.observed_at ? readableTime(entry.observed_at) : "未提供观测时间"}
                  </small>
                </li>
              ))}
            </ul>
          </section>
        </>
      )}
    </Modal>
  );
}
