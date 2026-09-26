# ADR-0002: Não inferioridade com margem, decidida pelo score de Tango

- **Status:** aceito
- **Data:** 2026-09-26
- **Substitui:** a primeira versão do gate, que decidia pelo bootstrap percentil pareado

## Contexto

A pergunta do gate não é "o candidato é melhor?". É "o candidato é pior do que o aceito,
por mais do que estamos dispostos a perder?". Trocar de modelo por custo, latência ou
licença é legítimo mesmo sem ganho de acurácia; o que não pode é perder qualidade sem que
ninguém veja.

Três propriedades são exigidas do teste:

1. **Aprovação indevida limitada.** Quando a perda real é igual à margem, o gate deve
   aprovar no máximo com a frequência de alfa (5%). É o erro que a margem promete conter.
2. **Atrito baixo.** Um candidato equivalente não deve ser reprovado.
3. **Honestidade com amostra pequena.** Quando os dados não sustentam conclusão, o gate diz
   isso, em vez de arredondar para verde.

Os dados são pareados: referência e candidato respondem aos mesmos casos, e erram quase
sempre nos mesmos casos difíceis. Um teste que ignora o pareamento desperdiça a informação
mais valiosa que existe aqui.

## Decisão

**Teste de não inferioridade com margem de 3 p.p. e veredito em três estados.** Com
Δ = acurácia do candidato menos a da referência, por caso pareado, e o intervalo [lo, hi]
com cada limite a alfa unilateral de 0,05 (intervalo bilateral de 90%):

```text
lo >= -margem   -> passa          os dados excluem perda maior que a margem
hi <  -margem   -> reprova        os dados excluem que a perda caiba na margem
caso contrário  -> inconclusivo   os dados não dizem, quase sempre por n pequeno
```

`inconclusivo` é um resultado de primeira classe. A política (`on_inconclusive = "fail"`)
decide que ele bloqueia o PR.

**O intervalo é o score de Tango (1998)** para diferença de proporções pareadas, com a
variância avaliada sob a hipótese nula, no estimador restrito das células discordantes, e
invertido por bisseção. As fórmulas seguem `PropCIs::scoreci.mp`. Quando o modelo é
amostrado R vezes por caso, a diferença por caso é fracionária; o teste usa as massas
discordantes b = Σ max(-d, 0) e c = Σ max(d, 0), o que superestima a variância e erra para
o lado de não aprovar.

O McNemar exato entra no relatório como evidência de apoio, não como decisor.

## Alternativas consideradas e medidas

A escolha não foi por leitura de livro. O comando `llm-eval-gate calibrate` simula 400
pares de modelos por célula, com perda real conhecida, e decide cada simulação com o código
que o CI roda. Os três métodos decidem sobre os mesmos pares:

| Método | Aprovação indevida na margem (pior n) | Alarme falso com efeito zero (pior n) |
|---|---|---|
| **Score de Tango** | **5,8%** | 0,2% |
| Wald+2 (Agresti e Min, 2005) | 7,0% | 0,0% |
| Bootstrap percentil, 500 reamostragens | 32,5% | 1,2% |

Com 400 simulações o erro padrão de Monte Carlo em torno de 5% é de cerca de 1,1 p.p.: o
Tango fica dentro do ruído do nominal, o Wald+2 fica quase dois erros padrão acima dele, e
o bootstrap fica fora de qualquer leitura razoável.

**Bootstrap percentil (rejeitado).** Foi o decisor da primeira versão. Com n = 20 e
poucos casos discordantes, boa parte das reamostragens não sorteia nenhum caso discordante:
a média reamostrada é zero, o intervalo colapsa em torno de zero e o gate aprova uma
regressão que ele não enxerga. A calibração mediu 32,5% de aprovação indevida em n = 20, e
de 8% a 11% com n de 50 a 300. Um gate que deixa passar um terço das regressões do tamanho
da margem é decorativo.

**Wald+2 (rejeitado como decisor, mantido como braço de comparação).** Bem melhor que o
bootstrap e simples, mas estima a variância onde os dados caíram, e não sob a nula testada.
Ficou em 7,0%.

**Caso especial para evidência idêntica (rejeitado).** Um replay idêntico à referência não
tem nenhum caso discordante, e mesmo assim o Tango só aprova com n ≥ 88 por split: com zero
discordância em menos casos, uma perda de 3 p.p. ainda não está excluída
(lo = -z²/(n + z²)). A tentação de aprovar "evidência idêntica" por regra foi recusada; a
consequência virou dimensionamento do candidato (amostra de 50%, 100 e 150 casos).

## Consequências

**Bom.** O erro que a margem promete conter está medido e publicado, e o CI recalcula a
tabela a cada mudança na estatística (workflow `calibration`). O terceiro estado torna
visível a diferença entre "não piorou" e "não sei".

**Ruim.** O gate custa amostra. Com a margem de 3 p.p., um candidato equivalente mas não
idêntico (na simulação, discordando da referência em cerca de 5% dos casos) passa em 28%
das vezes com 100 casos e em 72% com 300; no restante, fica inconclusivo e bloqueia. Isso é
correto e é caro: quem quer um gate que aprove com 20 casos precisa aceitar uma margem
maior, e a política deixa essa troca explícita.

**Revisitar se** os candidatos passarem a ser avaliados em milhares de casos (aí o Wald
simples basta e é mais barato de explicar), ou se a métrica de decisão deixar de ser
binária por caso.

## Referências

- Tango, T. (1998). Equivalence test and confidence interval for the difference in
  proportions for the paired-sample design. *Statistics in Medicine*, 17(8), 891-908.
- Agresti, A.; Min, Y. (2005). Simple improved confidence intervals for comparing matched
  proportions. *Statistics in Medicine*, 24(5), 729-740.
- Scherer, R. PropCIs: Various confidence interval methods for proportions (pacote R,
  funções `scoreci.mp` e `diffpropci.mp`).
