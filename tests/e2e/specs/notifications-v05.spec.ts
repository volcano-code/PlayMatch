import { test, expect, type Page } from '@playwright/test';
import { randomUUID } from 'node:crypto';

async function prepare(page: Page, country: 'US' | 'CN' = 'US', target: number | null = 1000) {
  const registered = await page.request.post('/api/v1/auth/register', {data: {
    email:`alerts-${randomUUID()}@example.test`, password:'Alert-e2e-only-584!'
  }});
  expect(registered.status()).toBe(201);
  const games = await (await page.request.get('/api/v1/games?limit=1')).json();
  const game = games.items[0];
  const saved = await page.request.post('/api/v1/watchlist', {data: {
    game_id:game.id, target_minor:target, country, currency:country === 'US' ? 'USD' : 'CNY'
  }});
  expect(saved.status()).toBe(200);
  await page.goto('/');
  await expect(page.getByRole('button', {name:'退出登录'})).toBeVisible();
  await page.getByRole('button', {name:/^我的收藏/}).click();
  return game;
}

test('explicit consent -> query -> SQL outbox -> inbox read/delete (synthetic provider only)', async ({page}) => {
  const game = await prepare(page);
  const control = page.getByRole('region', {name:`${game.title_zh || game.title} 查价提醒`});
  await expect(control.getByRole('button', {name:'开启查价提醒'})).toBeDisabled();
  await expect(control).toContainText('不持续抓价、不发送邮件');
  await control.getByRole('checkbox').check();
  await control.getByRole('button', {name:'开启查价提醒'}).click();
  await expect(control).toContainText('等待你主动查价');
  const response = page.waitForResponse(r => r.url().endsWith(`/watchlist/${game.id}/check`));
  await control.getByRole('button', {name:'查价并检查提醒'}).click();
  expect((await (await response).json()).alert_evaluation).toBe('queued');
  await expect(control).toContainText('已生成站内通知');
  await page.getByRole('button', {name:'通知中心', exact:true}).click();
  const card = page.getByTestId('notification-card');
  await expect(card).toHaveCount(1);
  await expect(card).toContainText('Synthetic E2E Store');
  await expect(card).toContainText('7.99');
  await expect(card).toContainText('购买资格未核验');
  await card.getByRole('button', {name:'标为已读'}).click();
  await expect(card.getByText('已读', {exact:true})).toBeVisible();
  await page.reload();
  await page.getByRole('button', {name:'通知中心', exact:true}).click();
  await expect(card).toHaveCount(1);
  await expect(card.getByText('已读', {exact:true})).toBeVisible();
  await card.getByRole('button', {name:'删除通知'}).click();
  await expect(page.getByRole('heading', {name:'还没有站内通知'})).toBeVisible();
  await page.getByRole('button', {name:/^我的收藏/}).click();
  await expect(control).toContainText('本次提醒已结束');
  await expect(control.getByRole('button', {name:'开启查价提醒'})).toBeDisabled();
});

test('unsupported region and saved targets never silently enable alerts', async ({page}) => {
  const game = await prepare(page, 'CN');
  let checks=0;
  page.on('request', r => {if(r.url().endsWith('/check') && r.method()==='POST') checks++;});
  await expect(page.getByText('先设置美国地区 USD 目标价；国区提醒尚未接入。')).toBeVisible();
  await expect(page.getByRole('button', {name:'查价并检查提醒'})).toHaveCount(0);
  const saved = await (await page.request.get('/api/v1/watchlist')).json();
  expect(saved.items[0].game.id).toBe(game.id);
  expect(saved.items[0].alert.state).toBe('off');
  await page.getByRole('button', {name:'通知中心',exact:true}).click();
  await expect(page.getByRole('heading', {name:'还没有站内通知'})).toBeVisible();
  expect(checks).toBe(0);
});

test('inbox data is removed on logout; only the owner sees notification history', async ({page}) => {
  const game = await prepare(page);
  await page.request.put(`/api/v1/watchlist/${game.id}/alert`, {data:{enabled:true, expected_version:0, consent_version:'observed-usd-inapp-v1'}});
  await page.request.post(`/api/v1/watchlist/${game.id}/check`);
  await page.getByRole('button', {name:'通知中心',exact:true}).click();
  await expect(page.getByTestId('notification-card')).toHaveCount(1);
  await page.getByRole('button', {name:'退出登录'}).click();
  await page.getByRole('button', {name:'通知中心',exact:true}).click();
  await expect(page.getByRole('heading', {name:'登录后查看你的通知'})).toBeVisible();
  await expect(page.getByTestId('notification-card')).toHaveCount(0);
  await page.request.post('/api/v1/auth/register', {data:{email:`new-${randomUUID()}@example.test`,password:'Other-e2e-only-582!'}});
  await page.reload();
  await page.getByRole('button', {name:'通知中心',exact:true}).click();
  await expect(page.getByRole('heading', {name:'还没有站内通知'})).toBeVisible();
  await expect(page.getByTestId('notification-card')).toHaveCount(0);
});

test('closing pending consent cancels future checks without removing target', async ({page}) => {
  const game = await prepare(page);
  const control=page.getByRole('region', {name:`${game.title_zh || game.title} 查价提醒`});
  await control.getByRole('checkbox').check();
  await control.getByRole('button', {name:'开启查价提醒'}).click();
  await control.getByRole('button', {name:'关闭查价提醒'}).click();
  await expect(control).toContainText('查价提醒未开启');
  const saved=await (await page.request.get('/api/v1/watchlist')).json();
  expect(saved.items[0].target_minor).toBe(1000);
  expect(saved.items[0].alert.state).toBe('off');
  expect((await page.request.post(`/api/v1/watchlist/${game.id}/check`)).status()).toBe(409);
});
