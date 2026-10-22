import { test, expect } from '@playwright/test';

test('real disabled metadata worker does not imply a populated Steam recommendation corpus', async ({ page }) => {
  await page.goto('/');
  const section = page.getByRole('region', { name: '自动资料补全与推荐接入' });
  await expect(section.getByText('自动资料补全未启用', { exact: true })).toBeVisible();
  await expect(section).toContainText('自动补全且有效 0 款');
  await expect(section).toContainText('有效推荐资料 48 款');
  await expect(section).toContainText('不是全库向量检索');
});

test('synthetic metadata status outage recovers through a read-only refresh', async ({ page }) => {
  let unavailable = true;
  await page.route('**/api/v1/catalog/enrichment/status', async route => {
    expect(route.request().method()).toBe('GET');
    if (unavailable) await route.fulfill({ status: 503, json: { detail: 'synthetic outage' } });
    else await route.continue();
  });
  await page.goto('/');
  const section = page.getByRole('region', { name: '自动资料补全与推荐接入' });
  await expect(section.getByRole('alert')).toContainText('资料状态读取失败');
  unavailable = false;
  await section.getByRole('button', { name: '刷新资料状态' }).click();
  await expect(section.getByText('自动资料补全未启用', { exact: true })).toBeVisible();
});
