# Publicar no cPanel

Esta aplicação usa Python 3.12 e Passenger. O worker inicia junto com a aplicação,
em segundo plano, e repete a coleta periodicamente.
Não precisa de Docker nem de um processo Uvicorn permanente.

## Instalação

1. Envie os arquivos para uma pasta como `/home/SEU_USUARIO/zion`, fora de `public_html`.
2. No cPanel, abra **Setup Python App** ou **Application Manager**, conforme a hospedagem.
   Selecione Python **3.12** e configure a aplicação para essa pasta e sua URL.
3. Configure **Application startup file** como `passenger_wsgi.py` e **Application entry point**
   como `application`. Mantenha o arquivo enviado neste projeto caso o painel crie outro.
4. Ative o virtualenv indicado pelo painel. No Terminal/SSH, dentro da pasta da aplicação:

```sh
python --version
python -m pip install -r requirements.txt
```

O `requirements.txt` fixa as dependências e contém o adaptador ASGI/WSGI `a2wsgi`.
`passenger_wsgi.py` inicializa o FastAPI e o MongoDB no mesmo event loop do adaptador,
uma vez por processo Passenger. A inicialização é agendada assim que o módulo Passenger
é carregado, antes da primeira requisição. O worker começa sem bloquear o atendimento HTTP.
Se o MongoDB falhar, as rotas respondem 503 e o worker tenta novamente após 30 segundos.

## Configuração

Envie seu `.env` junto à aplicação, preservando `API_KEY` e `OPENAI_API_KEY`:

```dotenv
MONGODB_URI=URI_ATUAL_OBTIDA_NO_ATLAS
MONGODB_DATABASE=zion_posts_93e3defe4e42
API_KEY=SUA_CHAVE_GERADA
OPENAI_API_KEY=SUA_CHAVE_OPENAI
OPENAI_MODEL=gpt-4.1-mini
OPENAI_EMBEDDING_MODEL=text-embedding-3-small
CORS_ALLOW_ORIGINS=["https://zionsoftwarehouse.com.br","https://www.zionsoftwarehouse.com.br"]
CORS_MAX_AGE=600
INSTAGRAM_REQUIRED=false
WORKER_ENABLED=true
WORKER_INTERVAL_SECONDS=3600
WORKER_RETRY_SECONDS=30
CRAWL_MAX_PAGES=100
CRAWL_MAX_DEPTH=3
CRAWL_DELAY_SECONDS=1
```

O arquivo é carregado pela localização do projeto, inclusive quando Passenger ou cron
iniciam em outra pasta. Variáveis configuradas no painel têm precedência sobre o `.env`;
não mantenha uma URI antiga no painel.

No Atlas, copie a URI em **Connect → Drivers** e autorize em **Network Access** o IP de saída
da hospedagem. A conta MongoDB precisa ler e escrever na database escolhida e criar índices.
Confirme com a hospedagem a disponibilidade do Python 3.12/Passenger e de saída TCP para
o MongoDB Atlas. Não é necessário instalar MongoDB no cPanel.

Execute o diagnóstico no virtualenv antes de reiniciar a aplicação:

```sh
python -m app.check
```

Ele verifica API key, conexão MongoDB, quantidade de posts prontos e acesso aos modelos
OpenAI, sem imprimir credenciais. Se falhar, retorna código de saída 1.

## Worker automático e execução manual

Com `WORKER_ENABLED=true`, basta iniciar/reiniciar a aplicação no cPanel. O worker faz
o primeiro ciclo imediatamente e os seguintes a cada `WORKER_INTERVAL_SECONDS`.
Ele consulta somente artigos com imagem destacada para gerar novos posts
pela API WordPress em `https://cms.zionsoftwarehouse.com.br/wp-json/wp/v2/posts`.
Páginas institucionais não geram novos posts. O worker prepara posts originais
no MongoDB. O lock coordena execuções de processos Passenger diferentes.

Para executar um ciclo manual, use:

```sh
python worker_job.py
```

Esse comando executa um ciclo, grava os posts novos no MongoDB e encerra. Falhas retornam
código 1 com uma mensagem operacional. A API continua consultando/atualizando os posts
enquanto seu worker automático trabalha em segundo plano.

O worker acompanha o processo Passenger: se a hospedagem encerra esse processo quando
fica ocioso, o worker também para e volta ao subir o processo. Para manter coletas mesmo
nesse período, um **Cron Jobs** pode servir como complemento. Configure `0 * * * *` e use
o comando abaixo, substituindo os caminhos pelos da sua conta e do virtualenv do painel:

```sh
/home/SEU_USUARIO/virtualenv/zion/3.12/bin/python /home/SEU_USUARIO/zion/worker_job.py >> /home/SEU_USUARIO/zion-worker.log 2>&1
```

O cron executa um ciclo por chamada. Para operar exclusivamente por cron, configure
`WORKER_ENABLED=false`. O lock MongoDB evita duas coletas simultâneas.

## Reiniciar e verificar

Use **Restart** no painel após atualizar código ou `.env`. Se a hospedagem usa o reinício
por arquivo Passenger, execute na pasta do projeto:

```sh
mkdir -p tmp
touch tmp/restart.txt
```

Com a API publicada, abra `/docs` na URL configurada e use **Authorize** com sua API key.
O Swagger envia a chave no header `apikey`. Outros clientes devem enviar o mesmo header.
Adicione a origem exata do seu frontend a `CORS_ALLOW_ORIGINS` no `.env` e reinicie a aplicação.
O preflight `OPTIONS` é atendido sem chave e sem inicializar o MongoDB; GET/PATCH exigem a chave.
Verifique `/posts/next`, `/posts` e o PATCH de status. Sem a chave, as três rotas respondem
401. Com o worker executado e conteúdo aprovado, `/posts/next` retorna o post pronto.
Se o diagnóstico retornar 503/erro MongoDB, os posts ainda não estão disponíveis.

O Instagram exige `INSTAGRAM_USER_ID` e `INSTAGRAM_ACCESS_TOKEN` da conta profissional
autorizada. Sem essas credenciais e com `INSTAGRAM_REQUIRED=false`, a geração usa site/blog;
ela não compara com as publicações que só existem no Instagram.

Referências: [Python WSGI no cPanel](https://docs.cpanel.net/knowledge-base/web-services/how-to-install-a-python-wsgi-application/)
e [adaptador a2wsgi](https://github.com/abersheeran/a2wsgi).
