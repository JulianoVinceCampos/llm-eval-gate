# Ficha dos datasets

Tudo aqui é sintético, gerado por código versionado a partir de uma seed. Nenhum texto vem
de sistema, cliente ou colega real, e isso é verificado no CI pelo gate de sanitização,
antes de qualquer outro job.

## Para que servem

Medir um classificador de padrão de incidente: dado o texto de um postmortem, dizer qual
dos oito padrões recorrentes ele descreve, ou `none` quando é um incidente pontual. O
rótulo verdadeiro vem do gerador, que o escolhe antes de escrever o texto. Nenhum rótulo
sai de um classificador.

## Splits

| Split | Casos | Por padrão | `none` | Seed | Vocabulário | Uso |
|---|---|---|---|---|---|---|
| `train` | 120 | 12 | 24 | 101 | template | só ajusta o baseline, nunca é avaliado |
| `in-dist` | 200 | 20 | 40 | 202 | template | o caso confortável |
| `hard` | 300 | 30 | 60 | 303 | paráfrase com operadores | fora da distribuição do template |

`datasets/manifest.json` guarda a contagem por rótulo e o sha256 de cada split, e o
`eval_sha256` do conjunto avaliado. Uma referência produzida sobre outro `eval_sha256` é
recusada pela checagem de compatibilidade.

Os rótulos são os oito padrões do [postmortem-miner](https://github.com/JulianoVinceCampos/postmortem-miner)
mais `none`:

| Rótulo | Padrão |
|---|---|
| `pool-lock` | pool de conexões esgotado com lock no banco |
| `heap-oom` | estouro de heap com payload grande |
| `retry-storm` | tempestade de retry de job agendado |
| `rollback` | rollback longo de transação monolítica |
| `cert` | certificado TLS expirado na borda |
| `acl` | regra de rede bloqueando acesso externo |
| `lb-app` | desbalanceamento com bug de aplicação |
| `slow-query` | query lenta após manutenção |
| `none` | incidente pontual, sem padrão |

`none` é resposta de verdade, não fallback. O dataset traz incidentes pontuais de
propósito (disco cheio, DNS apontando para endereço desativado, relógio fora de
sincronia, feature flag ligada para todos, entre outros), porque um classificador que acha
padrão em tudo manda o plantão para o runbook errado.

## Como os textos são gerados

**Splits de template (`train`, `in-dist`).** As frases das oito famílias vêm do gerador do
postmortem-miner (MIT, mesmo autor), no formato de postmortem com frontmatter e seções. Metade
dos casos em português, metade em inglês. Metade dos casos com mais de três sinais perde um
sinal, como no gerador original. Valores numéricos (CPU, minutos, tamanho do pool, nó,
horário) são sorteados por caso; endereços usam a faixa de documentação 203.0.113.0/24 da
RFC 5737 e os serviços são nomes fictícios e neutros (`svc-orders`, `svc-catalog`,
`svc-checkout`, `svc-notify`, `svc-search`).

**Split difícil (`hard`).** Parte de um léxico de paráfrases escrito a partir do mecanismo de
falha, evitando o vocabulário das regras, e mantém a frase do template com probabilidade
0,35. Idioma do documento: 35% inglês, 35% português, 30% misturando os dois frase a frase.
Sobre isso incidem operadores sorteados por caso:

| Operador | Probabilidade | Efeito |
|---|---|---|
| `opaque-title` | sempre | título que não nomeia a falha |
| `omission` | quando a família tem observações de sobra | uma ou duas observações somem, ficam ao menos duas |
| gatilho presente | 0,70 | a frase de gatilho aparece |
| mitigação presente | 0,80 | a frase de mitigação aparece |
| `noise` | 0,60 | uma ou duas frases de contexto sem valor discriminante |
| `distractor:<padrão>` | 0,45 | uma frase que descarta outro padrão citando-o |
| `free-form` | 0,50 | prosa corrida em vez de seções com marcadores |
| `typo` | 0,30 | duas letras trocadas numa palavra longa, fora do título |
| `code-switch` | 0,30 | português e inglês no mesmo documento |
| `vocab-shift` ou `vocab-mix` | sempre um dos dois | nenhuma ou alguma frase com a redação do template |

Cada caso registra os próprios operadores, então a acurácia pode ser fatiada por eles: o
comando `llm-eval-gate compare` mostra, para dois candidatos, a acurácia em cada fatia.

## O que o split difícil mede, e o que não mede

Ele mede quanto um classificador depende do vocabulário do template. É **adversarial a
regras de palavra-chave por construção**: o léxico foi escrito conhecendo as regras e
evitando o que elas procuram. Não estima a acurácia de nada em produção. A decisão e o
histórico (a primeira versão, só com paráfrase, derrubava o baseline para 13,7%, um efeito
de piso que não media nada) estão no [ADR-0008](adr/ADR-0008-split-dificil-mede-mudanca-de-vocabulario.md).

## Vazamentos fechados

- **Id opaco.** O id de cada caso é um hash da versão do gerador, do split, da seed e do
  índice. Não carrega o rótulo.
- **Sem linha `id:` no texto.** O template do postmortem-miner tinha uma linha com o nome da
  família no frontmatter. Para uma regex é inofensiva; para um modelo lendo o documento,
  entrega a resposta. Aqui ela não existe.
- **Baseline sem acesso aos splits de avaliação.** O ajuste só lê `train`, e pontuar no
  `train` levanta erro ([ADR-0007](adr/ADR-0007-baseline-deterministico-primeiro.md)).

## Resultado do baseline, para referência

| Split | Acurácia (IC95) | Leitura |
|---|---|---|
| `in-dist` | 100,0% [98,1; 100,0] | as regras conhecem o vocabulário |
| `hard` | 47,3% [41,8; 53,0] | sem o vocabulário, metade dos casos cai |

Os números vivos, sempre recalculados, estão no bloco de resultados do README.

## Regenerar e verificar

```bash
llm-eval-gate datasets --check   # os bytes commitados são os que o gerador produz?
llm-eval-gate datasets           # regenera (e o diff mostra o que mudou)
```

O gerador só usa `random.random()` e hashes sha256, então os bytes são os mesmos em Python
3.11, 3.12 e 3.13 e em qualquer sistema operacional
([ADR-0010](adr/ADR-0010-determinismo-byte-a-byte.md)). O CI confirma isso na matriz de
versões em todo PR.

## Limitações

- O texto é gerado por template e por léxico. A variedade é a que o léxico tem, não a de
  um acervo real escrito por dezenas de pessoas.
- Só inglês e português. O português do dataset é escrito sem acento, como muito texto
  operacional é, e isso também mantém os hashes independentes de normalização Unicode.
- Os oito padrões vêm de uma stack JVM com banco relacional atrás de balanceador. Um
  acervo de outra stack teria outros padrões.
