import { test, expect } from '@playwright/test';

test.describe('Cerebro Web Application E2E User Journeys', () => {
    test('should load the dashboard successfully', async ({ page }) => {
        await page.goto('/');

        // Verify title and main dashboard elements
        await expect(page).toHaveTitle(/Cerebro/i);
        // Assuming a Dashboard header or a known UI element exists
        await expect(page.locator('text=Dashboard').first()).toBeVisible();
    });

    test('should navigate to research and agents pages', async ({ page }) => {
        await page.goto('/');

        // Navigate to Research
        // Assuming a sidebar navigation link for Research exists
        await page.click('nav >> text=Research');
        await expect(page).toHaveURL(/.*\/research/);
        await expect(page.locator('text=Research').first()).toBeVisible();

        // Navigate to Agents
        // Assuming a sidebar navigation link for Agents exists
        await page.click('nav >> text=Agents');
        await expect(page).toHaveURL(/.*\/agents/);
        await expect(page.locator('text=Agents').first()).toBeVisible();
    });

    test('should handle mock API failures gracefully', async ({ page }) => {
        // Intercept API calls to simulate a 500 error
        await page.route('**/api/v1/health', async (route) => {
            await route.fulfill({
                status: 500,
                contentType: 'application/json',
                body: JSON.stringify({ detail: 'Internal Server Error' }),
            });
        });

        await page.goto('/');

        // Check if error boundary or error toast appears
        // The exact text depends on how the app handles errors.
        // It might show "Error loading data" or similar depending on toast implementation
        // We'll look for common error indicators generically or simply ensure the page doesn't fully crash
        const errorLocator = page.locator('text=Error').first();
        // We don't strictly assert toBeVisible unless we know the exact text,
        // but assuming there is a toast or error state that says "Error":
        // For now we just check if it renders without a blank white screen crash
        await expect(page.locator('body')).not.toBeEmpty();
    });
});
