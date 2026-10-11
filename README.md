# Zion Posts API

API em **Python 3.12 + FastAPI + MongoDB**, com worker independente que coleta conteúdo da
Zion Software House e utiliza um agente editorial OpenAI para preparar posts em português.

## Executar no cPanel

Use Python 3.12 no **Setup Python App**, startup file `passenger_wsgi.py` e entry point
`application`. Instale as dependências no virtualenv do painel:

```sh
python -m pip install -r requirements.txt
python -m app.check
python worker_job.py
```

O worker inicia automaticamente com a API (`WORKER_ENABLED=true`), coleta imediatamente
e repete a cada `WORKER_INTERVAL_SECONDS`. `worker_job.py` permanece como execução manual
de um ciclo ou como complemento via cron.
O guia [CPANEL.md](CPANEL.md) contém instalação, configuração, cron e verificação.
O FastAPI é servido pelo adaptador WSGI; a coleta trabalha em segundo plano no processo
Passenger, com encerramento junto à aplicação. A API inicia mesmo com MongoDB inacessível;
nesse caso, responde 503 nas operações de banco e o worker tenta novamente.

`API_KEY` é obrigatória: as três rotas de posts exigem o header `apikey`.
A API recusa iniciar quando a chave não está configurada; chave ausente ou incorreta
retorna 401. No Swagger `/docs`, use **Authorize** para informar a chave.

## Render

No Web Service, use `pip install -r requirements.txt` como Build Command e
`uvicorn app.main:app --host 0.0.0.0 --port $PORT` como Start Command.
Configure as variáveis do `.env.example` em Environment, incluindo `API_KEY`,
`MONGODB_URI` e `OPENAI_API_KEY` para o worker.

Defina **Health Check Path** como `/health`. `GET /health` retorna HTTP 200 com
`{"status":"ok"}` sem exigir `apikey` nem consultar MongoDB, OpenAI ou CMS.
Também aceita `HEAD /health`. É uma verificação de que a API responde;
não verifica a disponibilidade das dependências nem o funcionamento do worker.

