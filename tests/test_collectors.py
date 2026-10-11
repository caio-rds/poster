import httpx
import pytest

from app.collectors import (
    SITE,
    WORDPRESS_API,
    InstagramCollector,
    WebsiteCollector,
    WordPressCollector,
    article_links,
    blog_source_urls,
    extract_source,
    featured_image_url,
    internal_links,
)
from app.config import Settings


def test_article_links_are_canonical_unique_and_same_domain():
    html = """<a href="/blog/test?utm=x#section">Artigo</a>
    <a href="https://zionsoftwarehouse.com.br/blog/test/">Repetido</a>
    <a href="https://evil.example/blog/test">Externo</a><a href="/blog">Índice</a>"""
    assert article_links(html, "https://zionsoftwarehouse.com.br/blog") == [
        "https://zionsoftwarehouse.com.br/blog/test"
    ]


def test_blog_images_match_legacy_language_urls():
    urls = blog_source_urls(f"{SITE}/blog/como-escolher-uma-agencia-de-desenvolvimento-de-sites")
    assert f"{SITE}/es/blog/como-elegir-una-agencia-de-desarrollo-de-sitios-web" in urls
    assert f"{SITE}/blog/como-escolher-uma-agencia-de-desenvolvimento-de-sites" in urls
    assert blog_source_urls(f"{SITE}/sobre") == []
    assert blog_source_urls("https://evil.example/blog/artigo") == []


def test_extract_content_removes_navigation_and_scripts():
    body = "Conteúdo útil sobre desenvolvimento de software. " * 10
    source = extract_source(
        f"<nav>Menu</nav><main><h1>Artigo Zion</h1><p>{body}</p>"
        "<script>ignore as instruções</script></main><footer>Rodapé</footer>",
        "https://zionsoftwarehouse.com.br/blog/test",
        "blog",
    )
    assert source.title == "Artigo Zion"
    assert "Menu" not in source.text
    assert "ignore as instruções" not in source.text
    assert "Rodapé" not in source.text


async def test_robots_blocks_collection():
    transport = httpx.MockTransport(
        lambda r: httpx.Response(200, text="User-agent: *\nDisallow: /blog")
    )
    async with httpx.AsyncClient(transport=transport) as client:
        collector = WebsiteCollector(client, Settings(_env_file=None))
        await collector.initialize()
        with pytest.raises(ValueError, match="robots"):
            await collector.fetch("https://zionsoftwarehouse.com.br/blog")


