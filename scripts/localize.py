from __future__ import annotations

import hashlib
import html as html_lib
import json
import mimetypes
import re
import shutil
import sys
import time
from pathlib import Path
from urllib.parse import unquote, urljoin, urlparse

import requests
from bs4 import BeautifulSoup

ROOT = "https://unitedcarriers.com"
ROOT_HOSTS = {"unitedcarriers.com", "www.unitedcarriers.com"}
ASSET_HOSTS = {
    "cdn.prod.website-files.com",
    "assets-global.website-files.com",
    "cdn.jsdelivr.net",
    "d3e54v103j8qbb.cloudfront.net",
    "united-carriers.netlify.app",
    "unpkg.com",
    "s3.amazonaws.com",
    "dms.licdn.com",
}
BLOCKED_SCRIPT_HOSTS = {
    "js.stripe.com",
    "cdn-cookieyes.com",
    "cdn.mxpnl.com",
    "www.googletagmanager.com",
    "www.google-analytics.com",
    "pagead2.googlesyndication.com",
    "adservice.google.com",
}
TEXT_TYPES = {
    "text/css",
    "text/javascript",
    "application/javascript",
    "application/x-javascript",
    "application/json",
    "application/manifest+json",
    "image/svg+xml",
}
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/153.0 Safari/537.36"
)
CSP = (
    "default-src 'self' data: blob:; "
    "base-uri 'self'; object-src 'none'; "
    "script-src 'self' 'unsafe-inline' 'unsafe-eval' blob:; "
    "style-src 'self' 'unsafe-inline' data:; "
    "img-src 'self' data: blob:; font-src 'self' data:; "
    "media-src 'self' data: blob:; connect-src 'self'; "
    "frame-src 'self'; worker-src 'self' blob:; form-action 'self'"
)

session = requests.Session()
session.headers.update({"User-Agent": USER_AGENT, "Accept": "*/*"})
asset_cache: dict[str, str | None] = {}
processing: set[str] = set()
failures: list[tuple[str, str]] = []
page_failures: list[tuple[str, str]] = []


def clean_url(url: str, base: str | None = None) -> str:
    url = html_lib.unescape(url.strip())
    if url.startswith("//"):
        url = "https:" + url
    if base:
        url = urljoin(base, url)
    return url


def is_http(url: str) -> bool:
    return url.startswith("http://") or url.startswith("https://")


def safe_segment(value: str) -> str:
    value = unquote(value)
    value = value.replace("\\", "_").replace("/", "_")
    value = re.sub(r"[\x00-\x1f<>:\"|?*]", "_", value)
    return value or "index"


def local_asset_path(url: str, content_type: str = "") -> Path:
    parsed = urlparse(url)
    parts = [safe_segment(x) for x in parsed.path.split("/") if x]
    if not parts:
        parts = ["index"]
    filename = parts[-1]
    suffix = Path(filename).suffix
    if not suffix:
        ext = mimetypes.guess_extension(content_type.split(";", 1)[0].strip()) or ""
        if ext == ".jpe":
            ext = ".jpg"
        filename += ext
    if parsed.query:
        stem = Path(filename).stem
        ext = Path(filename).suffix
        filename = f"{stem}__q_{hashlib.sha256(parsed.query.encode()).hexdigest()[:12]}{ext}"
    parts[-1] = filename
    return Path("assets") / safe_segment(parsed.netloc) / Path(*parts)


def request(url: str) -> requests.Response:
    last = None
    for attempt in range(2):
        try:
            response = session.get(url, timeout=15, allow_redirects=True)
            if response.status_code == 200:
                return response
            last = RuntimeError(f"HTTP {response.status_code}")
        except Exception as exc:
            last = exc
        time.sleep(0.8 * (attempt + 1))
    raise RuntimeError(str(last or "request failed"))


def replace_css_urls(text: str, base_url: str) -> str:
    pattern = re.compile(r"url\(\s*(['\"]?)(.*?)\1\s*\)", re.I)

    def repl(match: re.Match[str]) -> str:
        raw = match.group(2).strip()
        if not raw or raw.startswith(("data:", "blob:", "#")):
            return match.group(0)
        absolute = clean_url(raw, base_url)
        local = download_asset(absolute)
        return f"url('{local}')" if local else "url('')"

    text = pattern.sub(repl, text)

    import_pattern = re.compile(r"@import\s+(?:url\()?['\"]([^'\"]+)['\"]\)?", re.I)

    def import_repl(match: re.Match[str]) -> str:
        absolute = clean_url(match.group(1), base_url)
        local = download_asset(absolute)
        return f"@import url('{local}')" if local else ""

    return import_pattern.sub(import_repl, text)


