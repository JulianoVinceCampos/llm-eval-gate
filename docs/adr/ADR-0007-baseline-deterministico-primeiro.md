# ADR-0007: Baseline determinístico primeiro

- **Status:** aceito
- **Data:** 2026-09-26

## Contexto

"O LLM classifica bem" não é uma afirmação; "o LLM classifica melhor que X, a este custo"
é. Sem um X barato, explicável e já em uso, qualquer número de acurácia de modelo fica
solto.

O [postmortem-miner](https://github.com/JulianoVinceCampos/postmortem-miner) já tem esse X:
31 regras de regex bilíngues que extraem sinais de postmortem e um agrupamento por
similaridade de Jaccard que forma os padrões. É determinístico, roda em microssegundos e
custa zero.

O risco clássico de um baseline ajustado a dados é vazamento: ajustar e medir no mesmo
conjunto e publicar um número que só vale para ele.

## Decisão

O baseline porta as 31 regras do postmortem-miner e um classificador por assinatura, o
mesmo método que o postmortem-miner usa para formar padrões, virado classificador. A
assinatura de cada padrão são os sinais presentes em pelo menos 60% dos casos de treino
dele. Um caso novo vai para o padrão de maior similaridade de Jaccard com a própria lista
de sinais, ou para `none` quando nenhum passa do limiar. O limiar é escolhido numa grade,
também só pela acurácia no treino.

- O ajuste usa **só** o split `train`. O artefato ajustado fica congelado em
  `evals/models/rules-signature.json`, com o sha256 do split de treino que o produziu.
- Pontuar o baseline no split de treino levanta `LeakageError`. Não há como medir onde se
  ajustou.
- `llm-eval-gate fit --check` roda no CI e prova que o artefato commitado é exatamente o
  que o split de treino produz.
- O baseline é o candidato `rules-signature`, no estágio `production`, e passa pelo mesmo
  gate que qualquer modelo.

## Consequências

**Bom.** Todo candidato LLM é comparado caso a caso com as regras, com McNemar exato e
Holm por classe. O resultado atual deixa a pergunta bem posta: as regras acertam 100% no
vocabulário do template e 47,3% no split difícil, com p95 abaixo de 1 ms e custo zero. É
essa distância que um modelo precisa fechar, e o custo dele fica ao lado.

**Ruim.** As regras foram escritas para o vocabulário do template do postmortem-miner, e o
split difícil foi escrito para evitar esse vocabulário
([ADR-0008](ADR-0008-split-dificil-mede-mudanca-de-vocabulario.md)). A comparação mede
dependência de vocabulário, não "regras contra IA" em geral.

**Revisitar se** o baseline passar a ser ajustado com mais parâmetros (limiar por classe,
pesos por sinal). Aí vale um split de validação separado para escolher hiperparâmetros.
