import socket
import ssl

import httpx
from openai import APIConnectionError, AuthenticationError, RateLimitError
from pydantic import ValidationError
from pymongo.errors import OperationFailure, ServerSelectionTimeoutError

# Somente mensagens fixas do aplicativo podem aparecer nos logs. Nunca repasse
# str(exc) indiscriminadamente: exceções externas podem conter tokens e conteúdo.
KNOWN_VALUE_ERRORS = {
    "Defina OPENAI_API_KEY para executar o worker": "OpenAI: configure OPENAI_API_KEY",
    "OpenAI não retornou um post válido": "Geração: OpenAI não retornou um post válido",
    "Post gerado contém fonte não fornecida": "Geração: post contém fonte não fornecida",
    "Post deve conter entre 3 e 6 hashtags distintas": (
        "Geração: post deve conter entre 3 e 6 hashtags distintas"
    ),
    "Hashtags inválidas ou ausentes na legenda": (
        "Geração: hashtags inválidas ou ausentes na legenda"
    ),
    "Post contém campos vazios": "Geração: post contém campos vazios",
    "Legenda deve ser texto pronto para publicação": "Geração: legenda contém cercas de código",
    "OpenAI não retornou decisão de duplicidade": (
        "Deduplicação: OpenAI não retornou decisão de duplicidade"
    ),
    "OpenAI retornou um ID de duplicata desconhecido": (
        "Deduplicação: OpenAI retornou um ID de duplicata desconhecido"
    ),
    "Página sem conteúdo suficiente; pode exigir renderização JavaScript": (
        "Coleta: página sem conteúdo suficiente; pode exigir renderização JavaScript"
    ),
    "URL fora do site": "Coleta: URL fora do site permitido",
    "URL bloqueada por robots.txt": "Coleta: URL bloqueada por robots.txt",
    "Redirecionamento inválido ou para fora do site": (
        "Coleta: redirecionamento inválido ou para fora do site"
    ),
    "Limite de redirecionamentos": "Coleta: limite de redirecionamentos atingido",
    "URL não contém uma página HTML": "Coleta: URL não contém uma página HTML",
    "Nenhuma página com conteúdo útil foi coletada": (
        "Coleta: nenhuma página com conteúdo útil foi coletada"
    ),
    "Resposta inválida da API WordPress": "Blog Zion: API WordPress retornou formato inválido",
}
EDITORIAL_FIELDS = {
    "should_create",
    "reason",
    "post",
    "title",
    "caption",
    "hashtags",
    "image_brief",
    "topic",
    "source_urls",
    "is_duplicate",
    "similar_post_id",
}


def network_hint(exc: Exception, service: str) -> str:
    causes = []
    current = exc
    while current is not None and len(causes) < 10:
        if any(current is cause for cause in causes):
            break
        causes.append(current)
        current = current.__cause__ or current.__context__
    if any(
        isinstance(cause, ssl.SSLCertVerificationError) or "CERTIFICATE_VERIFY_FAILED" in str(cause)
        for cause in causes
    ):
        return f"{service}: certificado TLS recusado; confira a cadeia e os certificados CA"
    if any(isinstance(cause, socket.gaierror) for cause in causes):
        return f"{service}: falha de DNS; confira a resolução do domínio na hospedagem"
    if any(isinstance(cause, ConnectionRefusedError) for cause in causes):
        return f"{service}: conexão recusada; confira o destino e a saída HTTPS na porta 443"
    if any(isinstance(cause, ConnectionResetError) for cause in causes):
        return f"{service}: conexão interrompida; confira firewall, proxy e bloqueios no destino"
    if any(isinstance(cause, httpx.TimeoutException) for cause in causes):
        return f"{service}: tempo de conexão ou resposta excedido; confira rede e disponibilidade"
    return f"{service}: sem conexão HTTPS; confira DNS, TLS, proxy e saída na porta 443"


def http_service(exc: httpx.RequestError | httpx.HTTPStatusError) -> str:
    try:
        host = exc.request.url.host
    except RuntimeError:
        return "HTTP"
    # Não imprime host arbitrário, URL, query string, headers nem credenciais.
    return {
        "zionsoftwarehouse.com.br": "Site Zion",
        "www.zionsoftwarehouse.com.br": "Site Zion",
        "cms.zionsoftwarehouse.com.br": "Blog Zion",
        "api.openai.com": "OpenAI",
        "graph.instagram.com": "Instagram",
    }.get(host, "HTTP")


def failure_hint(exc: Exception) -> str:
    """Mensagens operacionais sem imprimir URIs, tokens ou conteúdo de exceções."""
    if isinstance(exc, APIConnectionError):
        return network_hint(exc, "OpenAI")
    if isinstance(exc, httpx.ProxyError):
        return f"{http_service(exc)}: falha no proxy; confira a configuração de proxy do processo"
    if isinstance(exc, (httpx.ConnectError, httpx.TimeoutException)):
        return network_hint(exc, http_service(exc))
    if isinstance(exc, httpx.HTTPStatusError):
        return f"{http_service(exc)}: resposta HTTP {exc.response.status_code}"
    if isinstance(exc, ValidationError):
        fields = sorted(
            {
                part
                for error in exc.errors(
                    include_input=False, include_context=False, include_url=False
                )
                for part in error["loc"]
                if isinstance(part, str) and part in EDITORIAL_FIELDS
            }
        )
        detail = f"; campos: {', '.join(fields)}" if fields else ""
        return f"Validação: dados fora do formato ou dos limites esperados{detail}"
    if type(exc) is ValueError and str(exc) in KNOWN_VALUE_ERRORS:
        return KNOWN_VALUE_ERRORS[str(exc)]
    if isinstance(exc, ServerSelectionTimeoutError):
        if any(word in str(exc).lower() for word in ("getaddrinfo", "name or service", "dns")):
            return "MongoDB: hosts não resolvem no DNS; confira a URI atual no Atlas"
        return "MongoDB: sem conexão; confira cluster ativo, Network Access e porta 27017"
    if isinstance(exc, OperationFailure):
        if exc.code == 18:
            return "MongoDB: autenticação recusada; confira usuário e senha na URI"
        return "MongoDB: operação recusada; confira permissões na database"
    if isinstance(exc, AuthenticationError):
        return "OpenAI: chave recusada; confira OPENAI_API_KEY"
    if isinstance(exc, RateLimitError):
        return "OpenAI: limite de requisições ou créditos; confira o projeto na plataforma"
    return f"Falha do tipo {type(exc).__name__}; confira a configuração do serviço"