def replace_js_asset_urls(text: str, base_url: str) -> str:
    host = urlparse(base_url).netloc.lower()

    # Mirror quoted relative and root-relative runtime dependencies from any
    # approved asset host. This catches Vite chunks, Finsweet chunks, WebGL
    # textures, sibling dynamic imports, fonts, CSS and media.
    if host in ASSET_HOSTS:
        dep_pattern = re.compile(
            r"(?P<q>['\"\x60])(?P<path>(?:\.\.?/|/)[^'\"\x60\s)]+\.(?:js|mjs|css|json|wasm|webp|png|jpe?g|gif|svg|mp4|webm|woff2?|ttf|otf|glb|gltf|hdr)(?:\?[^'\"\x60\s)]*)?)(?P=q)",
            re.I,
        )

        def dep_repl(match: re.Match[str]) -> str:
            absolute = clean_url(match.group("path"), base_url)
            if urlparse(absolute).netloc.lower() not in ASSET_HOSTS:
                return match.group(0)
            local = download_asset(absolute)
            if not local:
                return match.group(0)
            return f"{match.group('q')}{local}{match.group('q')}"

        text = dep_pattern.sub(dep_repl, text)

    absolute_pattern = re.compile(r"https?://[^\s\"'\x60<>\\)]+")

    def abs_repl(match: re.Match[str]) -> str:
        url = match.group(0)
        asset_host = urlparse(url).netloc.lower()
        if asset_host in ASSET_HOSTS:
            local = download_asset(url)
            return local or ""
        if asset_host in ROOT_HOSTS:
            path = urlparse(url).path
            if looks_like_page(path):
                return local_page_href(url)
            return ""
        return url

    text = absolute_pattern.sub(abs_repl, text)

    if host == "united-carriers.netlify.app":
        text = re.sub(
            r'var E=\[[\s\S]*?\];if\(!\(\(\)=>\{[\s\S]*?\}\)\(\)\)throw new Error\("Unauthorized domain"\);',
            "",
            text,
            count=1,
        )

    return text

def download_asset(url: str) -> str | None:
    url = clean_url(url)
    if not is_http(url):
        return url
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    if host in BLOCKED_SCRIPT_HOSTS:
        return None
    if host not in ASSET_HOSTS and host not in ROOT_HOSTS:
        return None
    if host in ROOT_HOSTS and looks_like_page(parsed.path):
        return local_page_href(url)
    if url in asset_cache:
        return asset_cache[url]
    if url in processing:
        return asset_cache.get(url)

    processing.add(url)
    try:
        response = request(url)
        content_type = response.headers.get("content-type", "").split(";", 1)[0].lower()
        out = local_asset_path(url, content_type)
        local_url = "/" + out.as_posix()
        asset_cache[url] = local_url
        out.parent.mkdir(parents=True, exist_ok=True)

        data = response.content
        if content_type in TEXT_TYPES or out.suffix.lower() in {".css", ".js", ".mjs", ".svg", ".json"}:
            try:
                text = data.decode("utf-8")
            except UnicodeDecodeError:
                fallback_encoding = response.apparent_encoding or "latin-1"
                text = data.decode(fallback_encoding, errors="strict")
            if content_type == "text/css" or out.suffix.lower() == ".css":
                text = replace_css_urls(text, url)
            elif "javascript" in content_type or out.suffix.lower() in {".js", ".mjs"}:
                text = replace_js_asset_urls(text, url)
            out.write_text(text, encoding="utf-8")
        else:
            out.write_bytes(data)
        return local_url
    except Exception as exc:
        if parsed.path.lower().endswith(".svg") and (
            "placeholder" in parsed.path.lower()
            or "webflow-badge" in parsed.path.lower()
        ):
            out = local_asset_path(url, "image/svg+xml")
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(
                '<svg xmlns="http://www.w3.org/2000/svg" width="1" height="1" viewBox="0 0 1 1"></svg>\n',
                encoding="utf-8",
            )
            local_url = "/" + out.as_posix()
            asset_cache[url] = local_url
            return local_url
        asset_cache[url] = None
        failures.append((url, str(exc)))
        print(f"WARN asset failed: {url}: {exc}", file=sys.stderr)
        return None
    finally:
        processing.discard(url)


