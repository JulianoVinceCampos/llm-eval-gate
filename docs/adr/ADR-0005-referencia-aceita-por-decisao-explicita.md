# ADR-0005: Referência aceita por decisão explícita

- **Status:** aceito
- **Data:** 2026-09-26

## Contexto

Todo gate de regressão compara contra alguma coisa. Duas escolhas comuns falham aqui:

- **Comparar contra o último run.** Cada PR pode perder um pouco menos que a margem, e a
  soma dessas perdas nunca aparece em nenhum PR isolado.
- **Atualizar o alvo sozinho quando o run sai melhor.** Parece inofensivo e transforma
  ruído em referência: um run com sorte vira a régua do próximo.

Há também um problema de vocabulário. "Baseline" já é o classificador de regras
([ADR-0007](ADR-0007-baseline-deterministico-primeiro.md)). Usar a mesma palavra para "o
run contra o qual se compara" gera confusão em toda conversa sobre o gate.

## Decisão

A **referência** de um candidato é um run aceito, guardado em
`evals/reference/<candidato>.json`. Ela só muda pelo comando `llm-eval-gate accept`, que
grava junto do run a data do aceite, o commit de origem e uma nota com o motivo. O arquivo
chega ao repositório como diff revisado num PR. Nada no código atualiza a referência como
efeito colateral.

Quatro regras fecham os atalhos:

| Situação | Veredito |
|---|---|
| Referência produzida sobre outro dataset, outros casos ou outro candidato | reprova (checagem de compatibilidade) |
| Candidato com referência aceita e sem cassette | reprova: apagar a evidência não pode ser o caminho mais barato para passar |
| Candidato em produção sem referência | reprova |
| Candidato experimental sem referência | primeira avaliação: nada a comparar, os requisitos são medidos e publicados |

## Consequências

**Bom.** Toda mudança de régua tem autor, data, commit e motivo, e aparece no `git log`
do diretório `evals/reference/`. O bloco de resultados do README é gerado a partir das
referências aceitas, então o número publicado é o número decidido.

**Ruim.** O aceite é manual, e é para ser. O risco que sobra é o fatiamento: aceitar em
série candidatos que perdem 2,9 p.p. cada. Cada aceite é visível e exige nota, mas nada
no código compara contra a primeira referência de produção.

**Revisitar se** o fatiamento aparecer na prática. A proteção natural é uma segunda
checagem de não inferioridade contra a referência mais antiga do estágio `production`,
com a mesma margem.
