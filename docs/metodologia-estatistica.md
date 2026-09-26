# Metodologia estatística

O que o gate mede, como decide, quanto erra e quanto precisa de dado para decidir. Todas
as fórmulas estão implementadas em `src/llm_eval_gate/stats.py`, com a biblioteca padrão,
e os números citados aqui são os que o CI recalcula.

## 1. Unidade de análise

A unidade é o **caso**: um incidente descrito em texto, com rótulo verdadeiro conhecido
porque o gerador o escolheu antes de escrever o texto ([datasets.md](datasets.md)).

- Os splits de avaliação são `in-dist` (200 casos, vocabulário do template) e `hard` (300
  casos, vocabulário deslocado). O `train` (120 casos) só ajusta o baseline e nunca é
  avaliado.
- Um classificador determinístico responde uma vez por caso. Um LLM com temperatura acima de
  zero responde R vezes (R = 3 aqui), cada repetição com a própria seed.
- Um candidato pode avaliar uma amostra estratificada: a mesma fração de cada par
  (split, rótulo), para que nenhuma classe desapareça da amostra.

## 2. Métricas por split

| Métrica | Definição | Intervalo |
|---|---|---|
| Acurácia | média, sobre os casos, da taxa de acerto de cada caso | Wilson no n efetivo de Kish (seção 3) |
| Macro-F1 | média do F1 das classes presentes, pela resposta majoritária de cada caso | não tem |
| Recall e precisão por classe | pela resposta majoritária de cada caso | Wilson |
| Formato válido | fração das observações cuja saída o parser aceitou | Wilson sobre as observações |
| Instabilidade | fração dos casos com mais de uma resposta distinta entre as repetições | Wilson sobre os casos |
| Latência p95 | percentil por posto mais próximo, sempre um valor observado | não tem |
| Custo por chamada | média do custo-sombra (tokens vezes o preço declarado no candidato) | não tem |

A resposta majoritária desempata pela ordem de declaração dos rótulos, e `invalid` perde
qualquer empate. Métrica por classe usa a majoritária porque uma tabela montada com as
observações repetidas contaria cada caso R vezes e mostraria mais certeza do que o dado
tem.

## 3. Intervalo da acurácia: Wilson no n efetivo

O intervalo de Wilson (1927) se comporta bem com p perto de 0 ou de 1, onde a aproximação
normal sai de [0, 1]. Com repetições correlacionadas, o n certo não é o número de
observações. O tamanho efetivo de Kish (1965) vem da dispersão das taxas por caso:

```text
n_ef = p(1 - p) / Var(p̂),   Var(p̂) = variância das taxas por caso / número de casos
```

limitado a [casos, observações]. Dado binário devolve exatamente o número de casos, e o
Wilson comum. Repetições independentes se aproximam do número de observações. A decisão e
as alternativas rejeitadas estão no [ADR-0009](adr/ADR-0009-acuracia-com-wilson-no-n-efetivo.md).

## 4. A decisão de regressão: não inferioridade pareada

Para cada split da política, com os casos que o candidato e a referência avaliaram:

```text
d_i = acerto do candidato no caso i  menos  acerto da referência no caso i
Δ   = média de d_i
H0: Δ ≤ -m   (o candidato perde mais que a margem)
H1: Δ > -m
```

com margem m = 3 p.p. e alfa unilateral de 0,05 de cada lado. O gate calcula o intervalo
[lo, hi] para Δ e decide em três estados:

```text
lo ≥ -m        PASSA          a perda maior que a margem está excluída
hi < -m        REPROVA        está excluído que a perda caiba na margem
caso contrário INCONCLUSIVO   os dados não sustentam nenhuma das duas
```

Exigir que **todos** os splits passem é um teste de interseção e união (Berger, 1982): a
aprovação indevida continua limitada por alfa sem correção de multiplicidade, porque
aprovar exige rejeitar a nula em cada split. O custo é poder, não nível.

### O score de Tango

O intervalo é o score de Tango (1998) para diferença de proporções pareadas. Com n pares,
b a massa de casos em que o candidato piorou e c a massa em que melhorou, a estatística para
H0: Δ = δ0 é

```text
Z(δ0) = (c - b - n·δ0) / √( n · (2·q̃ + δ0·(1 - δ0)) )
```

em que q̃ é a estimativa de máxima verossimilhança de P(piorou) **restrita a H0**, raiz de

```text
2n·q² + a·q - b·δ0·(1 - δ0) = 0,   a = -(b + c) + (2n - c + b)·δ0
```

A variância é avaliada sob a nula testada, e não onde o dado caiu. É isso que mantém o teste
honesto com poucos casos discordantes: zero discordância em 30 casos ainda deixa espaço
para uma perda de 3 p.p., como deve deixar.

Os limites são obtidos invertendo o teste por bisseção (64 passos): lo é o menor δ0 que o
teste unilateral a alfa não rejeita, e a bisseção devolve sempre o lado rejeitado, então o
limite nunca afirma mais do que o teste. As fórmulas seguem `PropCIs::scoreci.mp`.

Quando o modelo responde R vezes por caso, d_i é fracionário. O teste usa as massas
discordantes b = Σ max(-d_i, 0) e c = Σ max(d_i, 0). Com R = 1 são as contagens da tabela
pareada; com R > 1, o tratamento equivale a usar |d_i| no lugar de d_i², o que superestima a
variância e erra para o lado de não aprovar.

### Evidência de apoio

O relatório traz também o McNemar exato (1947) sobre os casos discordantes pela resposta
majoritária, e a comparação entre dois candidatos (`llm-eval-gate compare`) ajusta por Holm
(1979) os p-valores por classe. Nenhum dos dois decide o gate: são leitura para quem revisa.

