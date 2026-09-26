# ADR-0009: Acurácia com intervalo de Wilson no n efetivo de Kish

- **Status:** aceito
- **Data:** 2026-09-26

## Contexto

Um LLM com temperatura acima de zero é amostrado mais de uma vez por caso (três
repetições aqui), porque a estabilidade entre execuções é um requisito. Isso cria um
problema de contagem: 300 casos com três repetições dão 900 observações, mas não 900
evidências independentes. Três respostas ao mesmo incidente se parecem muito mais entre si
do que três incidentes diferentes.

Duas saídas óbvias falham:

- **Wilson sobre as 900 observações.** Intervalo estreito demais, porque trata repetição
  correlacionada como caso novo.
- **Bootstrap por caso.** Com 100% de acerto, toda reamostragem tem média 1 e o intervalo
  sai [100,0; 100,0]. Um intervalo que afirma certeza absoluta a partir de 200 casos está
  errado, e o requisito que compara o limite inferior passaria por um motivo falso.

## Decisão

A acurácia de um split é a média das taxas de acerto por caso. O intervalo é o de Wilson
(1927) avaliado no **tamanho efetivo de amostra** de Kish (1965):

```text
n_ef = p(1 - p) / Var(p̂)
```

com a variância estimada pela dispersão das taxas por caso, e `n_ef` limitado a
[número de casos, número de observações]. Consequências da fórmula:

- dado binário (uma avaliação por caso, ou repetições que sempre concordam) dá exatamente o
  número de casos, e o intervalo é o Wilson comum;
- repetições independentes se aproximam do número de observações;
- com p igual a 0 ou 1, a dispersão não diz nada sobre as repetições, e a resposta
  conservadora é o número de casos.

Métricas por classe (precisão, recall, F1) usam a resposta majoritária de cada caso. Uma
tabela por classe montada com as observações repetidas contaria cada caso três vezes.

## Consequências

**Bom.** O baseline com 100% de acerto em 200 casos recebe [98,1; 100,0], que é o que 200
acertos em 200 sustentam. Repetição conta como evidência só na medida em que discorda, e o
requisito que compara o limite inferior usa um limite honesto.

**Ruim.** O efeito de desenho é estimado dos próprios dados, então com poucos casos ele
também tem ruído. O limite a [casos, observações] impede os dois extremos absurdos, não o
ruído do meio.

**Revisitar se** o número de repetições por caso passar a variar entre casos. A fórmula
atual assume o mesmo R para todos.
