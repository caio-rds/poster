import asyncio
import logging
import time
import xml.etree.ElementTree as ET
from collections import deque
from datetime import UTC, datetime
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit
from urllib.robotparser import RobotFileParser

import httpx
from bs4 import BeautifulSoup

from app.config import Settings
from app.models import Source

logger = logging.getLogger(__name__)
SITE = "https://zionsoftwarehouse.com.br"
WORDPRESS_API = "https://cms.zionsoftwarehouse.com.br/wp-json/wp/v2"
USER_AGENT = "ZionContentWorker/1.0"


def clean_url(url: str) -> str:
    parts = urlsplit(url)
    query = urlencode(
        sorted(
            (key, value)
            for key, value in parse_qsl(parts.query)
            if key in {"page", "p", "offset"} and value.isdigit() and int(value) <= 1000
        )
    )
    return urlunsplit((parts.scheme, parts.netloc, parts.path.rstrip("/") or "/", query, ""))


def is_site_url(url: str) -> bool:
    parts = urlsplit(url)
    return parts.scheme in {"http", "https"} and parts.netloc.lower() in {
        urlsplit(SITE).netloc,
        "www.zionsoftwarehouse.com.br",
    }


def page_url(url: str) -> str | None:
    if not is_site_url(url):
        return None
    url = clean_url(url)
    path = urlsplit(url).path.lower()
    if any(
        path.endswith(extension)
        for extension in (
            ".jpg",
            ".jpeg",
            ".png",
            ".gif",
            ".webp",
            ".svg",
            ".ico",
            ".pdf",
            ".zip",
            ".mp4",
            ".mp3",
            ".css",
            ".js",
            ".xml",
            ".json",
            ".woff",
            ".woff2",
        )
    ):
        return None
    if any(
        path == prefix or path.startswith(prefix + "/")
        for prefix in (
            "/wp-admin",
            "/wp-login.php",
            "/login",
            "/logout",
            "/admin",
        )
    ):
        return None
    parts = urlsplit(url)
    return urlunsplit(("https", urlsplit(SITE).netloc, parts.path, parts.query, ""))


def internal_links(html: str, base: str) -> list[str]:
    soup = BeautifulSoup(html, "html.parser")
    links = []
    for tag in soup.select("a[href]"):
        url = page_url(urljoin(base, tag["href"]))
        if url and url not in links:
            links.append(url)
    return links


def article_links(html: str, base: str) -> list[str]:
    return [url for url in internal_links(html, base) if urlsplit(url).path.startswith("/blog/")]


def is_blog_url(url: str) -> bool:
    return "blog" in urlsplit(url).path.lower().split("/")


# URLs traduzidas usadas antes da migração do blog para o CMS.
LEGACY_BLOG_SLUGS = {
    (
        "the-secret-to-top-rankings-how-optimized-website-and-landing-page-design-"
        "guarantees-your-google-ranking"
    ): (
        "o-segredo-dos-primeiros-lugares-como-a-criacao-de-sites-e-landing-pages-otimizadas-"
        "garante-seu-ranqueamento-no-google"
    ),
    "myths-and-truths-about-website-security-why-ongoing-maintenance-is-your-best-defense": (
        "mitos-e-verdades-sobre-a-seguranca-de-sites-por-que-a-manutencao-continua-e-a-sua-melhor-defesa"
    ),
    "como-elegir-una-agencia-de-desarrollo-de-sitios-web": (
        "como-escolher-uma-agencia-de-desenvolvimento-de-sites"
    ),
    "desarrollo-de-aplicaciones-cuando-el-sitio-web-no-es-suficiente-para-tu-estrategia-digital": (
        "desenvolvimento-de-aplicativos-quando-o-site-nao-e-suficiente-para-a-sua-estrategia-digital"
    ),
}


def blog_source_urls(url: str) -> list[str]:
    """Permalinks do mesmo artigo, incluindo URLs antigas de idiomas."""
    if not is_site_url(url) or not is_blog_url(url):
        return []
    slug = urlsplit(url).path.rstrip("/").rsplit("/", 1)[-1]
    canonical = LEGACY_BLOG_SLUGS.get(slug, slug)
    slugs = [canonical, *[key for key, value in LEGACY_BLOG_SLUGS.items() if value == canonical]]
    urls = []
    for item in slugs:
        for prefix in ("", "/en-US", "/es"):
            candidate = f"{SITE}{prefix}/blog/{item}"
            urls.extend([candidate, candidate + "/"])
    return urls


def extract_source(html: str, url: str, kind: str) -> Source:
    soup = BeautifulSoup(html, "html.parser")
    for node in soup.select("script, style, nav, footer, header, noscript, svg, form"):
        node.decompose()
    if kind == "blog":
        container = soup.find("article") or soup.find("main") or soup.body or soup
    else:
        container = soup.find("main") or soup.body or soup
    heading = container.find("h1") or soup.find("title")
    title = heading.get_text(" ", strip=True) if heading else "Zion Software House"
    text = "\n".join(line.strip() for line in container.get_text("\n").splitlines() if line.strip())
    if len(text) < 150:
        raise ValueError("Página sem conteúdo suficiente; pode exigir renderização JavaScript")
    return Source(url=clean_url(url), title=title, text=text[:24000], kind=kind)


