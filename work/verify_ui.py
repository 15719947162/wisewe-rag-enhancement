from __future__ import annotations

import json
import os
from pathlib import Path

from playwright.sync_api import sync_playwright


ROOT = Path(r"F:\中教AI\wisewe-rag-simple")
OUT_DIR = ROOT / "work"
OUT_DIR.mkdir(parents=True, exist_ok=True)
BASE_URL = os.environ.get("WISEWE_UI_BASE_URL", "http://127.0.0.1:3000").rstrip("/")

IDENTITY = {
    "tenantId": "1",
    "userId": "100",
    "username": "admin",
    "displayName": "系统管理员",
    "tenantName": "AI 基座租户",
    "roleCodes": ["super_admin"],
    "isTenantAdmin": True,
    "source": "identity_snapshot",
}


def main() -> None:
    with sync_playwright() as p:
      browser = p.chromium.launch(headless=True)
      page = browser.new_page(viewport={"width": 1440, "height": 1600}, color_scheme="light")
      page.set_default_timeout(90_000)

      page.goto(f"{BASE_URL}/login?next=%2Foverview", wait_until="domcontentloaded", timeout=90_000)
      page.wait_for_load_state("networkidle")
      page.evaluate(
          """identity => {
              localStorage.setItem("wisewe.integration.identity", JSON.stringify(identity));
          }""",
          IDENTITY,
      )

      page.goto(f"{BASE_URL}/overview", wait_until="domcontentloaded", timeout=90_000)
      page.wait_for_load_state("networkidle")
      page.wait_for_timeout(1200)
      print("overview-title:", page.locator("h1").first.inner_text())
      page.screenshot(path=str(OUT_DIR / "overview.png"), full_page=True)

      page.goto(f"{BASE_URL}/knowledge-bases?create=1", wait_until="domcontentloaded", timeout=90_000)
      page.wait_for_load_state("networkidle")
      page.wait_for_timeout(1200)
      print("kbs-title:", page.locator("h1").first.inner_text())
      page.screenshot(path=str(OUT_DIR / "knowledge-bases-create.png"), full_page=True)

      browser.close()


if __name__ == "__main__":
    main()
