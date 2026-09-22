import json
import sys
from pathlib import Path
from playwright.sync_api import sync_playwright

sys.stdout.reconfigure(encoding='utf-8')
DIAG_DIR = Path(r"C:\Lira Videos\_diagnostico")

def main():
    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp("http://[::1]:9222")
        context = browser.contexts[0]
        page = [pg for pg in context.pages if "/project/" in pg.url][0]

        tiles_info = page.evaluate("""() => {
            const tiles = Array.from(document.querySelectorAll('flow-grid-tile-container'));
            return tiles.map((t, idx) => {
                const img = t.querySelector('img');
                const video = t.querySelector('video');
                const duration = t.querySelector('.duration-badge, [class*="duration"]')?.innerText || '';
                const title = t.querySelector('.tile-title, [class*="title"]')?.innerText || '';
                const badges = Array.from(t.querySelectorAll('.badge, [class*="badge"], mat-icon')).map(b => b.innerText.trim());
                return {
                    index: idx,
                    hasImg: !!img,
                    imgSrc: img ? img.src.substring(0, 80) : '',
                    hasVideo: !!video,
                    duration: duration,
                    badges: badges
                };
            });
        }""")
        print(f"Total tiles no canvas: {len(tiles_info)}")
        for t in tiles_info:
            print(f"  Tile {t['index']}: hasImg={t['hasImg']} hasVideo={t['hasVideo']} duration='{t['duration']}' badges={t['badges']}")

if __name__ == "__main__":
    main()
