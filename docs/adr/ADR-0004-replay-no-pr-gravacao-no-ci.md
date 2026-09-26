# ADR-0004: Replay de cassette no PR, gravação no CI

- **Status:** aceito
- **Data:** 2026-09-26

## Contexto

O gate precisa de respostas do modelo para medir. Chamar o modelo em cada pull request
parece o caminho natural e tem cinco problemas concretos:

1. **Tempo.** Em CPU, 250 casos com três repetições levam dezenas de minutos. Um PR que
   espera meia hora pelo gate vira um PR que alguém aprova sem esperar.
2. **Custo.** Com API paga, cada push de correção de typo cobra a avaliação inteira.
3. **Segredo.** PR vindo de fork não recebe secret. O gate seria vermelho ou pulado
   justamente para quem contribui de fora.
4. **Não determinismo.** O veredito de um PR passaria a depender da disponibilidade e da
   versão do provider naquele minuto.
5. **Proveniência.** Um número gravado no laptop de alguém é a palavra dessa pessoa.

Mockar o modelo não resolve: o gate passaria a medir o mock.

## Decisão

Três modos de provider, escolhidos na linha de comando:

| Modo | Onde roda | O que acontece |
|---|---|---|
| `replay` | todo PR, o dashboard, o default | respostas vêm da cassette versionada, sem rede e sem chave |
| `live` | investigação local | provider real, com orçamento e retry |
| `record` | workflow `live-eval` | provider real, e cada resposta bem-sucedida vai para a cassette |

A cassette é um JSONL de respostas **endereçadas pelo conteúdo do request**: a chave é o
sha256 de modelo, system prompt, texto do caso, temperatura, seed, limite de tokens e
schema de saída. Mudou qualquer um desses campos, a chave muda, o replay não encontra a
resposta e o candidato termina em erro, que bloqueia. Não há fallback silencioso para uma
resposta velha.

A gravação oficial acontece no CI, no workflow `live-eval` (disparo manual):

- Ollama em versão fixa, baixado da release e conferido por sha256 antes de executar;
- cadeia de providers `Recording(Retry(Budget(Ollama)))`: só resposta que deu certo é
  gravada, e o orçamento conta toda chamada que chegou ao modelo;
- gravação parcial é preservada: um run que morre na chamada 400 de 600 não joga fora 400
  respostas;
- a cassette sai com **atestação de proveniência assinada** (Sigstore, via
  `actions/attest-build-provenance`) e entra no repositório por um PR com commit
  assinado, que o `pr-ci` julga como qualquer outro.

## Consequências

**Bom.** O gate do PR roda em segundos, sem chave, sem rede e com o mesmo resultado em
qualquer máquina. Contribuição de fork recebe o mesmo gate. A evidência tem dono
verificável: `gh attestation verify evals/cassettes/<candidato>.jsonl --repo <dono>/<repo>`
confirma qual workflow e qual commit produziram aqueles bytes.

**Ruim.** A evidência é tão recente quanto a última gravação. Um provider hospedado que
troca o modelo por trás do mesmo nome não é detectado até alguém gravar de novo. O PR não
verifica a atestação automaticamente; a verificação é manual (ver
[threat-model.md](../threat-model.md), riscos residuais).

**Revisitar se** existir runner com GPU no plano gratuito (gravar em todo PR ficaria
barato), ou quando a verificação da atestação puder entrar no próprio `eval-gate`.