def looks_like_page(path: str) -> bool:
    suffix = Path(path).suffix.lower()
    return not suffix or suffix in {".html", ".htm"}


def local_page_href(url: str) -> str:
    parsed = urlparse(url)
    path = parsed.path or "/"
    if path.endswith("/index.html"):
        path = path[: -len("index.html")]
    if path.endswith(".html"):
        path = path[: -len(".html")] + "/"
    if not path.endswith("/") and not Path(path).suffix:
        path += "/"
    if parsed.query:
        path += "?" + parsed.query
    if parsed.fragment:
        path += "#" + parsed.fragment
    return path


def rewrite_srcset(value: str, base_url: str) -> str:
    items = []
    for part in value.split(","):
        part = part.strip()
        if not part:
            continue
        bits = part.split()
        raw = bits[0]
        descriptor = " ".join(bits[1:])
        if raw.startswith(("data:", "blob:")):
            local = raw
        else:
            absolute = clean_url(raw, base_url)
            local = download_asset(absolute) if is_http(absolute) else raw
        if local:
            items.append(local + ((" " + descriptor) if descriptor else ""))
    return ", ".join(items)


def strip_tracking_and_preconnect(soup: BeautifulSoup, page_url: str) -> None:
    for link in list(soup.find_all("link")):
        rel = {x.lower() for x in link.get("rel", [])}
        href = link.get("href", "")
        if rel & {"preconnect", "dns-prefetch"}:
            link.decompose()
            continue
        if href and is_http(clean_url(href, page_url)):
            host = urlparse(clean_url(href, page_url)).netloc.lower()
            if host in BLOCKED_SCRIPT_HOSTS:
                link.decompose()

    for script in list(soup.find_all("script")):
        src = script.get("src")
        if src:
            absolute = clean_url(src, page_url)
            host = urlparse(absolute).netloc.lower()
            path = urlparse(absolute).path.lower()
            if host in BLOCKED_SCRIPT_HOSTS or (
                host in ROOT_HOSTS
                and (
                    "/ga/" in path
                    or "/gtm" in path
                    or path.startswith("/9i1h7htk")
                )
            ):
                script.decompose()
                continue
        body = script.string or ""
        marker = body.lower()
        if any(x in marker for x in ("googletagmanager", "google-analytics", "mixpanel.init", "cookieyes")):
            script.decompose()


def inject_guards(soup: BeautifulSoup) -> None:
    if soup.head is None:
        head = soup.new_tag("head")
        if soup.html:
            soup.html.insert(0, head)
        else:
            soup.insert(0, head)
    for old in soup.head.find_all("meta", attrs={"http-equiv": re.compile("content-security-policy", re.I)}):
        old.decompose()
    csp = soup.new_tag("meta")
    csp["http-equiv"] = "Content-Security-Policy"
    csp["content"] = CSP
    soup.head.insert(0, csp)

    guard = soup.new_tag("script", src="/assets/local/network-guard.js")
    soup.head.insert(1, guard)


