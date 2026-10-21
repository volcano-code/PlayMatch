"use client";
import { useEffect, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";
import type { OfferResponse, WatchItem } from "@/lib/types";
import {
  alertStateLabel,
  priceCheckLabel,
  PRICE_ALERT_CONSENT,
  PRICE_ALERT_SCOPE,
} from "@/lib/notification-labels";

export function PriceAlertControls({ item, ownerId }: { item: WatchItem; ownerId: string }) {
  const client = useQueryClient();
  const [consent, setConsent] = useState(false);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const alive = useRef(true);
  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
    };
  }, []);
  const state = item.alert;
  if (!state) return null;
  const available = item.country === "US" && item.currency === "USD" && item.target_minor !== null;
  const needsConsent = ["off", "notified", "failed"].includes(state.state);

  async function action(kind: "enable" | "disable" | "check") {
    setBusy(true);
    setMessage("");
    try {
      if (kind === "check") {
        const result = await api<OfferResponse>(
          `/watchlist/${encodeURIComponent(item.game.id)}/check`,
          { method: "POST" },
        );
        if (alive.current) setMessage(priceCheckLabel(result.alert_evaluation));
      } else {
        await api(`/watchlist/${encodeURIComponent(item.game.id)}/alert`, {
          method: "PUT",
          body: JSON.stringify({
            enabled: kind === "enable",
            expected_version: state.version,
            consent_version: kind === "enable" ? PRICE_ALERT_CONSENT : null,
          }),
        });
      }
    } catch (error) {
      if (alive.current)
        setMessage(error instanceof Error ? error.message : "操作尚未确认，请刷新状态。");
    } finally {
      // Refetch even on an ambiguous network failure; never assume mutation failed.
      if (alive.current) {
        setBusy(false);
        await client.invalidateQueries({ queryKey: ["watchlist", ownerId] });
        await client.invalidateQueries({ queryKey: ["notifications", ownerId] });
      }
    }
  }
  return (
    <section
      className="price-alert-controls"
      aria-label={`${item.game.title_zh || item.game.title} 查价提醒`}
    >
      <p className="fine-print" role="status">
        {alertStateLabel(state.state)}
      </p>
      {!available ? (
        <p className="fine-print">先设置美国地区 USD 目标价；国区提醒尚未接入。</p>
      ) : needsConsent ? (
        <>
          <label className="alert-consent">
            <input
              type="checkbox"
              checked={consent}
              onChange={(event) => setConsent(event.target.checked)}
              disabled={busy}
            />
            <span>我已了解并同意：{PRICE_ALERT_SCOPE}</span>
          </label>
          <button
            className="button button-secondary"
            disabled={!consent || busy}
            onClick={() => action("enable")}
          >
            开启查价提醒
          </button>
        </>
      ) : (
        <div className="watch-actions">
          {state.state === "armed" && (
            <button
              className="button button-secondary"
              disabled={busy}
              onClick={() => action("check")}
            >
              查价并检查提醒
            </button>
          )}
          <button className="text-button" disabled={busy} onClick={() => action("disable")}>
            关闭查价提醒
          </button>
        </div>
      )}
      {message && (
        <p className="fine-print" role="status">
          {message}
        </p>
      )}
    </section>
  );
}
