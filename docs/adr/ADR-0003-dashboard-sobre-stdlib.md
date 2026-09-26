# ADR-0003: Dashboard sobre a biblioteca padrão

- **Status:** aceito
- **Data:** 2026-09-26
- **Herda de:** ADR-0003 do [postmortem-miner](https://github.com/JulianoVinceCampos/postmortem-miner)
- **Revisita:** [ADR-0001](ADR-0001-zero-dependencia-de-runtime.md)

## Contexto

O relatório do gate em markdown responde bem "este PR passou?". Responde mal as perguntas
que vêm depois de um vermelho: quais casos puxaram o número para baixo, se a regressão está
concentrada numa classe, o que aconteceria com outra margem ou com o dobro de casos. Essas
perguntas são exploração, e exploração pede tela.

Há também o argumento de avaliação: um projeto público sem tela acessível obriga quem
avalia a clonar o repositório para formar opinião.

FastAPI com uvicorn resolveria em uma tarde, ao custo de duas dependências diretas, várias
transitivas, e do argumento central do [ADR-0001](ADR-0001-zero-dependencia-de-runtime.md).

## Decisão

Mesmo desenho do postmortem-miner:

- `http.server.ThreadingHTTPServer`, roteamento por tabela sobre `urlparse(path).path`.
- Handlers de rota são **funções puras** que devolvem `(status, payload)`. O `Handler` HTTP é
  casca fina, então a API inteira é testável sem abrir socket.
- Sessão em cookie assinado com HMAC-SHA256, segredo de `secrets.token_bytes(32)` gerado na
  subida. Sem store de sessão.
- Frontend em HTML, CSS e JavaScript sem framework e sem CDN. Todo valor vindo da API é
  escrito com `textContent`, nunca `innerHTML`, e os gráficos são SVG gerado no navegador.
- O estado é montado **uma vez** na subida, em modo replay: nenhuma requisição chama modelo.

A única computação exposta à rede é o *what-if* (redecidir o gate com outra margem, outro
alfa ou outro tamanho de amostra). As entradas são validadas contra uma grade fechada e o
resultado vai para um cache LRU, para que o endpoint não vire amplificador de CPU.

## Consequências

**Bom.** `dependencies = []` continua verdadeiro. A imagem não tem etapa de instalação. A
CSP pode ser estrita (`default-src 'none'`, sem `unsafe-inline`), porque não há script de
terceiro nem estilo inline.

**Ruim.** `http.server` não é servidor de produção: TLS fica no proxy da plataforma, e o
limite de tentativas de login é implementado à mão (janela deslizante por cliente, memória
limitada). Com o segredo gerado na subida, reiniciar o processo encerra todas as sessões,
o que é aceitável para uma instância de demonstração.

**Revisitar se** o dashboard passar a escrever algo (aceitar referência pela tela, por
exemplo): aí entram CSRF, concorrência de escrita e trilha de auditoria, e o `http.server`
deixa de bastar.