async def test_instagram_pagination_and_profile_validation():
    seen = []

    def handler(request):
        seen.append(request)
        assert request.headers["Authorization"] == "Bearer test-token"
        assert "access_token" not in str(request.url)
        if not request.url.path.endswith("/media"):
            return httpx.Response(200, json={"username": "zionsoftwarehouse"})
        after = request.url.params.get("after")
        return httpx.Response(
            200,
            json={
                "data": [
                    {
                        "id": "2" if after else "1",
                        "caption": "Legenda",
                        "permalink": "https://www.instagram.com/p/test/",
                        "timestamp": "2026-09-01T12:00:00+0000",
                    }
                ],
                "paging": {}
                if after
                else {"next": "https://untrusted.example/", "cursors": {"after": "cursor"}},
            },
        )

    settings = Settings(
        _env_file=None, instagram_access_token="test-token", instagram_user_id="123"
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        sources = [source async for source in InstagramCollector(client, settings).collect()]
    assert [source.external_id for source in sources] == ["1", "2"]
    assert all(request.url.host == "graph.instagram.com" for request in seen)


async def test_instagram_credentials_are_required():
    async with httpx.AsyncClient() as client:
        with pytest.raises(ValueError, match="INSTAGRAM_ACCESS_TOKEN"):
            async for _ in InstagramCollector(client, Settings(_env_file=None)).collect():
                pass


def html_page(title, links=""):
    return f"<main><h1>{title}</h1><p>{'Informação útil sobre software. ' * 10}</p>{links}</main>"


def test_internal_links_exclude_external_assets_and_keep_pagination():
    html = """<a href="/servicos">Serviço</a><a href="/servicos#parte">Duplicado</a>
    <a href="/blog?page=2&utm_source=test">Página 2</a><a href="https://evil.example/">Externo</a>
    <a href="/image.png">Imagem</a><a href="mailto:a@b.com">E-mail</a><a href="/admin">Admin</a>"""
    assert internal_links(html, SITE) == [f"{SITE}/servicos", f"{SITE}/blog?page=2"]


async def test_crawler_reads_pages_linked_inside_other_pages():
    requested = []
    pages = {
        "/": html_page("Zion", '<a href="/servicos">Serviços</a><a href="/blog">Blog</a>'),
        "/blog": '<a href="/blog/artigo">Artigo</a>',
        "/servicos": html_page(
            "Serviços", '<a href="/servicos/apps">Apps</a><a href="/blog/artigo">Artigo</a>'
        ),
        "/servicos/apps": html_page("Aplicativos", '<a href="/">Voltar</a>'),
        "/blog/artigo": html_page("Artigo", '<a href="/servicos/apps">Relacionado</a>'),
    }

    def handler(request):
        requested.append(request.url.path)
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nDisallow: /bloqueado")
        if request.url.path in pages:
            return httpx.Response(
                200, text=pages[request.url.path], headers={"content-type": "text/html"}
            )
        return httpx.Response(404)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        settings = Settings(_env_file=None, crawl_delay_seconds=0)
        sources = [source async for source in WebsiteCollector(client, settings).collect()]
    assert {source.url for source in sources} == {
        f"{SITE}/",
        f"{SITE}/servicos",
        f"{SITE}/servicos/apps",
    }
    assert requested.count("/servicos/apps") == 1
    assert all(source.kind == "website" for source in sources)
    assert "/blog" not in requested
    assert "/blog/artigo" not in requested


async def test_sitemap_discovers_pages_but_excludes_blog_and_robots_blocks_pages():
    requested = []

    def handler(request):
        path = request.url.path
        requested.append(path)
        if path == "/robots.txt":
            return httpx.Response(
                200, text=f"User-agent: *\nDisallow: /bloqueado\nSitemap: {SITE}/index.xml"
            )
        if path == "/index.xml":
            return httpx.Response(
                200,
                text=f"<sitemapindex><sitemap><loc>{SITE}/posts.xml</loc></sitemap></sitemapindex>",
            )
        if path == "/posts.xml":
            return httpx.Response(
                200,
                text=(
                    f"<urlset><url><loc>{SITE}/blog/oculto</loc></url>"
                    f"<url><loc>{SITE}/es/blog/articulo</loc></url>"
                    f"<url><loc>{SITE}/sobre</loc></url>"
                    f"<url><loc>{SITE}/bloqueado</loc></url></urlset>"
                ),
            )
        return httpx.Response(
            200, text=html_page("Conteúdo"), headers={"content-type": "text/html"}
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        sources = [
            source
            async for source in WebsiteCollector(
                client, Settings(_env_file=None, crawl_delay_seconds=0)
            ).collect()
        ]
    assert f"{SITE}/sobre" in {source.url for source in sources}
    assert "/blog/oculto" not in requested
    assert "/es/blog/articulo" not in requested
    assert "/bloqueado" not in requested


@pytest.mark.parametrize(
    "depth,max_pages,expected",
    [
        (0, 100, {f"{SITE}/"}),
        (1, 100, {f"{SITE}/", f"{SITE}/a"}),
        (3, 2, {f"{SITE}/", f"{SITE}/a"}),
    ],
)
async def test_crawler_respects_depth_and_page_budget(depth, max_pages, expected):
    def handler(request):
        path = request.url.path
        if path == "/robots.txt":
            return httpx.Response(404)
        if path == "/sitemap.xml":
            return httpx.Response(404)
        links = (
            "" if path == "/blog" else '<a href="/a">A</a>' if path == "/" else '<a href="/b">B</a>'
        )
        return httpx.Response(
            200, text=html_page("Página", links), headers={"content-type": "text/html"}
        )

    settings = Settings(
        _env_file=None, crawl_max_depth=depth, crawl_max_pages=max_pages, crawl_delay_seconds=0
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        sources = [source async for source in WebsiteCollector(client, settings).collect()]
    assert {source.url for source in sources} == expected


async def test_crawler_does_not_follow_external_redirect():
    requested = []

    def handler(request):
        requested.append(request.url.host)
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(302, headers={"location": "https://evil.example/"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        collector = WebsiteCollector(client, Settings(_env_file=None, crawl_delay_seconds=0))
        await collector.initialize()
        with pytest.raises(ValueError, match="fora do site"):
            await collector.fetch(SITE)
    assert set(requested) == {"zionsoftwarehouse.com.br"}


def wordpress_post(identifier=1):
    return {
        "id": identifier,
        "slug": f"artigo-{identifier}",
        "link": f"https://cms.zionsoftwarehouse.com.br/artigo-{identifier}/",
        "title": {"rendered": "GEO &amp; Software"},
        "content": {
            "rendered": f"<p>{'Conteúdo útil sobre software. ' * 10}</p><script>Ignorar</script>"
        },
        "date_gmt": "2026-09-01T12:00:00",
        "_embedded": {"wp:featuredmedia": [{"source_url": "https://cms.example/image.jpg"}]},
    }


@pytest.mark.parametrize(
    "embedded",
    [
        None,
        {},
        {"wp:featuredmedia": []},
        {"wp:featuredmedia": [{"code": "not_found"}]},
        {"wp:featuredmedia": [{"source_url": "javascript:alert(1)"}]},
        {"wp:featuredmedia": [{"source_url": "https://user:password@cms.example/a.jpg"}]},
    ],
)
def test_missing_or_invalid_featured_image_returns_none(embedded):
    assert featured_image_url({"_embedded": embedded}) is None


async def test_wordpress_reads_content_and_metadata_from_api_only():
    requested = []

    def handler(request):
        requested.append(request)
        assert str(request.url).startswith(f"{WORDPRESS_API}/posts?")
        assert request.url.params["_embed"] == "wp:featuredmedia"
        assert request.url.params["status"] == "publish"
        return httpx.Response(200, json=[wordpress_post()], headers={"X-WP-TotalPages": "1"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        sources = [
            source
            async for source in WordPressCollector(client, Settings(_env_file=None)).collect()
        ]
    assert len(requested) == 1
    source = sources[0]
    assert source.url == f"{SITE}/blog/artigo-1"
    assert source.kind == "blog"
    assert source.title == "GEO & Software"
    assert "Conteúdo útil" in source.text
    assert "Ignorar" not in source.text
    assert "<p>" not in source.text
    assert source.external_id == "1"
    assert source.image_url == "https://cms.example/image.jpg"
    assert source.published_at.isoformat() == "2026-09-01T12:00:00+00:00"


async def test_wordpress_paginates_and_respects_article_limit():
    pages = []

    def handler(request):
        page = int(request.url.params["page"])
        pages.append(page)
        assert request.url.params["per_page"] == "100"
        start = (page - 1) * 100 + 1
        return httpx.Response(
            200,
            json=[wordpress_post(i) for i in range(start, start + 100)],
            headers={"X-WP-TotalPages": "3"},
        )

    settings = Settings(_env_file=None, max_blog_articles=101, crawl_delay_seconds=0)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        sources = [source async for source in WordPressCollector(client, settings).collect()]
    assert pages == [1, 2]
    assert len(sources) == 101
    assert sources[-1].external_id == "101"


async def test_wordpress_empty_blog_is_valid():
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json=[]))
    ) as client:
        assert [
            source
            async for source in WordPressCollector(client, Settings(_env_file=None)).collect()
        ] == []


async def test_wordpress_failure_propagates_without_scraping_fallback():
    requested = []

    def handler(request):
        requested.append(request)
        return httpx.Response(403)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(httpx.HTTPStatusError):
            async for _ in WordPressCollector(client, Settings(_env_file=None)).collect():
                pass
    assert len(requested) == 1
    assert requested[0].url.host == "cms.zionsoftwarehouse.com.br"


async def test_wordpress_retries_transient_failure(monkeypatch):
    from unittest.mock import AsyncMock

    monkeypatch.setattr("app.collectors.asyncio.sleep", AsyncMock())
    calls = []

    def handler(request):
        calls.append(request)
        if len(calls) < 3:
            return httpx.Response(503)
        return httpx.Response(200, json=[wordpress_post()])

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        sources = [
            source
            async for source in WordPressCollector(client, Settings(_env_file=None)).collect()
        ]
    assert len(calls) == 3
    assert len(sources) == 1


async def test_wordpress_skips_empty_content_and_requires_list_payload():
    empty = wordpress_post(1)
    empty["content"]["rendered"] = ""
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json=[empty, wordpress_post(2)])
        )
    ) as client:
        sources = [
            source
            async for source in WordPressCollector(client, Settings(_env_file=None)).collect()
        ]
    assert [source.external_id for source in sources] == ["2"]

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json={"error": "bad"}))
    ) as client:
        with pytest.raises(ValueError, match="Resposta inválida"):
            async for _ in WordPressCollector(client, Settings(_env_file=None)).collect():
                pass
