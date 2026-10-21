import { expect, test } from '@playwright/test';

test('real 48-game release exposes two disjoint pages and honest coverage', async ({ page, request }) => {
  const first = await (await request.get('/api/v1/games?limit=24')).json();
  const second = await (await request.get('/api/v1/games?limit=24&offset=24')).json();
  expect(first.total).toBe(48);
  expect(second.items).toHaveLength(24);
  expect(new Set([...first.items, ...second.items].map(g => g.id)).size).toBe(48);
  await page.goto('/');
  const coverage = page.getByRole('region', { name: '目录覆盖与证据范围' });
  await expect(coverage).toContainText('当前目录 48 款');
  await expect(coverage).toContainText('简体中文界面有据 32 款');
  await expect(coverage).toContainText('不是独立玩家评测');
  await expect(page.getByTestId('game-card')).toHaveCount(24);
  await page.getByRole('button', { name: '下一页', exact: true }).click();
  await expect(page.getByTestId('game-card')).toHaveCount(24);
  await expect(page.getByTestId('game-card').first()).toContainText(second.items[0].title_zh);
});

test('editorial abbreviation is found by real catalog and recommendation paths', async ({ page, request }) => {
  const search = await (await request.get('/api/v1/games?q=BG3')).json();
  expect(search.items[0].steam_app_id).toBe(1086940);
  await page.goto('/');
  await page.getByLabel('购买地区').selectOption('CN');
  await page.getByLabel('描述你想玩的游戏', { exact: true }).fill('BG3');
  await page.getByRole('button', { name: '为我推荐', exact: true }).click();
  await expect(page.getByRole('button', { name: '回到游戏目录' })).toBeVisible();
  await expect(page.getByTestId('game-card').first()).toContainText('博德之门3');
});

test('coverage failure is labeled rather than asserting a complete catalog (synthetic failure)', async ({ page }) => {
  await page.route('**/api/v1/catalog/coverage', route => route.fulfill({ status: 503, json: { detail: 'Synthetic coverage failure' } }));
  await page.goto('/');
  await expect(page.getByRole('region', { name: '目录覆盖与证据范围' })).toContainText('暂时无法读取覆盖统计');
  await expect(page.getByTestId('game-card')).toHaveCount(24);
});