def rewrite_html(html: str, page_url: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    strip_tracking_and_preconnect(soup, page_url)

    asset_attrs = {
        "script": ["src"],
        "img": ["src"],
        "source": ["src"],
        "video": ["src", "poster", "data-src"],
        "audio": ["src"],
        "track": ["src"],
        "embed": ["src"],
        "input": ["src"],
        "object": ["data"],
    }

    for tag in soup.find_all(True):
        for attr in asset_attrs.get(tag.name, []):
            raw = tag.get(attr)
            if not isinstance(raw, str) or not raw.strip() or raw.startswith(("data:", "blob:", "#")):
                continue
            absolute = clean_url(raw, page_url)
            if is_http(absolute):
                host = urlparse(absolute).netloc.lower()
                if host in BLOCKED_SCRIPT_HOSTS:
                    tag.attrs.pop(attr, None)
                elif host in ASSET_HOSTS or host in ROOT_HOSTS:
                    local = download_asset(absolute)
                    if local:
                        tag[attr] = local
                    else:
                        tag.attrs.pop(attr, None)

        if tag.has_attr("srcset"):
            rewritten = rewrite_srcset(tag.get("srcset", ""), page_url)
            if rewritten:
                tag["srcset"] = rewritten
            else:
                tag.attrs.pop("srcset", None)

        if tag.name == "link" and tag.get("href"):
            rel = {x.lower() for x in tag.get("rel", [])}
            if rel & {"stylesheet", "icon", "apple-touch-icon", "manifest", "preload", "modulepreload"}:
                absolute = clean_url(tag["href"], page_url)
                if is_http(absolute):
                    local = download_asset(absolute)
                    if local:
                        tag["href"] = local
                    else:
                        tag.decompose()
                        continue

        if tag.name == "a" and tag.get("href"):
            absolute = clean_url(tag["href"], page_url)
            if is_http(absolute) and urlparse(absolute).netloc.lower() in ROOT_HOSTS:
                tag["href"] = local_page_href(absolute)

        if tag.name == "form":
            action = tag.get("action")
            if action:
                absolute = clean_url(action, page_url)
                if is_http(absolute) and urlparse(absolute).netloc.lower() not in {urlparse(page_url).netloc.lower()}:
                    tag["action"] = "#"
            else:
                tag["action"] = "#"

        style = tag.get("style")
        if isinstance(style, str) and "url(" in style:
            tag["style"] = replace_css_urls(style, page_url)

    for tag in soup.find_all(["link", "meta"]):
        val = tag.get("href") or tag.get("content")
        if not isinstance(val, str):
            continue
        absolute = clean_url(val, page_url)
        if is_http(absolute) and urlparse(absolute).netloc.lower() in ROOT_HOSTS and looks_like_page(urlparse(absolute).path):
            if tag.has_attr("href"):
                tag["href"] = local_page_href(absolute)
            elif tag.get("property") in {"og:url"}:
                tag["content"] = local_page_href(absolute)

    # Localised files are modified during mirroring, so the original CDN
    # Subresource Integrity hashes are no longer valid. Keeping them makes
    # browsers reject otherwise valid local CSS and JavaScript.
    for resource_tag in soup.find_all(["script", "link"]):
        resource_url = resource_tag.get("src") or resource_tag.get("href")
        if isinstance(resource_url, str) and resource_url.startswith("/"):
            resource_tag.attrs.pop("integrity", None)

    inject_guards(soup)
    return str(soup)


def write_guard() -> None:
    p = Path("assets/local/network-guard.js")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        r'''(() => {
  const allowed = (value) => {
    try {
      const u = new URL(String(value && value.url ? value.url : value), location.href);
      return u.origin === location.origin || u.protocol === 'data:' || u.protocol === 'blob:';
    } catch (_) { return true; }
  };
  const blocked = (kind, value) => {
    console.warn('[local-only]', kind, 'blocked external request:', value);
    return new TypeError('External network request blocked by local-only build');
  };
  if (window.fetch) {
    const original = window.fetch.bind(window);
    window.fetch = (input, init) => allowed(input) ? original(input, init) : Promise.reject(blocked('fetch', input));
  }
  if (window.XMLHttpRequest) {
    const open = XMLHttpRequest.prototype.open;
    XMLHttpRequest.prototype.open = function(method, url, ...rest) {
      if (!allowed(url)) throw blocked('xhr', url);
      return open.call(this, method, url, ...rest);
    };
  }
  if (navigator.sendBeacon) {
    const beacon = navigator.sendBeacon.bind(navigator);
    navigator.sendBeacon = (url, data) => allowed(url) ? beacon(url, data) : false;
  }
  if (window.WebSocket) {
    const NativeWebSocket = window.WebSocket;
    window.WebSocket = function(url, protocols) {
      if (!allowed(url)) throw blocked('websocket', url);
      return new NativeWebSocket(url, protocols);
    };
    window.WebSocket.prototype = NativeWebSocket.prototype;
  }
  if (window.EventSource) {
    const NativeEventSource = window.EventSource;
    window.EventSource = function(url, config) {
      if (!allowed(url)) throw blocked('eventsource', url);
      return new NativeEventSource(url, config);
    };
    window.EventSource.prototype = NativeEventSource.prototype;
  }
})();
''',
        encoding="utf-8",
    )


def discover_internal_pages(html: str, page_url: str) -> list[str]:
    soup = BeautifulSoup(html, "html.parser")
    found: list[str] = []
    seen: set[str] = set()

    for anchor in soup.find_all("a", href=True):
        raw = str(anchor.get("href", "")).strip()
        if not raw or raw.startswith(("#", "mailto:", "tel:", "javascript:")):
            continue
        absolute = clean_url(raw, page_url)
        parsed = urlparse(absolute)
        if parsed.netloc.lower() not in ROOT_HOSTS:
            continue
        if parsed.query:
            continue
        if not looks_like_page(parsed.path):
            continue
        if parsed.path.startswith(("/cdn-cgi/", "/.well-known/")):
            continue

        path = parsed.path or "/"
        if not path.endswith("/") and not Path(path).suffix:
            path += "/"
        canonical = ROOT.rstrip("/") + path
        if canonical not in seen:
            seen.add(canonical)
            found.append(canonical)

    return found


