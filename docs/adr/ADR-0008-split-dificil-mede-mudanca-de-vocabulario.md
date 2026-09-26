# ADR-0008: O split difícil mede robustez a mudança de vocabulário, e é adversarial por construção

- **Status:** aceito
- **Data:** 2026-09-26

## Contexto

Avaliar no mesmo vocabulário em que o classificador foi escrito mede memória, não
compreensão. Era preciso um split fora da distribuição do template, e ele tinha de ser
gerado por código, com rótulo verdadeiro conhecido, sem nada vindo de sistema real.

A primeira versão usava só paráfrase: toda frase reescrita sem as palavras que as regras
procuram. O baseline caiu para 13,7% de acurácia. Um número desses não mede robustez,
mede efeito de piso: as regras não conhecem sinônimos, o que já se sabia antes de gerar
um caso sequer.

## Decisão

O split `hard` (300 casos: 30 por padrão e 60 incidentes pontuais, seed 303) sai de um
léxico de paráfrases escrito a partir do mecanismo de falha, evitando de propósito o
vocabulário das regras. Para ficar perto de como gente escreve de verdade, cada frase
**mantém a redação do template com probabilidade de 0,35**, então a maioria dos casos
mistura os dois vocabulários.

Por cima disso, operadores que existem em postmortem real, cada um registrado no próprio
caso para que a acurácia possa ser fatiada por ele:

| Operador | O que faz |
|---|---|
| `opaque-title` | título que não nomeia a falha |
| `omission` | uma ou duas observações da família somem |
| `noise` | frases de contexto que não discriminam nada |
| `distractor:<padrão>` | frase que **descarta** outro padrão citando-o ("sem OOM desta vez"); regra por palavra lê a palavra, leitor lê a negação |
| `code-switch` | português e inglês no mesmo documento |
| `typo` | troca de duas letras numa palavra longa |
| `free-form` | prosa corrida em vez de seções |
| `vocab-shift` / `vocab-mix` | nenhuma frase do template, ou alguma |

A afirmação é feita por extenso no código e na documentação: **o split é adversarial a
regras de palavra-chave por construção.** Ele não estima a acurácia em produção de nada.
Ele mede quanto um classificador depende do vocabulário do template.

Dois vazamentos ficam fechados: o id do caso é um hash opaco, e o texto não carrega a linha
`id:` com o nome da família, que o template do postmortem-miner tinha. Para regex é
inofensivo; para um modelo lendo o documento, entrega a resposta.

## Consequências

**Bom.** O baseline mede 47,3% [41,8; 53,0] no split difícil contra 100% no in-dist: a
queda é grande, mas não é piso, e cada operador pode ser isolado. Um modelo que melhora
no difícil sem piorar no in-dist mostra ganho real de robustez, e o custo dessa robustez
fica medido ao lado.

**Ruim.** O léxico foi escrito por quem conhece as regras, e isso é exatamente o que o
torna adversarial. Um split escrito por terceiros, sem conhecer as regras, mediria outra
coisa, e ainda não existe.

**Revisitar se** houver corpus público de postmortems com licença que permita
redistribuição: rotulado por gente, ele seria o terceiro split, e o único que diria algo
sobre desempenho fora do laboratório.
