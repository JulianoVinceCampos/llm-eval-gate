# ADR-0001: Zero dependência de runtime

- **Status:** aceito
- **Data:** 2026-09-26
- **Herda de:** ADR-0001 do [postmortem-miner](https://github.com/JulianoVinceCampos/postmortem-miner)

## Contexto

Um gate de CI roda em lugares que ninguém escolhe: runner efêmero sem cache, bastion sem
índice de pacotes acessível, laptop corporativo com proxy que bloqueia metade do PyPI. Se
instalar o gate exige resolver dependência, o primeiro dia de uso vira uma tarde de
depuração de ambiente, e a pergunta que o gate responde ("este PR piorou o modelo?") fica
para depois.

Há também o argumento de superfície. O gate decide se código entra em produção. Cada
dependência transitiva é código de terceiro rodando com o token do CI, e a cadeia de
suprimentos é por onde os ataques a pipeline de fato chegam.

A tentação existe e é concreta: `numpy` e `scipy` para a estatística, `httpx` para os
providers, `pydantic` para validar configuração, `fastapi` para o dashboard, `pyyaml` para
a spec.

## Decisão

`dependencies = []` no `pyproject.toml`. Tudo o que o pacote precisa vem da biblioteca
padrão do Python 3.11:

| Necessidade | Biblioteca padrão |
|---|---|
| Configuração e spec | `tomllib` |
| Estatística (Wilson, score de Tango, bootstrap, McNemar, Holm) | `math`, `statistics.NormalDist` |
| Aleatoriedade reprodutível | `random.Random.random()` e `hashlib` ([ADR-0010](ADR-0010-determinismo-byte-a-byte.md)) |
| HTTP para os providers | `urllib.request` com opener montado à mão |
| Dashboard | `http.server`, `hmac`, `secrets` ([ADR-0003](ADR-0003-dashboard-sobre-stdlib.md)) |
| Artefatos | `json` canônico, escrito em bytes com LF |

O ferramental de desenvolvimento (pytest, hypothesis, ruff, mypy) fica em extras `dev`,
com pin exato, e nunca é importado pelo pacote.

## Consequências

**Bom.** `pip install -e .` não resolve nada, e a imagem Docker nem roda pip: o código vai
da árvore de fontes para o container com `PYTHONPATH`. O `pip-audit` e o dependency review
guardam só a superfície de desenvolvimento e de CI, que é onde o risco mora. Um runner
limpo roda o gate em segundos.

**Ruim.** Cada fórmula estatística é implementada à mão, e implementação à mão erra. A
mitigação é dupla: as fórmulas seguem as de referência publicadas (PropCIs para o score de
Tango e o Wald+2) e a calibração ([ADR-0002](ADR-0002-nao-inferioridade-com-score-de-tango.md))
mede o comportamento do gate inteiro contra efeitos conhecidos, em vez de confiar na
leitura do código. O cliente HTTP também não tem pool de conexão nem HTTP/2, o que é
irrelevante para centenas de chamadas sequenciais.

**Revisitar se** o gate precisar de um juiz-modelo com embeddings locais, ou de um
provider cujo protocolo não caiba em JSON sobre HTTP.