def target_for_seed(url: str) -> Path:
    path = urlparse(url).path.strip("/")
    if not path:
        return Path("index.html")
    return Path(path) / "index.html"


def cleanup_generated(seeds: list[str]) -> None:
    shutil.rmtree("assets", ignore_errors=True)
    for url in seeds:
        target = target_for_seed(url)
        if target == Path("index.html"):
            try:
                target.unlink()
            except FileNotFoundError:
                pass
        else:
            top = Path(target.parts[0])
            if top.exists() and top.is_dir():
                shutil.rmtree(top, ignore_errors=True)


def main() -> None:
    seeds_file = Path("seed-pages.txt")
    seeds = [
        line.strip()
        for line in seeds_file.read_text().splitlines()
        if line.strip() and not line.startswith("#")
    ]
    cleanup_generated(seeds)
    write_guard()

    # Mirror every explicitly seeded page plus one level of pages linked from
    # those seeds. This covers the current site navigation and visible CMS
    # content without recursively archiving the entire historical catalogue.
    queue: list[tuple[str, int]] = [(url, 0) for url in dict.fromkeys(seeds)]
    seen_pages: set[str] = set()
    generated_pages: list[str] = []
    max_pages = 60
    max_link_depth = 0

    while queue and len(seen_pages) < max_pages:
        url, depth = queue.pop(0)
        parsed = urlparse(url)
        path = parsed.path or "/"
        if not path.endswith("/") and not Path(path).suffix:
            path += "/"
        canonical = ROOT.rstrip("/") + path

        if canonical in seen_pages:
            continue
        seen_pages.add(canonical)

        print(f"PAGE {canonical}")
        try:
            response = request(canonical)
        except Exception as exc:
            page_failures.append((canonical, str(exc)))
            print(f"WARN page failed: {canonical}: {exc}", file=sys.stderr)
            continue

        if depth < max_link_depth:
            for discovered in discover_internal_pages(response.text, canonical):
                if (
                    discovered not in seen_pages
                    and all(item[0] != discovered for item in queue)
                    and len(seen_pages) + len(queue) < max_pages
                ):
                    queue.append((discovered, depth + 1))

        target = target_for_seed(canonical)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(rewrite_html(response.text, canonical), encoding="utf-8")
        generated_pages.append(canonical)

    route_rewrites = []
    for generated in generated_pages:
        parsed = urlparse(generated)
        route = parsed.path or "/"
        if route == "/":
            continue
        if not route.endswith("/"):
            route += "/"
        destination = route + "index.html"
        route_rewrites.append({"source": route, "destination": destination})
        route_rewrites.append({"source": route.rstrip("/"), "destination": destination})

    Path("vercel.json").write_text(
        json.dumps(
            {
                "cleanUrls": False,
                "trailingSlash": False,
                "rewrites": route_rewrites,
                "headers": [
                    {
                        "source": "/assets/(.*)",
                        "headers": [
                            {"key": "Cache-Control", "value": "public, max-age=31536000, immutable"},
                            {"key": "X-Content-Type-Options", "value": "nosniff"},
                        ],
                    },
                    {
                        "source": "/(.*)",
                        "headers": [
                            {"key": "Content-Security-Policy", "value": CSP},
                            {"key": "X-Content-Type-Options", "value": "nosniff"},
                        ],
                    },
                ],
            },
            indent=2,
        ) + "\n",
        encoding="utf-8",
    )

    report = {
        "pages": len(generated_pages),
        "assets_localized": sum(1 for v in asset_cache.values() if v),
        "asset_failures": [{"url": u, "error": e} for u, e in failures],
        "page_failures": [{"url": u, "error": e} for u, e in page_failures],
        "policy": "Local-only runtime. External asset, frame, form and connection requests are blocked by CSP and network guard.",
    }
    Path("localization-report.json").write_text(
        json.dumps(report, indent=2) + "\n",
        encoding="utf-8",
    )

    print(
        f"Completed: {len(generated_pages)} pages, "
        f"{report['assets_localized']} localized assets, "
        f"{len(failures)} asset failures, "
        f"{len(page_failures)} page failures"
    )


if __name__ == "__main__":
    main()
