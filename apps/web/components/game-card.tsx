"use client";
import { Bookmark, Check, ChevronRight, Columns3, Gamepad2, ThumbsUp } from "lucide-react";
import { useState } from "react";
import type { Game, Recommendation, FeedbackKind } from "@/lib/types";
import { safeExternalUrl } from "@/lib/api";

export function GameArtwork({ game, className = "" }: { game: Game; className?: string }) {
  const [failed, setFailed] = useState(false);
  return (
    <div className={`game-art ${className}`}>
      {!failed && safeExternalUrl(game.image_url) ? (
        <img
          src={game.image_url}
          alt={`${game.title_zh || game.title} 游戏封面`}
          loading="lazy"
          onError={() => setFailed(true)}
        />
      ) : (
        <div className="art-fallback">
          <Gamepad2 size={36} />
          <span>{game.title_zh || game.title}</span>
        </div>
      )}
    </div>
  );
}

export function GameCard({
  game,
  recommendation,
  saved,
  selected,
  liked,
  owned,
  onOpen,
  onSave,
  onCompare,
  onFeedback,
}: {
  game: Game;
  recommendation?: Recommendation;
  saved: boolean;
  selected: boolean;
  liked: boolean;
  owned: boolean;
  onOpen: (game: Game) => void;
  onSave: (game: Game) => void;
  onCompare: (game: Game) => void;
  onFeedback: (game: Game, kind: FeedbackKind) => void;
}) {
  const title = game.title_zh || game.title;
  return (
    <article className={`game-card ${selected ? "is-selected" : ""}`} data-testid="game-card">
      <button
        className="card-cover-button"
        onClick={() => onOpen(game)}
        aria-label={`查看 ${title} 详情与报价`}
      >
        <GameArtwork game={game} />
        <span className="cover-shade" />
        <span className="cover-platform">
          <Gamepad2 size={12} /> PC
        </span>
        <span className="cover-open">
          <ChevronRight size={18} />
        </span>
      </button>
      <div className="card-content">
        <div className="card-eyebrow">{game.genres.slice(0, 2).join(" · ") || "探索游戏"}</div>
        <button className="card-title-button" onClick={() => onOpen(game)}>
          <h3>{title}</h3>
        </button>
        {game.title !== title && <p className="english-title">{game.title}</p>}
        <p className="card-description">{recommendation?.reasons[0] || game.description}</p>
        <div className="tag-row">
          {game.themes.slice(0, 2).map((theme) => (
            <span className="tag" key={theme}>
              {theme}
            </span>
          ))}
          <span className={`tag ${game.chinese_support === "verified" ? "tag-quiet-mint" : ""}`}>
            {game.chinese_support === "verified"
              ? "中文已核验"
              : game.chinese_support === "unsupported"
                ? "不支持中文"
                : "中文待核验"}
          </span>
        </div>
        {recommendation && recommendation.tradeoffs.length > 0 && (
          <p className="card-caveat">需留意 · {recommendation.tradeoffs[0]}</p>
        )}
        <div className="card-rule" />
        <div className="card-actions">
          <button className="card-offer-button" onClick={() => onOpen(game)}>
            详情与报价 <ChevronRight size={15} />
          </button>
          <div className="card-utility">
            <button
              className={`icon-button small ${selected ? "active" : ""}`}
              onClick={() => onCompare(game)}
              aria-pressed={selected}
              aria-label={`${selected ? "移出" : "加入"}对比 ${title}`}
              title="加入对比"
            >
              <Columns3 size={17} />
            </button>
            <button
              className={`icon-button small ${saved ? "active" : ""}`}
              onClick={() => onSave(game)}
              aria-pressed={saved}
              aria-label={`${saved ? "已收藏" : "收藏"} ${title}`}
              title={saved ? "已收藏" : "收藏"}
            >
              <Bookmark size={17} fill={saved ? "currentColor" : "none"} />
            </button>
          </div>
        </div>
        <div className="feedback-actions">
          <button
            onClick={() => onFeedback(game, "like")}
            aria-pressed={liked}
            title={liked ? "点击撤销喜欢" : "记录喜欢"}
          >
            <ThumbsUp size={13} />
            {liked ? "已喜欢" : "喜欢这款"}
          </button>
          <button
            onClick={() => onFeedback(game, "owned")}
            aria-pressed={owned}
            title={owned ? "点击撤销拥有标记" : "标记为已拥有"}
          >
            <Check size={13} />
            {owned ? "已标记拥有" : "我已拥有"}
          </button>
        </div>
      </div>
    </article>
  );
}
