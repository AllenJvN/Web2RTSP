"""Browser authentication helpers.

The Home Assistant token bootstrap is adapted from DashSnap, GPL-3.0,
revision 0ed4825dc5d76532fbb2bdf6ce7966ccf748bc46.
"""

from __future__ import annotations

import json


async def apply_auth(context, page, auth: dict) -> None:
    strategy = auth.get("strategy", "none")
    if strategy == "none":
        return
    if strategy == "http_header":
        if auth.get("headers"):
            await context.set_extra_http_headers(auth["headers"])
        return
    if strategy != "ha_token":
        raise ValueError(f"unsupported authentication strategy: {strategy}")

    base_url = auth["base_url"].rstrip("/")
    blob = {
        "access_token": auth["token"],
        "token_type": "Bearer",
        "expires_in": 1800,
        "hassUrl": base_url,
        "clientId": f"{base_url}/",
        "expires": 9999999999999,
        "refresh_token": "",
    }
    token_json = json.dumps(blob)
    await context.add_init_script(
        """(() => {
          const blob = __TOKEN__;
          try { localStorage.setItem('hassTokens', JSON.stringify(blob)); } catch (_) {}
          try {
            const request = indexedDB.open('home-assistant', 1);
            request.onupgradeneeded = event => {
              const db = event.target.result;
              if (!db.objectStoreNames.contains('tokens')) db.createObjectStore('tokens');
            };
            request.onsuccess = event => {
              try {
                const tx = event.target.result.transaction('tokens', 'readwrite');
                tx.objectStore('tokens').put(blob, 'hassTokens');
              } catch (_) {}
            };
          } catch (_) {}
        })();""".replace("__TOKEN__", token_json)
    )
    await page.goto(f"{base_url}/", wait_until="domcontentloaded", timeout=30_000)
    await page.evaluate(
        """async blob => {
          try { localStorage.setItem('hassTokens', JSON.stringify(blob)); } catch (_) {}
          await new Promise(resolve => {
            let settled = false;
            const done = () => { if (!settled) { settled = true; resolve(); } };
            try {
              const request = indexedDB.open('home-assistant', 1);
              request.onupgradeneeded = event => {
                const db = event.target.result;
                if (!db.objectStoreNames.contains('tokens')) db.createObjectStore('tokens');
              };
              request.onsuccess = event => {
                try {
                  const tx = event.target.result.transaction('tokens', 'readwrite');
                  tx.objectStore('tokens').put(blob, 'hassTokens');
                  tx.oncomplete = done; tx.onerror = done;
                } catch (_) { done(); }
              };
              request.onerror = done;
            } catch (_) { done(); }
          });
        }""",
        blob,
    )
