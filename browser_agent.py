from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlparse

try:
    from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
    from playwright.sync_api import sync_playwright
except Exception:  # pragma: no cover - exercised when dependency is absent
    PlaywrightTimeoutError = TimeoutError
    sync_playwright = None


BLOCKED_URL_KEYWORDS = {
    "login",
    "signin",
    "sign-in",
    "signup",
    "register",
    "checkout",
    "cart",
    "payment",
    "billing",
    "purchase",
    "subscribe",
    "auth",
    "account",
}

SENSITIVE_FIELD_KEYWORDS = {
    "password",
    "pass",
    "card",
    "credit",
    "cvv",
    "cvc",
    "token",
    "secret",
    "otp",
    "2fa",
    "ssn",
    "dni",
    "documento",
}


@dataclass(frozen=True)
class BrowserLink:
    text: str
    href: str


@dataclass(frozen=True)
class BrowserSnapshot:
    url: str
    title: str
    text: str
    links: list[BrowserLink]
    blocked: bool = False
    reason: str | None = None


class BrowserSafetyError(RuntimeError):
    pass


def assert_safe_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        raise BrowserSafetyError("Solo se permiten URLs http/https")
    lowered = url.lower()
    for keyword in BLOCKED_URL_KEYWORDS:
        if keyword in lowered:
            raise BrowserSafetyError(f"URL bloqueada por política de seguridad: {keyword}")


def _extract_links(page, *, max_links: int) -> list[BrowserLink]:
    raw_links = page.eval_on_selector_all(
        "a[href]",
        """
        (nodes) => nodes.map((node) => ({
            text: (node.innerText || node.textContent || '').trim(),
            href: node.href || ''
        }))
        """,
    )
    links: list[BrowserLink] = []
    for item in raw_links:
        text = str(item.get("text") or "").strip()
        href = str(item.get("href") or "").strip()
        if not href:
            continue
        links.append(BrowserLink(text=text[:200], href=href))
        if len(links) >= max_links:
            break
    return links


def _assert_no_sensitive_forms(page) -> None:
    fields = page.eval_on_selector_all(
        "input, textarea, select",
        """
        (nodes) => nodes.map((node) => ({
            type: (node.getAttribute('type') || '').toLowerCase(),
            name: (node.getAttribute('name') || '').toLowerCase(),
            id: (node.getAttribute('id') || '').toLowerCase(),
            autocomplete: (node.getAttribute('autocomplete') || '').toLowerCase(),
            placeholder: (node.getAttribute('placeholder') || '').toLowerCase()
        }))
        """,
    )
    for field in fields:
        joined = " ".join(str(value) for value in field.values())
        for keyword in SENSITIVE_FIELD_KEYWORDS:
            if keyword in joined:
                raise BrowserSafetyError(f"Formulario sensible detectado: {keyword}")


class BrowserAgent:
    """Read-only browser agent.

    It reads title, text and links only. It does not click, type, authenticate,
    purchase, pay, or submit forms.
    """

    def __init__(self, *, headless: bool = True, timeout_ms: int = 15000):
        self.headless = headless
        self.timeout_ms = timeout_ms

    def inspect_page(
        self,
        url: str,
        *,
        max_chars: int = 8000,
        max_links: int = 50,
    ) -> BrowserSnapshot:
        assert_safe_url(url)
        if sync_playwright is None:
            raise BrowserSafetyError("Playwright no está instalado")

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=self.headless)
            page = browser.new_page()
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=self.timeout_ms)
                assert_safe_url(page.url)
                _assert_no_sensitive_forms(page)
                return BrowserSnapshot(
                    url=page.url,
                    title=page.title(),
                    text=page.locator("body").inner_text(timeout=self.timeout_ms)[:max_chars],
                    links=_extract_links(page, max_links=max_links),
                )
            except PlaywrightTimeoutError as exc:
                raise BrowserSafetyError(f"Timeout leyendo página: {exc}") from exc
            finally:
                browser.close()

    def read_text(self, url: str, *, max_chars: int = 8000) -> str:
        return self.inspect_page(url, max_chars=max_chars).text
