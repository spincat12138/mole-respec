// Exercise the browser, WebSocket bridge, VNC authentication and rendered Qt UI.
const { chromium } = require('playwright');
const { readFileSync, mkdirSync } = require('node:fs');

(async () => {
  const browser = await chromium.launch({ headless: true });
  try {
    const page = await browser.newPage({ viewport: { width: 1500, height: 1000 } });
    await page.goto('http://127.0.0.1:6080/vnc.html?autoconnect=1&resize=scale');
    await page.locator('#noVNC_password_input').waitFor({ state: 'visible' });
    await page.locator('#noVNC_password_input').fill(readFileSync('vnc_password.txt', 'utf8').trim());
    await page.locator('#noVNC_credentials_button').click();
    await page.waitForFunction(() => document.documentElement.classList.contains('noVNC_connected'));
    await page.waitForFunction(() => {
      const canvas = document.querySelector('#noVNC_container canvas');
      if (!canvas || canvas.width < 1080 || canvas.height < 824) return false;
      // The initial RFB framebuffer is black; require actual desktop pixels.
      const pixels = canvas.getContext('2d').getImageData(0, 0, canvas.width, canvas.height).data;
      let bright = 0;
      for (let i = 0; i < pixels.length; i += 160) {
        if (pixels[i] > 180 && pixels[i + 1] > 180 && pixels[i + 2] > 180) bright++;
      }
      return bright > canvas.width * canvas.height / 160;
    });
    mkdirSync('artifacts', { recursive: true });
    await page.screenshot({ path: 'artifacts/desktop.png' });
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