A rota por si só não evita a inatividade. No plano grátis, a Render suspende o serviço
após 15 minutos sem tráfego de entrada. Configure um monitor HTTP externo para consultar
`https://SEU-SERVICO.onrender.com/health` a cada 5 minutos, sem headers de autenticação.
O health check interno da Render não substitui essas requisições externas.
Consulte a [documentação do plano grátis](https://render.com/docs/free).

## Docker opcional para desenvolvimento

```powershell
Copy-Item .env.example .env
# Preencha API_KEY, OPENAI_API_KEY e as credenciais Instagram no .env.
docker compose up --build -d
docker compose logs -f worker
```

Acesse o Swagger em http://localhost:8000/docs. O MongoDB persiste no volume `mongodb_data`.
A API funciona sem chave OpenAI; somente o worker precisa dela. Os serviços Docker são
expostos apenas em localhost. No Docker com MongoDB local, defina
`MONGODB_URI=mongodb://mongodb:27017` no `.env`.

API e worker usam `MONGODB_URI` do `.env`, inclusive para clusters externos MongoDB Atlas.
O serviço MongoDB local do Compose permanece disponível, mas seus dados não são usados
quando a URI aponta para Atlas. `MONGODB_DATABASE` define a database de posts nesse cluster.

## Rotas

| Método | Rota | Comportamento |
| --- | --- | --- |
| GET / HEAD | `/health` | Pública; HTTP 200 indica que a API está respondendo. |
| GET | `/posts/next` | Marca o post `ready` mais antigo como `posted` e o retorna; HTTP 200 com `null` se a fila estiver vazia. |
| GET | `/posts` | Lista todos os posts, paginados; filtros `status=ready` ou `status=posted`. |
| PATCH | `/posts/{id}/status` | Recebe `{"status":"posted"}` e registra a publicação. |

```powershell
$headers = @{'apikey'='sua-chave-do-env'}
Invoke-RestMethod http://localhost:8000/posts/next -Headers $headers
Invoke-RestMethod 'http://localhost:8000/posts?status=ready&offset=0&limit=20' -Headers $headers
Invoke-RestMethod -Method Patch -Uri 'http://localhost:8000/posts/SEU_ID/status' `
  -Headers $headers -ContentType 'application/json' -Body '{"status":"posted"}'
```

Para gerar uma nova chave, execute `python -c "import secrets; print(secrets.token_urlsafe(32))"`,
atualize `API_KEY` no `.env` e reinicie a aplicação pelo painel cPanel.
O `.env` está ignorado pelo Git; não coloque a chave no código ou no `.env.example`.

O header de autenticação é `apikey` (não `X-API-Key` nem parâmetro de URL). A chave gerada
continua sendo a mesma `API_KEY` do `.env`. Exemplo para um cliente JavaScript:

```javascript
const resposta = await fetch(`${apiUrl}/posts/next`, {
  headers: { apikey: apiKey }
});
```

O CORS já permite `https://zionsoftwarehouse.com.br` e `https://www.zionsoftwarehouse.com.br`.
Configure outros frontends no `.env` usando uma lista JSON de origens, sem caminho ou barra final:

```dotenv
CORS_ALLOW_ORIGINS=["https://zionsoftwarehouse.com.br","https://www.zionsoftwarehouse.com.br"]
CORS_MAX_AGE=600
```

São permitidos `GET`, `PATCH` e `OPTIONS`, com headers `apikey` e `Content-Type`.
O preflight `OPTIONS` dispensa API key; as requisições às três rotas continuam autenticadas.
No Passenger, o preflight responde antes da conexão MongoDB. Cookies não são utilizados.
Após alterar as origens, reinicie a aplicação no cPanel.

`limit` aceita 1 a 100; `offset` começa em zero. Use páginas sucessivas para percorrer todos.
O PATCH é idempotente: chamadas repetidas preservam a data da primeira publicação.
IDs inválidos ou status diferentes de `posted` retornam 422; posts inexistentes retornam 404.
Falhas de MongoDB durante as requisições retornam 503.

`GET /posts/next` consome a fila: seleciona e marca o post como `posted` em uma operação
atômica, preenchendo `posted_at` e `updated_at` antes de retorná-lo. Requisições simultâneas
recebem posts diferentes. A fila vazia retorna HTTP 200 com o JSON literal `null`.
O consumidor não precisa chamar o PATCH após publicar. O status registra a retirada do post;
se a publicação externa falhar ou a resposta se perder, ele permanece `posted` e não volta
automaticamente à fila. Evite pré-busca, cache e chamadas de teste nessa rota em produção;
use `/posts` para consultar sem consumir.

Exemplo de post retornado:

```json
{
  "id": "507f1f77bcf86cd799439011",
  "title": "Sua empresa precisa de software sob medida?",
  "caption": "Sua empresa precisa de software sob medida?\n\nUma legenda curta, com chamada para ação.",
  "image_url": "https://SEU-SERVICO.onrender.com/posts/507f1f77bcf86cd799439011/image.jpg",
  "image_source_url": "https://cms.zionsoftwarehouse.com.br/wp-content/uploads/imagem-do-artigo.webp",
  "image_brief": "Arte com fundo azul, título em destaque e até três pontos de apoio.",
  "topic": "Quando adotar software sob medida",
  "source_urls": ["https://zionsoftwarehouse.com.br/blog/guia-completo-geo-2026"],
  "status": "ready",
  "created_at": "2026-10-01T12:00:00Z",
  "updated_at": "2026-10-01T12:00:00Z",
  "posted_at": null,
  "origin": "blog"
}
```

O prompt do agente em `app/agent.py` define público, objetivo, estrutura, tom, originalidade
e limites factuais. A legenda deve ser o texto final, com uma chamada para ação e sem
hashtags; o agente valida as fontes e os campos antes de gravar.
As legendas usam parágrafos curtos separados por uma linha em branco, chamada para ação
em um bloco próprio. O worker também separa automaticamente blocos
densos por frases, preservando listas e quebras já existentes.
Os textos não incluem emojis: título, legenda, tema e briefing são limpos na
validação do modelo, tanto na geração quanto nas respostas da API. Acentos, links e listas
de texto são preservados.
O campo `hashtags` não é gerado nem retornado pela API. As hashtags também são removidas
da legenda ao gerar novos posts e ao retornar posts antigos, sem modificar o histórico salvo.
Na Zapier, use apenas `caption` no campo de texto da publicação: ele já inclui o título,
uma linha em branco e a legenda, com no máximo 700 caracteres no total. Não concatene
`title` novamente. Posts antigos são encurtados na resposta sem alterar o conteúdo salvo,
priorizando frases completas e a chamada final quando ela cabe no limite.
Novas gerações usam títulos de até 100 caracteres e legendas de até 500 caracteres.
O conteúdo pronto inclui título, texto curto de publicação sem hashtags, tema, fontes
e `image_url`, com uma URL pública da API que entrega a imagem em JPEG. O coletor extrai
`_embedded["wp:featuredmedia"][0].source_url` da resposta WordPress e o worker salva essa URL
diretamente. O agente também gera `image_brief`, com composição, cores, título na arte e
até três pontos de apoio, sem emojis ou hashtags, com no máximo 1200 caracteres.
O briefing é separado de `caption` e pode ser usado na etapa de criação de arte.
Todas as respostas de posts incluem
`image_url`; na leitura de posts antigos sem imagem, usa a imagem institucional definida em
`DEFAULT_POST_IMAGE_URL` (padrão: `https://zionsoftwarehouse.com.br/og-image.webp`).
O fallback também é aplicado na leitura de posts antigos que ainda não têm imagem salva.
Na Render, configure `PUBLIC_API_URL=https://SEU-SERVICO.onrender.com` para garantir
links HTTPS, mesmo atrás de um proxy. Sem essa variável, o link usa a base da requisição.
No campo de imagem do Zapier, use `image_url` da nova resposta. `image_source_url`
preserva a URL original do blog para diagnóstico. Atualize a amostra do Zapier usando
`GET /posts?status=ready`, pois `/posts/next` consome a fila.
`GET` e `HEAD /posts/{id}/image.jpg` são públicos: plataformas externas baixam imagens
sem enviar `apikey`. Essas rotas apenas leem a imagem e não alteram o post. O servidor
baixa somente imagens dos domínios da Zion, converte WebP/PNG para JPEG, remove metadados
e limita o maior lado a 1440 pixels. Uma falha no CMS retorna HTTP 502; a conversão
não elimina indisponibilidade ou bloqueios de rede no servidor de origem.
O download usa `curl-cffi` com perfil de conexão Chrome, pois o CMS encerra conexões
do cliente HTTP padrão. A conexão mantém HTTPS e validação de certificado, incluindo
os redirecionamentos. JPEGs baixados com sucesso ficam na coleção
`post_images` do MongoDB, separados dos posts; downloads seguintes usam essa cópia,
inclusive após reiniciar a Render. Falhas de download não são armazenadas no cache.
Novos posts exigem briefing; posts antigos mantêm seus briefings salvos e só retornam `null`
se não tiverem esse campo. Embeddings e hashes ficam internos no MongoDB.

A cada coleta, o worker também sincroniza a imagem dos posts existentes que citam a mesma
URL de artigo, inclusive fontes já processadas e URLs antigas em inglês/espanhol,
mesmo quando foram cadastradas como `website`. Essa sincronização não altera status,
legenda, `posted_at` ou `updated_at`, nem gera posts novamente. Artigos fora do limite de
`MAX_BLOG_ARTICLES` só terão suas imagens sincronizadas quando forem coletados.
Uma resposta do CMS sem imagem não apaga uma URL já salva.

## Worker e deduplicação

1. Atualiza os embeddings antigos se `OPENAI_EMBEDDING_MODEL` mudar.
2. Coleta o histórico do perfil Instagram autenticado e salva suas legendas como `posted`.
3. Coleta artigos pela API REST do WordPress no CMS, sem visitar páginas HTML do blog.
   Só gera posts quando o artigo tem imagem destacada; páginas institucionais não geram posts.
   Remove scripts, navegação e rodapés; limita texto por fonte a 24 mil caracteres.
4. Busca os cinco posts mais próximos da fonte e pede ao agente um recorte novo e fundamentado.
5. Compara o post gerado com todos os posts do mesmo modelo de embedding, inclusive publicados.
   Rejeita similaridade de cosseno a partir de `SIMILARITY_THRESHOLD`; abaixo desse limiar,
   o agente também analisa os cinco vizinhos para identificar paráfrases da mesma ideia.
6. Persiste o conteúdo aprovado como `ready` e registra o resultado da fonte.

Hash normalizado de legenda e chave de origem têm índices únicos. Fontes já processadas,
incluindo descartadas, não geram novamente enquanto seu conteúdo não mudar. Falhas de geração
ficam elegíveis para nova tentativa no ciclo seguinte. A coleção `sources` registra motivos
de descarte; `posts` guarda os posts e seus embeddings. Um lock MongoDB com expiração e
renovação coordena os workers; a perda do lock cancela o processamento.

Posts arquivados (`archived=true`) ficam fora da listagem e da fila da API. Os que já foram
publicados continuam no histórico de deduplicação; rascunhos arquivados não bloqueiam novas
gerações. Arquivar não altera o status de publicação original.

A comparação percorre todo o histórico em memória limitada, com custo O(n) por consulta.
Funciona em MongoDB local, sem Atlas Vector Search. Para grandes volumes, substitua essa
busca por índice vetorial. A deduplicação semântica é heurística: calibre o limiar com os posts
reais; ela pode descartar recortes próximos ou deixar passar repetições.

O worker inicia junto com a API, roda imediatamente e repete a cada `WORKER_INTERVAL_SECONDS`
(padrão: uma hora). Falhas tentam novamente após `WORKER_RETRY_SECONDS` (30 segundos).
Gera até um post por artigo novo com imagem destacada. Não usa imagem institucional para
novos posts. O coletor institucional permanece disponível apenas para diagnóstico.
Nesse diagnóstico, `CRAWL_MAX_PAGES` limita as páginas a considerar (100),
`CRAWL_MAX_DEPTH` limita a profundidade dos links (3) e `MAX_BLOG_ARTICLES` limita artigos (30).
URLs de sitemap também são pontos iniciais da busca. `CRAWL_DELAY_SECONDS` define o intervalo
mínimo entre requests (1 segundo), respeitando limites maiores em `robots.txt`.
Links de paginação `page`, `p` e `offset` são preservados; fragmentos e rastreamento são removidos.
Links externos, arquivos estáticos e páginas de login/administração são ignorados.
O coletor lê HTML retornado pelas páginas institucionais; não renderiza JavaScript.
As URLs do blog são excluídas dessa varredura, inclusive quando aparecem em links e sitemaps.
Os artigos são coletados diretamente de
`https://cms.zionsoftwarehouse.com.br/wp-json/wp/v2/posts`, com `_embed=wp:featuredmedia`,
paginação por `page`/`per_page` e somente posts publicados, do mais recente ao mais antigo.
`MAX_BLOG_ARTICLES` limita os posts consultados por ciclo, independentemente de `CRAWL_MAX_PAGES`.
Título e conteúdo vêm de `title.rendered` e `content.rendered`; o HTML é convertido em texto.
O ID e a data UTC são importados, e a URL pública da fonte segue o formato
`https://zionsoftwarehouse.com.br/blog/{slug}`. O slug do exemplo `guia-completo-geo-2026`
é coletado junto com os demais posts, sem fixar a coleta em um único artigo.
Falhas da API são propagadas para nova tentativa do worker; não há retorno à varredura do blog.

No cPanel, o worker acompanha a vida do processo Passenger. Consulte o guia de implantação
para a opção de cron caso a hospedagem encerre processos ociosos.

## Instagram

Configure uma integração Meta **Instagram API com Instagram Login** para a conta profissional
`zionsoftwarehouse`, com permissão de leitura do perfil e das mídias, e preencha:

```dotenv
INSTAGRAM_USER_ID=seu_id_da_conta
INSTAGRAM_ACCESS_TOKEN=seu_token
INSTAGRAM_API_VERSION=v25.0
INSTAGRAM_REQUIRED=true
```

O worker verifica o username e usa `graph.instagram.com` com Bearer token. Leia as permissões
e o fluxo de obtenção/renovação do token na documentação Meta; o aplicativo não faz OAuth nem
renova tokens automaticamente. A versão é configurável conforme sua integração.
Ele importa legendas e URLs, sem baixar imagens ou transcrever vídeos. Mídias sem legenda
são ignoradas. Para históricos grandes, ajuste `INSTAGRAM_MAX_PAGES` (100 mídias por página).

Por padrão, `INSTAGRAM_REQUIRED=false` permite coletar site/blog sem acesso ao Instagram.
Nesse modo, não há garantia de comparação com todo o histórico publicado no perfil. Com
`true`, falta de credenciais, falhas ou limite de paginação impedem a geração do site/blog
naquele ciclo. Não há scraping de páginas de login do Instagram.

## Desenvolvimento local

Com [uv](https://docs.astral.sh/uv/), o arquivo `.python-version` seleciona Python 3.12:

```powershell
uv sync --extra dev --frozen
Copy-Item .env.example .env
docker compose up -d mongodb
uv run uvicorn app.main:app --reload
# O worker já inicia junto com a API. Para rodá-lo separadamente, use
# WORKER_ENABLED=false na API e execute em outro terminal:
uv run python -m app.worker
# Para apenas um ciclo:
uv run python -m app.worker --once
```

Use `MONGODB_URI=mongodb://localhost:27017` no modo local. Para MongoDB externo, altere URI
e credenciais. As datas são armazenadas em UTC. `uv.lock` fixa as versões das dependências
utilizadas também pelo Docker.

## Validação

```powershell
uv run pytest -q
uv run ruff check app tests passenger_wsgi.py worker_job.py
uv run ruff format --check app tests passenger_wsgi.py worker_job.py
```

Os testes isolam MongoDB, OpenAI e HTTP usando mocks. Cobrem contratos das rotas, autenticação,
paginação, idempotência, rejeição de duplicatas, recuperação de falhas, perda de lock,
extração de HTML, robots.txt, coleta e paginação WordPress, paginação Instagram e validação
das respostas do agente.
Eles não substituem um teste integrado com MongoDB e credenciais reais.

## Referências da integração

- [OpenAI: Structured Outputs](https://developers.openai.com/api/docs/guides/structured-outputs)
- [OpenAI: embeddings](https://developers.openai.com/api/docs/guides/embeddings)
- [MongoDB: PyMongo Async](https://www.mongodb.com/docs/languages/python/pymongo-driver/current/reference/migration/)
- [Meta: Instagram API com Instagram Login](https://developers.facebook.com/docs/instagram-platform/instagram-api-with-instagram-login/)
