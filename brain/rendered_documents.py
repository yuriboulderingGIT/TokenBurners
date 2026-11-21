"""Observe public file-button GET destinations on a rendered notice without executing their requests."""

import asyncio
import re

from playwright.async_api import Error

from brain.run_recipe import RecipeReadError, portal_origin

_FILENAME = re.compile(r".{1,215}\.(?:pdf|zip(?:\.\d{3})?|docx?|xlsx?|pptx?|od[ts]|rtf|csv|xml|dwg|7z)$", re.I)


async def capture_notice(page, context, notice_url):
    if portal_origin(page.url) != portal_origin(notice_url):
        raise RecipeReadError("the rendered notice left its public portal")
    # Use exactly the published filename on a visible file button. Never submit forms.
    controls = page.locator("button:visible, [role='button']:visible")
    candidates = []
    available = await controls.all()
    if len(available) > 300:
        raise RecipeReadError("the notice exceeds the rendered-control limit")
    for button in available:
        label = " ".join((await button.inner_text()).split())
        if _FILENAME.fullmatch(label) and not await button.evaluate(
            "e => !!e.closest('form,nav,header,aside,footer') || e.disabled || e.getAttribute('aria-disabled')==='true'"
        ):
            candidates.append((button, label))
    if len(candidates) > 100:
        raise RecipeReadError("the notice exceeds the public file-button limit")
    content = await page.content()
    links = {}
    recorded = []
    seen = asyncio.Event()

    async def intercept(route):
        request = route.request
        if (request.method == "GET" and request.resource_type in {"document", "xhr", "fetch"}
                and portal_origin(request.url) == portal_origin(notice_url) and request.url != page.url):
            recorded.append(request.url)
            seen.set()
        # Observe the action only. Each accepted URL is fetched once by the file pipeline.
        await route.abort()

    await context.route("**/*", intercept)
    try:
        for button, label in candidates:
            recorded.clear()
            seen.clear()
            try:
                await button.click(timeout=3000)
                await asyncio.wait_for(seen.wait(), 1.5)
                await asyncio.sleep(0.05)
            except (TimeoutError, Error) as exc:
                raise RecipeReadError(f"document button {label!r} exposed no public file GET") from exc
            urls = set(recorded)
            if len(urls) != 1:
                raise RecipeReadError(f"document button {label!r} exposed ambiguous public requests")
            url = urls.pop()
            if url in links and links[url] != label:
                raise RecipeReadError("different document buttons resolved to the same file")
            links[url] = label
    finally:
        await context.unroute("**/*", intercept)
    return content, links
