/**
 * Starter Playwright spec for agent-coddies.
 *
 * Nothing is hardcoded: the base URL and the test account come from
 * credentials.yaml -> test_environments.<env>, exported by run_tests.py as
 * CODDIE_BASE_URL / CODDIE_USERNAME / CODDIE_PASSWORD.
 *
 *   python scripts/run_tests.py playwright --spec tests/e2e --browser chromium
 */

import { test, expect, Page } from '@playwright/test';

const BASE_URL = process.env.CODDIE_BASE_URL ?? 'http://localhost:3000';
const USERNAME = process.env.CODDIE_USERNAME ?? '';
const PASSWORD = process.env.CODDIE_PASSWORD ?? '';

test.beforeAll(() => {
  if (!USERNAME || !PASSWORD) {
    throw new Error(
      'CODDIE_USERNAME / CODDIE_PASSWORD are not set. Run through ' +
        'scripts/run_tests.py so the configured test account is injected.',
    );
  }
});

/** Log in via the UI. Prefer an API/storageState login once the flow is stable. */
async function login(page: Page): Promise<void> {
  await page.goto(`${BASE_URL}/login`);
  await page.getByLabel(/username|email/i).fill(USERNAME);
  await page.getByLabel(/password/i).fill(PASSWORD);
  await page.getByRole('button', { name: /sign in|log ?in/i }).click();
  // Wait for a condition, never for a fixed duration.
  await expect(page.getByRole('navigation')).toBeVisible();
}

test.describe('HM-XXXX — <what this suite proves>', () => {
  test.beforeEach(async ({ page }) => {
    await login(page);
  });

  test('AC1: <criterion stated as an observable outcome>', async ({ page }) => {
    await page.goto(`${BASE_URL}/your/feature`);

    // Select by role, label, or data-testid. Never by a brittle CSS chain.
    await page.getByRole('button', { name: 'Create' }).click();
    await page.getByLabel('Name').fill(`coddie-${Date.now()}`);
    await page.getByRole('button', { name: 'Save' }).click();

    await expect(page.getByRole('alert')).toContainText(/saved|created/i);
  });

  test('AC2 (negative): rejects an empty required field without writing', async ({ page }) => {
    await page.goto(`${BASE_URL}/your/feature/new`);
    await page.getByRole('button', { name: 'Save' }).click();

    await expect(page.getByText(/required/i)).toBeVisible();
    await expect(page).toHaveURL(/\/new$/); // still on the form, nothing persisted
  });

  test('AC3 (boundary): handles an apostrophe in free text', async ({ page }) => {
    const value = "O'Brien & Sons <script>";
    await page.goto(`${BASE_URL}/your/feature/new`);
    await page.getByLabel('Name').fill(value);
    await page.getByRole('button', { name: 'Save' }).click();

    // Stored intact and rendered as text, not as markup.
    await expect(page.getByText(value, { exact: true })).toBeVisible();
  });

  test('no console errors on the main flow', async ({ page }) => {
    const errors: string[] = [];
    page.on('console', (msg) => {
      if (msg.type() === 'error') errors.push(msg.text());
    });
    await page.goto(`${BASE_URL}/your/feature`);
    await expect(page.getByRole('main')).toBeVisible();
    expect(errors, `console errors:\n${errors.join('\n')}`).toHaveLength(0);
  });
});

test.afterEach(async ({ page }, testInfo) => {
  // Artefact on failure so coddie-dev can reproduce without guessing.
  if (testInfo.status !== testInfo.expectedStatus) {
    await page.screenshot({
      path: testInfo.outputPath(`failure-${testInfo.title.replace(/\W+/g, '-')}.png`),
      fullPage: true,
    });
  }
});