class WebsiteCollector:
    def __init__(self, client: httpx.AsyncClient, settings: Settings):
        self.client = client
        self.settings = settings
        self.robots: RobotFileParser | None = None
        self.last_request = 0.0

    async def request(self, url: str) -> httpx.Response:
        if not is_site_url(url):
            raise ValueError("URL fora do site")
        for redirect in range(6):
            if self.robots and not self.robots.can_fetch(USER_AGENT, url):
                raise ValueError("URL bloqueada por robots.txt")
            for attempt in range(3):
                delay = self.settings.crawl_delay_seconds
                if self.robots:
                    delay = max(delay, self.robots.crawl_delay(USER_AGENT) or 0)
                    rate = self.robots.request_rate(USER_AGENT)
                    if rate and rate.requests:
                        delay = max(delay, rate.seconds / rate.requests)
                await asyncio.sleep(max(0, delay - (time.monotonic() - self.last_request)))
                self.last_request = time.monotonic()
                try:
                    response = await self.client.get(url, follow_redirects=False)
                    if response.status_code == 429 or response.status_code >= 500:
                        response.raise_for_status()
                    break
                except httpx.HTTPError:
                    if attempt == 2:
                        raise
                    await asyncio.sleep(2**attempt)
            if response.is_redirect:
                target = urljoin(url, response.headers.get("location", ""))
                if not is_site_url(target) or redirect == 5:
                    raise ValueError("Redirecionamento inválido ou para fora do site")
                url = target
                continue
            response.raise_for_status()
            return response
        raise ValueError("Limite de redirecionamentos")

    async def initialize(self):
        try:
            response = await self.request(f"{SITE}/robots.txt")
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code != 404:
                raise
            response = exc.response
        self.robots = RobotFileParser()
        if response.status_code == 404:
            self.robots.parse([])
        else:
            response.raise_for_status()
            self.robots.parse(response.text.splitlines())

    async def fetch(self, url: str) -> str:
        if self.robots is None:
            raise RuntimeError("Collector não inicializado")
        if not self.robots.can_fetch(USER_AGENT, url):
            raise ValueError("URL bloqueada por robots.txt")
        response = await self.request(url)
        content_type = response.headers.get("content-type", "text/html")
        if "html" not in content_type:
            raise ValueError("URL não contém uma página HTML")
        return response.text

    async def sitemap_pages(self) -> list[str]:
        pending = deque(self.robots.site_maps() or [f"{SITE}/sitemap.xml"])
        seen = set()
        pages = []
        while pending and len(seen) < 10 and len(pages) < self.settings.crawl_max_pages:
            url = pending.popleft()
            if url in seen or not is_site_url(url):
                continue
            seen.add(url)
            try:
                response = await self.request(url)
                root = ET.fromstring(response.text)
            except (httpx.HTTPError, ValueError, ET.ParseError):
                logger.warning("Sitemap indisponível: %s", url)
                continue
            locations = [node.text.strip() for node in root.findall(".//{*}loc") if node.text]
            if root.tag.rsplit("}", 1)[-1] == "sitemapindex":
                pending.extend(locations[:10])
            else:
                for location in locations:
                    candidate = page_url(location)
                    if candidate and not is_blog_url(candidate) and candidate not in pages:
                        pages.append(candidate)
                        if len(pages) >= self.settings.crawl_max_pages:
                            break
        return pages

    async def collect(self):
        await self.initialize()
        sitemap = deque(await self.sitemap_pages())
        queue = deque([(f"{SITE}/", 0)])
        queued = {url for url, _ in queue}
        visited = set()
        yielded = 0
        while (queue or sitemap) and len(visited) < self.settings.crawl_max_pages:
            if not queue:
                candidate = sitemap.popleft()
                if candidate in queued:
                    continue
                queued.add(candidate)
                queue.append((candidate, 0))
            url, depth = queue.popleft()
            if url in visited:
                continue
            visited.add(url)
            if is_blog_url(url):
                continue
            try:
                html = await self.fetch(url)
                if depth < self.settings.crawl_max_depth:
                    for link in internal_links(html, url):
                        if (
                            not is_blog_url(link)
                            and link not in queued
                            and len(queued) < self.settings.crawl_max_pages * 5
                        ):
                            queued.add(link)
                            queue.append((link, depth + 1))
                source = extract_source(html, url, "website")
                yielded += 1
                yield source
            except (httpx.HTTPError, ValueError):
                logger.warning("Falha ao coletar página: %s", url)
        logger.info("Coleta concluída: %s páginas visitadas, %s fontes", len(visited), yielded)
        if not yielded:
            raise ValueError("Nenhuma página com conteúdo útil foi coletada")


