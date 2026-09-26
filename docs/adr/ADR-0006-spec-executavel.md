# ADR-0006: Spec executável, com enforcement por requisito e estágio por candidato

- **Status:** aceito
- **Data:** 2026-09-26

## Contexto

Um gate que olha só a acurácia deixa passar as falhas que param a automação que consome o
rótulo: saída fora do formato, resposta que muda entre execuções, latência que atrasa a
triagem, custo que muda sem ninguém ver. E requisito escrito em documento apodrece: ninguém
percebe quando o sistema deixa de atendê-lo.

Ao mesmo tempo, um candidato novo não pode ser barrado por uma meta de produção antes de
alguém decidir colocá-lo em produção, senão ninguém consegue nem medir um modelo novo.

## Decisão

Os requisitos vivem em `spec/requirements.toml` e são avaliados pelo gate. Cada um declara:

| Campo | Papel |
|---|---|
| `id`, `title`, `rationale` | rastreabilidade e o porquê, lidos por gente |
| `split`, `metric`, `label` | o que medir e onde |
| `op`, `threshold` | o critério |
| `evidence` | `lower` compara o limite inferior do intervalo, `upper` o superior, ausente usa o valor medido |
| `enforcement` | `block` bloqueia candidato em produção, `report` mede e publica |

Cada candidato declara um estágio. `production` precisa atender todos os requisitos
`block`. `experimental` só não pode regredir contra a própria referência.

Requisito sem casos de evidência **reprova** (`require_evidence = true` na política):
spec sem evidência é lista de desejos. A matriz de rastreabilidade lista todo requisito,
o valor medido e os casos que puxam o número para baixo.

O formato é TOML lido por `tomllib`, com parser estrito: chave desconhecida é erro.

## Consequências

**Bom.** O gate verifica formato, estabilidade, latência e custo com o mesmo rigor da
acurácia. As metas de robustez que ninguém atende ainda (split difícil, macro-F1, recall
por classe) ficam publicadas como `report`, em vez de escondidas ou afrouxadas até
passarem. Um requisito vermelho vem com a lista de casos, que é o que torna o vermelho
acionável em minutos.

**Ruim.** Promover um candidato para `production` ou um requisito de `report` para
`block` é decisão de produto e fica fora do código. É o comportamento desejado, mas exige
disciplina de quem revisa.

**Revisitar se** a spec precisar de requisito composto (por exemplo, custo por acerto em
vez de custo por chamada). Hoje cada requisito é uma métrica contra um limite.