## 5. Calibração: quanto o gate erra

Um gate é um instrumento de medida, e instrumento sem calibração é opinião. O comando
`llm-eval-gate calibrate` simula pares de modelos com efeito real conhecido e decide cada
simulação com o mesmo código que o CI roda.

**Modelo da simulação.** Referência com acurácia 0,85. Candidato com 0,85 mais o efeito.
Cada caso tem uma dificuldade latente sorteada; com probabilidade 0,8 o candidato
compartilha a dificuldade da referência, senão sorteia a própria. Isso reproduz o que
acontece entre duas versões de modelo: erram quase sempre nos mesmos casos difíceis, às
vezes em direções opostas.

**Desenho.** Tamanhos 20, 50, 100 e 300; efeitos 0, -1,5, -3, -6 e -12 p.p.; 400 simulações
por célula; os três métodos decidem sobre os mesmos pares. O resultado fica em
`evals/calibration.json`, com a própria especificação embutida, e o workflow
`calibration` refaz a conta e exige o mesmo resultado byte a byte.

**Resultado resumido** (pior caso entre os tamanhos):

| Método | Aprovação indevida com efeito igual à margem | Alarme falso com efeito zero |
|---|---|---|
| Score de Tango (decide) | 5,8% | 0,2% |
| Wald+2 de Agresti e Min (comparação) | 7,0% | 0,0% |
| Bootstrap percentil, 500 reamostragens (comparação) | 32,5% | 1,2% |

Com 400 simulações, o erro padrão de Monte Carlo em torno de 5% é de cerca de 1,1 p.p. O
Tango fica dentro do ruído do nominal. O bootstrap percentil, muito usado para pôr intervalo
em métrica de avaliação, aprova um terço das regressões do tamanho da margem com 20 casos:
com poucos casos discordantes, boa parte das reamostragens não sorteia nenhum, o intervalo
colapsa em zero e o gate não enxerga a perda.

**Poder e atrito do Tango** (fração de simulações em cada veredito):

| Casos | Efeito zero: passa | -3 p.p.: passa | -6 p.p.: reprova | -12 p.p.: reprova |
|---|---|---|---|---|
| 20 | 2% | 0% | 6% | 22% |
| 50 | 12% | 2% | 15% | 55% |
| 100 | 28% | 4% | 24% | 80% |
| 300 | 72% | 6% | 54% | 100% |

Lido em voz alta: com 100 casos, uma regressão de 12 p.p. é pega em 80% das vezes e nunca
passa; um candidato equivalente passa em 28% das vezes e no resto fica inconclusivo, o que
bloqueia. A tabela completa, com os três métodos e os três vereditos, está no README e no
painel.

## 6. Dimensionamento

Um replay idêntico à referência não tem nenhum caso discordante (b = c = 0). Mesmo assim, o
limite inferior do Tango é

```text
lo = -z² / (n + z²),   z = 1,645
```

então passar exige z²/(n + z²) ≤ m, ou seja, n ≥ z²·(1 - m)/m. Com m = 3 p.p., **n ≥ 88
casos pareados por split**. Abaixo disso, até evidência idêntica fica inconclusiva. Não há
exceção para "evidência idêntica": o gate diz que não sabe porque, com aquele n, não sabe.

Por isso o candidato Ollama avalia uma amostra estratificada de 50% (100 casos in-dist e
150 hard), e não de 40%. Para candidatos que discordam da referência em parte dos casos, a
linha "efeito zero" da tabela da seção 5 é o guia: com 300 casos, 72% de aprovação.

Quem precisa aprovar com menos casos tem duas saídas honestas, ambas visíveis num PR da
política: aumentar a margem, ou aceitar `on_inconclusive = "warn"` e perder a proteção
contra o que não se consegue medir.

## 7. Limitações

- A calibração assume uma estrutura de correlação (0,8) e uma acurácia de referência
  (0,85). Outros valores mudam os números; o comando aceita outra especificação.
- A métrica decisora é acerto binário por caso. Rótulo parcialmente certo, ou texto livre
  avaliado por um juiz-modelo, pediria outra estatística.
- O tratamento de d_i fracionário é conservador, não exato.
- Os requisitos usam intervalos de 95% bilaterais (o limite inferior tem 97,5% de
  cobertura unilateral), mais exigentes que os 90% da decisão de regressão. Foi escolha de
  rigor, não de consistência.

## Referências

- Wilson, E. B. (1927). Probable inference, the law of succession, and statistical
  inference. *JASA*, 22(158), 209-212.
- Kish, L. (1965). *Survey Sampling*. Wiley.
- McNemar, Q. (1947). Note on the sampling error of the difference between correlated
  proportions or percentages. *Psychometrika*, 12(2), 153-157.
- Holm, S. (1979). A simple sequentially rejective multiple test procedure. *Scandinavian
  Journal of Statistics*, 6(2), 65-70.
- Efron, B. (1979). Bootstrap methods: another look at the jackknife. *Annals of
  Statistics*, 7(1), 1-26.
- Berger, R. L. (1982). Multiparameter hypothesis testing and acceptance sampling.
  *Technometrics*, 24(4), 295-300.
- Tango, T. (1998). Equivalence test and confidence interval for the difference in
  proportions for the paired-sample design. *Statistics in Medicine*, 17(8), 891-908.
- Agresti, A.; Min, Y. (2005). Simple improved confidence intervals for comparing matched
  proportions. *Statistics in Medicine*, 24(5), 729-740.