def featured_image_url(post: dict) -> str | None:
    embedded = post.get("_embedded")
    if not isinstance(embedded, dict):
        return None
    media = embedded.get("wp:featuredmedia")
    if not isinstance(media, list) or not media or not isinstance(media[0], dict):
        return None
    url = media[0].get("source_url")
    if not isinstance(url, str):
        return None
    url = url.strip()
    try:
        parts = urlsplit(url)
        if parts.scheme not in {"http", "https"} or not parts.hostname:
            return None
        if parts.username or parts.password:
            return None
    except ValueError:
        return None
    return url


class WordPressCollector:
    def __init__(self, client: httpx.AsyncClient, settings: Settings):
        self.client = client
        self.settings = settings

    async def collect(self):
        per_page = min(100, self.settings.max_blog_articles)
        page = 1
        articles = 0
        while articles < self.settings.max_blog_articles:
            params = {
                "_embed": "wp:featuredmedia",
                "per_page": per_page,
                "page": page,
                "status": "publish",
                "orderby": "date",
                "order": "desc",
            }
            for attempt in range(3):
                try:
                    response = await self.client.get(
                        f"{WORDPRESS_API}/posts", params=params, follow_redirects=False
                    )
                    response.raise_for_status()
                    break
                except httpx.HTTPError as exc:
                    if isinstance(exc, httpx.HTTPStatusError):
                        if exc.response.status_code != 429 and exc.response.status_code < 500:
                            raise
                    if attempt == 2:
                        raise
                    await asyncio.sleep(2**attempt)
            posts = response.json()
            if not isinstance(posts, list):
                raise ValueError("Resposta inválida da API WordPress")
            if not posts:
                return
            for post in posts:
                # O limite conta os posts consultados, inclusive os sem conteúdo útil.
                articles += 1
                try:
                    slug = post["slug"]
                    if not isinstance(slug, str) or not slug or any(c in slug for c in "/?#"):
                        raise ValueError("Slug inválido")
                    source = extract_source(
                        post["content"]["rendered"], f"{SITE}/blog/{slug}", "blog"
                    )
                    source.title = BeautifulSoup(post["title"]["rendered"], "html.parser").get_text(
                        " ", strip=True
                    )
                    source.external_id = str(post["id"])
                    source.image_url = featured_image_url(post)
                    if post.get("date_gmt"):
                        source.published_at = datetime.fromisoformat(
                            post["date_gmt"].replace("Z", "+00:00")
                        ).replace(tzinfo=UTC)
                    yield source
                except (KeyError, TypeError, ValueError):
                    logger.warning("Post WordPress sem conteúdo válido; ignorado")
                if articles >= self.settings.max_blog_articles:
                    return
            total_pages = response.headers.get("X-WP-TotalPages")
            if total_pages is not None:
                if page >= int(total_pages):
                    return
            elif len(posts) < per_page:
                return
            page += 1
            await asyncio.sleep(self.settings.crawl_delay_seconds)


class InstagramCollector:
    def __init__(self, client: httpx.AsyncClient, settings: Settings):
        self.client = client
        self.settings = settings

    async def collect(self):
        token = self.settings.instagram_access_token.get_secret_value()
        user_id = self.settings.instagram_user_id
        if not token or not user_id:
            raise ValueError("Instagram requer INSTAGRAM_ACCESS_TOKEN e INSTAGRAM_USER_ID")
        base = f"https://graph.instagram.com/{self.settings.instagram_api_version}"
        headers = {"Authorization": f"Bearer {token}"}
        profile = await self.client.get(
            f"{base}/{user_id}", params={"fields": "username"}, headers=headers
        )
        profile.raise_for_status()
        if profile.json().get("username", "").lower() != "zionsoftwarehouse":
            raise ValueError("As credenciais devem corresponder ao perfil zionsoftwarehouse")
        params = {"fields": "id,caption,permalink,timestamp", "limit": "100"}
        for _ in range(self.settings.instagram_max_pages):
            response = await self.client.get(
                f"{base}/{user_id}/media", params=params, headers=headers
            )
            response.raise_for_status()
            payload = response.json()
            for item in payload.get("data", []):
                caption = item.get("caption", "").strip()
                if not caption:
                    continue
                yield Source(
                    url=item["permalink"],
                    title=caption.splitlines()[0][:200],
                    text=caption,
                    kind="instagram",
                    external_id=item["id"],
                    published_at=datetime.fromisoformat(item["timestamp"].replace("Z", "+00:00")),
                )
            paging = payload.get("paging", {})
            after = paging.get("cursors", {}).get("after")
            if not paging.get("next") or not after:
                return
            # Nunca segue URLs de paginação que possam incluir tokens nos logs.
            params["after"] = after
        logger.warning("Limite de paginação do Instagram atingido; histórico pode estar incompleto")
        if self.settings.instagram_required:
            raise ValueError("Histórico incompleto: aumente INSTAGRAM_MAX_PAGES")
