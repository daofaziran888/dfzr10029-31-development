import { expect, test } from '@playwright/test'
import { writeFile } from 'node:fs/promises'

const ENDPOINT = '/export'
const browserErrors: string[] = []

test.describe('导出版本固化：批次、指纹、清单核对与固化包下载', () => {
  test.beforeEach(async ({ page }) => {
    browserErrors.length = 0
    page.on('pageerror', (e) => browserErrors.push(e.message))
    page.on('response', (r) => {
      if (r.url().includes('/api/') && r.status() >= 400) browserErrors.push(`${r.status()} ${r.url()}`)
    })
    await page.goto(ENDPOINT)
    await expect(page.locator('[data-testid^="batch-row-"]').first()).toBeVisible({ timeout: 15000 })
  })

  test.afterEach(async () => {
    expect(browserErrors).toEqual([])
  })

  test('页面按导出批次号、行数指纹和源快照标识一致展示已固化批次', async ({ page }) => {
    const row = page.locator('[data-testid^="batch-row-"]').first()
    await expect(row).toBeVisible()
    await expect(row.locator('td').nth(0)).toContainText(/EXB-\d{8}-\d{4}/)
    await expect(page.locator('[data-testid="row-fingerprint"]').first()).toContainText(/^[a-f0-9]{64}$/)
    await expect(row.locator('td').nth(2)).toContainText('SNAP-')
    await expect(row.locator('.frozen-tag')).toHaveText('已固化')
  })

  test('按交付范围发起批次：固化时点边界、清单与主版本核对一致', async ({ page }) => {
    await page.getByTestId('open-batch-create').click()
    await expect(page.getByTestId('batch-modal')).toBeVisible()
    // 固化时点只允许“启动时固化”，完成时固化不在处理边界内
    const policyOptions = page.getByTestId('freeze-policy').locator('option')
    await expect(policyOptions).toHaveCount(1)
    await expect(policyOptions.first()).toHaveText('启动时固化')

    const checkboxes = page.locator('.scope-option input[type="checkbox"]')
    await expect(checkboxes.first()).toBeVisible()
    await page.getByTestId('scope-check-SNAP-0003').check()
    await page.getByTestId('submit-batch').click()

    await expect(page.getByTestId('batch-modal')).toHaveCount(0)
    await expect(page.getByTestId('batch-detail')).toBeVisible()
    // 清单逐行带源快照标识
    const manifestRows = page.locator('[data-testid^="manifest-row-"]')
    await expect(manifestRows).toHaveCount(1)
    await expect(manifestRows.first()).toContainText('SNAP-0003')

    // 与主版本逐项核对
    const firstBatch = page.locator('[data-testid^="batch-row-"]').first()
    await firstBatch.getByRole('button', { name: '核对主版本' }).click()
    await expect(page.getByTestId('verify-result')).toContainText('逐项一致')
  })

  test('同交付范围并发提交只保留唯一有效批次（幂等）', async ({ page }) => {
    const before = await page.locator('[data-testid^="batch-row-"]').count()
    await page.getByTestId('open-batch-create').click()
    await page.getByTestId('scope-check-SNAP-0002').check()
    await page.getByTestId('submit-batch').click()
    await expect(page.getByTestId('batch-modal')).toHaveCount(0)
    const afterFirst = await page.locator('[data-testid^="batch-row-"]').count()
    expect(afterFirst).toBe(before + 1)

    // 再次提交同一交付范围
    await page.getByTestId('open-batch-create').click()
    await page.getByTestId('scope-check-SNAP-0002').check()
    await page.getByTestId('submit-batch').click()
    await expect(page.getByTestId('batch-modal')).toHaveCount(0)
    await expect(page.locator('[data-testid^="batch-row-"]')).toHaveCount(afterFirst)
  })

  test('文件上传形成导出版本清单，下载固化包并保留来源文件', async ({ page }) => {
    const scopePath = '/tmp/export_e2e/scope-ui.csv'
    await writeFile(scopePath, '﻿交付范围\nSNAP-0001\nSNAP-0003\n', 'utf-8')
    const before = await page.locator('[data-testid^="batch-row-"]').count()

    await page.getByTestId('scope-file').setInputFiles(scopePath)
    await expect(page.getByTestId('batch-detail')).toBeVisible({ timeout: 15000 })
    await expect(page.locator('[data-testid^="batch-row-"]')).toHaveCount(before + 1)

    // 下载入口只返回已固化的 zip 包
    const downloadPromise = page.waitForEvent('download')
    await page.locator('[data-testid^="batch-row-"]').first().getByRole('button', { name: '下载固化包' }).click()
    const download = await downloadPromise
    expect(download.suggestedFilename()).toMatch(/^EXB-\d{8}-\d{4}\.zip$/)
  })

  test('批次检索与原有时间线能力同时可用', async ({ page }) => {
    await page.getByTestId('batch-search').fill('SNAP-0001')
    await page.getByTestId('batch-query').click()
    const rows = page.locator('[data-testid^="batch-row-"]')
    await expect(rows.first()).toBeVisible()
    for (const item of await rows.all()) {
      await expect(item).toContainText('SNAP-0001')
    }
    await page.getByTestId('batch-reset').click()
    await expect(rows.first()).toBeVisible()

    // 既有时间线回归
    await expect(page.locator('.timeline-bar').first()).toBeVisible()
    const bars = page.locator('.timeline-bar')
    expect(await bars.count()).toBeGreaterThanOrEqual(3)
    await bars.first().click()
    await expect(page.getByTestId('timeline-log')).toBeVisible()

    const downloadPromise = page.waitForEvent('download')
    await page.getByTestId('export').click()
    const download = await downloadPromise
    expect(download.suggestedFilename()).toBe('export.csv')
  })
})
