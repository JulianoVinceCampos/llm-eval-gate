# Contribuindo

## Setup

```bash
make install     # install editável com extras de dev, mais os hooks de pre-commit
make check       # sanitize, lint, testes e o gate, na mesma ordem do CI
```

Sem `make` (Windows, por exemplo), o mesmo roteiro roda com `python tools/tasks.py check`.

Python 3.11 ou mais novo. O pacote não tem dependência de runtime, e isso é restrição de
desenho, não acaso ([ADR-0001](docs/adr/ADR-0001-zero-dependencia-de-runtime.md)). Um PR
que adiciona uma precisa defender o caso na descrição.

## Adicionar um candidato

Um arquivo em `evals/candidates/<nome>.toml`, com o nome do arquivo igual ao `name`. O
parser é estrito: chave desconhecida é erro, porque um `temprature` que cai no default em
silêncio mede outra coisa que não a do arquivo.

```toml
[candidate]
name = "ollama-llama3.2-3b"
stage = "experimental"          # production exige os requisitos `block` da spec
description = "Llama 3.2 3B via Ollama, temperatura 0.2, três repetições."

[classifier]
kind = "llm"
provider = "ollama"             # ou "openai-compatible", com base_url e api_key_env
model = "llama3.2:3b"
prompt = "prompts/classify-v1.toml"
cassette = "evals/cassettes/ollama-llama3.2-3b.jsonl"
temperature = 0.2
seed = 7
repeats = 3

[sample]
fraction = 0.5                  # abaixo de ~88 casos pareados por split o gate não aprova
seed = 11

[budget]
max_calls = 1000
max_tokens = 1500000

[prices]
input_per_mtok_usd = 0.10       # custo-sombra, mesmo para modelo local
output_per_mtok_usd = 0.40
```

A evidência é gravada pelo workflow `live-eval` (Actions > live-eval > Run workflow), que
abre um PR com a cassette. Enquanto a cassette não existe, o candidato aparece como
pendente e não bloqueia. Veja [docs/operacao.md](docs/operacao.md).

## Mudar a spec

Requisitos vivem em `spec/requirements.toml`. Cada um declara split, métrica, operador,
limite e se bloqueia (`block`) ou só é medido e publicado (`report`). Requisito sem casos de
evidência reprova: spec sem evidência é lista de desejos.

Promover um requisito de `report` para `block` é decisão de produto e vai no corpo do PR
com o número atual de cada candidato de produção.

## Mudar a estatística

Qualquer mudança em `stats.py`, `gate.py`, `calibration.py` ou `rng.py` exige rodar
`llm-eval-gate calibrate` e revisar a tabela. O workflow `calibration` recalcula a tabela a
partir da spec gravada e reprova se o número não bater byte a byte. Um método de intervalo
novo entra primeiro como braço de comparação da calibração, nunca direto como decisor
([ADR-0002](docs/adr/ADR-0002-nao-inferioridade-com-score-de-tango.md)).

## Aceitar uma referência

```bash
llm-eval-gate accept <candidato> --note "motivo da aceitação"
```

Aceitar é decisão, não efeito colateral: o comando grava data, commit de origem e nota, e o
arquivo em `evals/reference/` chega como diff revisado. Referência nunca se atualiza sozinha
porque um run saiu mais verde.

## O que faz um PR voltar

- Contexto de organização ou de cliente de qualquer tipo: hostname, id de conta, endereço,
  documento fiscal, texto copiado de um postmortem real. Todo exemplo é fictício. O gate
  `sanitize` bloqueia as formas genéricas e, pela lista privada (`SANITIZE_DENYLIST` no CI,
  `.sanitize-denylist` local), os nomes da organização; o resto é julgamento de quem revisa.
- Dependência de runtime sem justificativa.
- Mudança de prompt ou de parâmetro de modelo sem nova gravação.
- Referência alterada sem explicação no corpo do PR.
- Cobertura abaixo do piso em `.coverage-floor`. O piso só sobe: quem aumenta a cobertura
  roda `python tools/coverage_ratchet.py coverage.json --update` e commita o piso novo. O
  `--update` grava a medição menos 0,2 p.p., arredondada para baixo: a medição que vale é a
  do job `coverage` no runner Linux, e meia dúzia de ramos que dependem de plataforma não
  deveria virar vermelho.
- Travessão ou meia-risca no texto. `tools/check_typography.py` reprova; quebre a frase.

## Commits

Conventional Commits, verificados por hook: `feat:`, `fix:`, `docs:`, `test:`,
`refactor:`, `chore:`, `eval:` para evidência nova. O CHANGELOG e o número de versão saem
deles, então a mensagem é a nota de release.
