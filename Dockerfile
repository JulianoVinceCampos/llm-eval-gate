# Imagem do dashboard. Nenhuma etapa de instalação: o pacote tem zero dependência de
# runtime (ADR-0001), então o código roda direto da árvore de fontes com PYTHONPATH. Sem
# pip, sem build backend baixado da rede, sem lock file para divergir.
#
# Digest e não apenas tag: `3.13-slim` é um ponteiro mutável, e quem publica a imagem pode
# reapontar a tag para outro conteúdo sem que nada aqui mude. O digest fixa o byte exato
# que entrou no build; o Renovate avança o digest (docker:pinDigests) num PR revisável.
FROM python:3.13-slim@sha256:7c61056e61ac89e852de05f3dc6fa51a6dd2181797bceed46aa725dd7cb2cd3b

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/src

WORKDIR /app

RUN useradd --system --no-create-home --uid 10001 --shell /usr/sbin/nologin gate

# Código e evidência, nada mais: datasets, baseline ajustado, referências aceitas, spec,
# prompt, cenários e calibração. Testes, ferramentas e docs ficam fora (.dockerignore).
COPY src ./src
COPY datasets ./datasets
COPY evals ./evals
COPY spec ./spec
COPY prompts ./prompts

USER 10001

EXPOSE 8000

# Lê PORT porque a plataforma de deploy injeta a porta. Sem PORT, cai em 8000.
HEALTHCHECK --interval=30s --timeout=4s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import os,sys,urllib.request; p=os.environ.get('PORT','8000'); sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:'+p+'/api/health',timeout=3).status==200 else 1)"]

# 0.0.0.0 dentro do container é deliberado: o processo só vê o namespace de rede do
# container, e o default de loopback da CLI o deixaria inalcançável. A porta fica fora do
# comando de propósito, para o default ler PORT do ambiente.
CMD ["python", "-m", "llm_eval_gate.cli", "--root", "/app", "serve", "--host", "0.0.0.0"]
