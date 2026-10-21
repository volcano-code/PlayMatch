import test from "node:test";
import assert from "node:assert/strict";
import {
  alertStateLabel,
  priceCheckLabel,
  notificationUrl,
  PRICE_ALERT_SCOPE,
} from "./notification-labels.ts";

test("scope never implies background monitoring or verified purchase eligibility", () => {
  assert.match(PRICE_ALERT_SCOPE, /不持续抓价、不发送邮件/);
  assert.match(PRICE_ALERT_SCOPE, /版本与激活资格未核验/);
  assert.match(PRICE_ALERT_SCOPE, /最多提醒一次/);
});
test("pending is not delivered and unknown is not a success", () => {
  assert.match(alertStateLabel("pending"), /待投递/);
  assert.match(priceCheckLabel("queued"), /尚未确认投递/);
  assert.match(priceCheckLabel("future-success"), /尚未确认/);
  assert.match(alertStateLabel(null), /待确认/);
});
test("price failure does not masquerade as free or within-budget", () => {
  assert.match(priceCheckLabel("no_qualifying_offers"), /没有把缺价当作免费/);
  assert.match(priceCheckLabel("unsupported"), /没有进行汇率换算/);
  assert.match(priceCheckLabel("save_failed"), /不能确认/);
});
test("only a single CheapShark redirect parameter is a notification link", () => {
  assert.equal(
    notificationUrl("https://www.cheapshark.com/redirect?dealID=test"),
    "https://www.cheapshark.com/redirect?dealID=test",
  );
  for (const url of [
    "javascript:alert(1)",
    "https://evil.invalid",
    "https://www.cheapshark.com.evil.invalid/redirect?dealID=x",
    "https://user:pass@www.cheapshark.com/redirect?dealID=x",
    "https://www.cheapshark.com/redirect?dealID=x&dealID=y",
    "https://www.cheapshark.com/redirect?dealID=x&token=secret",
    "https://www.cheapshark.com/redirect?dealID=x#foo",
  ])
    assert.equal(notificationUrl(url), undefined);
});
