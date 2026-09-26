# ADR-0010: Determinismo byte a byte

- **Status:** aceito
- **Data:** 2026-09-26

## Contexto

O README publica números: acurácia, intervalos, tabela de calibração. Um número publicado
que o código não reproduz é uma afirmação que ninguém consegue checar. O CI precisa
recalcular tudo e comparar byte a byte, em Python 3.11, 3.12 e 3.13, em Linux, e também no
laptop Windows de quem mantém o projeto.

Quatro armadilhas quebram isso em silêncio:

1. `random.choice`, `randrange`, `shuffle` e `sample` usam detalhes internos que o CPython
   pode mudar entre versões. Só `random.random()` tem garantia documentada de sequência
   igual para a mesma seed.
2. `hash()` de string tem sal por processo.
3. `json.dumps` sem `sort_keys` depende da ordem de inserção, que é acidente do código.
4. `Path.write_text` troca `\n` pelo separador da plataforma: no Windows todo artefato vira
   CRLF e todo sha256 vira outro número.

## Decisão

- Toda aleatoriedade passa por `rng.Rng`, cujos métodos (`below`, `choice`, `shuffled`,
  `sample`, `chance`) são construídos só sobre `random()`. Seeds são derivadas do sha256 das
  partes que as nomeiam, nunca de `hash()`.
- JSON canônico: chaves ordenadas, separadores fixos. Floats arredondados na saída
  (latência com 3 casas, custo com 9) para que ruído na última casa não mude um hash.
- Toda escrita de artefato é em bytes UTF-8 com LF, e o `.gitattributes` fixa `eol=lf`
  para o checkout não reescrever.
- Casos ordenados por id; ids derivados da versão do gerador, do split, da seed e do índice.
- O CI verifica: `datasets --check` e `fit --check` em todo PR, na matriz 3.11 a 3.13;
  `readme --check` em todo PR; `calibrate --check` quando a estatística muda e toda semana.

## Consequências

**Bom.** Qualquer mudança no gerador, no ajuste ou na estatística aparece como diff de
artefato, e o número do README só muda num PR que o mostra mudando. Quem clona o
repositório reproduz os mesmos bytes.

**Ruim.** Não dá para usar as conveniências do módulo `random`, e todo artefato novo
precisa passar pelo `jsonio`. A latência medida é o único campo que muda de um run para
outro; ela entra no registro do run, mas não em nenhuma checagem de igualdade.

**Revisitar se** o projeto precisar de aleatoriedade criptográfica (não precisa: aqui a
sequência fixa é o objetivo).
