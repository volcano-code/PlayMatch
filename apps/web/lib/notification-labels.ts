export const PRICE_ALERT_CONSENT = "observed-usd-inapp-v1";
export const PRICE_ALERT_SCOPE =
  "只在我主动点击“查价并检查提醒”后检查 USD 观测报价，不持续抓价、不发送邮件；版本与激活资格未核验，不代表保证可购买的最低价。每次开启最多提醒一次。";

export function alertStateLabel(state: unknown): string {
  switch (state) {
    case "off":
      return "查价提醒未开启";
    case "armed":
      return "已开启 · 等待你主动查价";
    case "pending":
      return "通知待投递 · 可关闭以阻止尚未提交的通知";
    case "notified":
      return "已生成站内通知 · 本次提醒已结束";
    case "failed":
      return "通知失败或已过期 · 可重新确认开启";
    default:
      return "提醒状态待确认";
  }
}

export function priceCheckLabel(value: unknown): string {
  switch (value) {
    case "queued":
      return "已观测到不高于目标价的报价，站内通知已排队；尚未确认投递。";
    case "above_target":
      return "本次合格观测报价仍高于目标价，未生成通知。";
    case "no_qualifying_offers":
      return "本次没有符合提醒范围的有效观测报价；没有把缺价当作免费。";
    case "unavailable":
      return "报价源暂不可用，未生成通知；提醒保持开启。";
    case "stale":
      return "设置已变化或本次报价已过期，未生成通知，请刷新后再查。";
    case "unsupported":
      return "本次地区或币种不在提醒范围内，没有进行汇率换算。";
    case "already_triggered":
      return "本次提醒已处理，不会重复生成通知。";
    case "disabled":
      return "提醒未开启或已关闭，未生成通知。";
    case "save_failed":
      return "报价已返回，但提醒记录保存失败；不能确认通知已排队，请稍后重试。";
    default:
      return "提醒结果尚未确认，请刷新状态；不会把未知状态视为成功。";
  }
}

export function notificationUrl(value: string): string | undefined {
  try {
    const url = new URL(value);
    const keys = [...url.searchParams.keys()];
    if (
      url.protocol !== "https:" ||
      url.host !== "www.cheapshark.com" ||
      url.username ||
      url.password ||
      url.pathname !== "/redirect" ||
      url.hash ||
      keys.length !== 1 ||
      keys[0] !== "dealID" ||
      !url.searchParams.get("dealID") ||
      /\s/.test(value)
    )
      return undefined;
    return url.href;
  } catch {
    return undefined;
  }
}
