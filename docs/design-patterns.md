# Padrões de projeto

Cada padrão aqui resolve um problema concreto do gate, e cada um tem um custo. A regra usada
foi simples: padrão entra quando tira um `if` sobre o tipo de alguma coisa, ou quando
separa uma decisão que muda de uma que não muda. Nada entrou para parecer arquitetura.

| Padrão | Onde | Problema que resolve |
|---|---|---|
| [Ports and Adapters](#ports-and-adapters) | `providers/base.py`, `classifiers.py`, `gate.py`, `runner.py` | o núcleo não sabe com quem está falando |
| [Strategy](#strategy) | `classifiers.py` | medir regras, LLM e modelo sintético com o mesmo código |
| [Adapter](#adapter) | `providers/ollama.py`, `providers/openai_compat.py` | API de vendor vira um método só |
| [Decorator](#decorator) | `providers/resilience.py`, `providers/replay.py` | retry, orçamento e gravação sem tocar no provider |
| [Record and Replay](#record-and-replay) | `providers/replay.py` | PR sem modelo, sem chave, determinístico |
| [Factory com Registry](#factory-com-registry) | `factory.py` | um único lugar conhece provider concreto |
| [Composite](#composite) | `gate.py` | um veredito a partir de muitos checks |
| [Specification](#specification) | `spec.py`, `gate.py` | requisito é dado, não código |
| [Observer e Null Object](#observer-e-null-object) | `runner.py` | progresso sem `print` dentro do laço |
| [Facade](#facade) | `pipeline.py` | CLI, CI e painel chamam a mesma operação |
| [Command](#command) | `cli.py` | um subcomando, uma função, uma tabela |
| [Value Object](#value-object) | `domain.py`, `stats.py`, `gate.py` | dado imutável, comparável e serializável |
| [Retry com backoff e jitter](#retry-com-backoff-e-jitter) | `providers/resilience.py` | falha transitória sem tempestade de retry |
| [Disjuntor de orçamento](#disjuntor-de-orçamento) | `providers/resilience.py` | a gravação para antes de passar do teto |
| [Anti-corruption layer](#anti-corruption-layer) | `parsing.py` | saída do modelo não contamina o domínio |
| [Functional core, imperative shell](#functional-core-imperative-shell) | `webapp.py`, `stats.py` | a API inteira testável sem socket |

## Ports and Adapters

O núcleo define protocolos e nunca importa implementação. Os quatro portos:

| Porto | Protocolo | Quem implementa |
|---|---|---|
| Modelo | `Provider.complete(request) -> Completion` | Ollama, OpenAI-compatible, replay, gravação, retry, orçamento, fakes de teste |
| Classificador | `Classifier.classify(case, repeat) -> Outcome` | regras, LLM, sintético |
| Check do gate | `Check.evaluate(ctx) -> CheckResult` | compatibilidade, não inferioridade, requisito |
| Progresso | `RunListener` | impressora de progresso, listener nulo |

Os protocolos são `typing.Protocol`, então a conformidade é estrutural e verificada pelo
`mypy --strict`: um fake de teste não precisa herdar de nada.

**Custo:** mais tipos para ler antes de entender o fluxo. Por isso cada protocolo tem um
método só, ou quase.

## Strategy

```python
class Classifier(Protocol):
    @property
    def descriptor(self) -> Mapping[str, Any]: ...
    @property
    def deterministic(self) -> bool: ...
    def classify(self, case: Case, repeat: int) -> Outcome: ...
```

`RulesClassifier`, `LLMClassifier` e `SyntheticClassifier` implementam o mesmo protocolo. O
`runner`, as métricas e o gate nunca perguntam qual estão medindo. É isso que faz a
comparação "regras contra LLM" ser justa: o mesmo laço, a mesma amostra, as mesmas métricas.

`deterministic` é a única pergunta que o runner faz, e só para decidir se repetir vale a
pena: um classificador determinístico é pontuado uma vez por caso.

## Adapter

`OllamaProvider` traduz o request para `/api/chat` (mensagens, `options.seed`,
`options.num_predict`, `format` com o JSON schema da saída) e a resposta para
`Completion` (texto, `prompt_eval_count`, `eval_count`, latência). O adapter
OpenAI-compatible faz o mesmo para `/v1/chat/completions`, com `response_format` em modo
`json_schema` estrito, e serve tanto API hospedada quanto servidor local que fala o mesmo
protocolo (vLLM, llama.cpp, LM Studio). O resto do sistema só vê `Completion`.

**Custo:** cada vendor novo é um arquivo. É o custo certo: o conhecimento sobre um vendor
fica num lugar só.

## Decorator

Retry, orçamento e gravação são providers que embrulham outro provider:

```text
Recording(Retry(Budget(Ollama)))
```

A ordem é deliberada e documentada em `factory.py`:

- `Recording` por fora: só resposta que **terminou bem** vai para a cassette, nunca uma
  tentativa que falhou no meio do retry;
- `Retry` no meio: tenta de novo o que é transitório;
- `Budget` por dentro: conta **toda** chamada que chegou ao modelo, inclusive as que o retry
  repetiu, porque é isso que custa.

Trocar a ordem muda o significado. `Budget(Retry(...))` contaria uma chamada por caso e
deixaria o retry passar do teto sem ser visto.

## Record and Replay

A cassette guarda cada resposta pela chave sha256 do request inteiro. O `ReplayProvider`
responde só a partir dela, e uma chave ausente é erro:

```python
completion = self._cassette.get(request.key())
if completion is None:
    raise CassetteMissError(
        f"no recorded answer in {self._source} for this request; the prompt, model "
        "or parameters changed. Re-record with the live-eval workflow."
    )
```

Não há fallback silencioso. Mudou uma palavra do prompt, o replay erra alto, o gate
bloqueia, e a única saída é gravar evidência nova. Ver
[ADR-0004](adr/ADR-0004-replay-no-pr-gravacao-no-ci.md).

## Factory com Registry

```python
PROVIDERS: dict[str, ProviderFactory] = {"ollama": _ollama, "openai-compatible": _openai}
```

`build_classifier(config, root, mode)` é o único lugar que decide o que fica atrás de um
candidato: o tipo (`rules`, `synthetic`, `llm`), o provider pelo registry e o modo
(`replay`, `live`, `record`) pela pilha de decorators. Os testes injetam um registry próprio
com fakes, sem monkeypatch.

## Composite

`GateEngine` é uma lista de checks montada a partir da spec e da política:

```python
checks: list[Check] = [CompatibilityCheck()]
checks.extend(NonInferiorityCheck(split) for split in policy.regression.splits)
checks.extend(RequirementCheck(req) for req in spec.requirements)
```

Todo check roda, mesmo depois de um reprovar, para o relatório sair completo. O veredito é
o pior entre os checks que valem para o estágio do candidato. Um check novo é uma classe com
`evaluate`; o motor não muda.

## Specification

Cada requisito da spec é um objeto de critério avaliado contra as métricas: split, métrica,
rótulo, operador, limite e qual parte do intervalo comparar. `RequirementCheck` é a regra
genérica que aplica qualquer um deles. Adicionar um requisito é uma entrada no TOML, não uma
linha de código, e o texto do critério sai do próprio objeto (`criterion()`) para o
relatório e para a matriz de rastreabilidade. Ver [ADR-0006](adr/ADR-0006-spec-executavel.md).

## Observer e Null Object

O `EvalRunner` publica início, cada observação e fim para um `RunListener`. A CLI passa um
`ProgressPrinter` (uma gravação ao vivo leva minutos e precisa mostrar progresso); o CI e os
testes passam nada, e o runner usa o `NullListener`. Assim o laço de avaliação não tem
`print`, não tem `if verbose` e não depende de terminal.

## Facade

`pipeline.py` expõe as operações do sistema: `run_ci`, `evaluate_candidate`,
`accept_reference`, `compare_results`, `calibrate`, `render_readme`. A CLI, o workflow e o
painel chamam essas funções e nada abaixo delas. Consequência prática: o painel não pode
divergir da CLI, porque não tem caminho próprio até os dados.

## Command

Cada subcomando da CLI é uma função com a mesma assinatura, registrada numa tabela pelo
`argparse`:

```python
Handler = Callable[[Project, argparse.Namespace], int]
```

O `main` só resolve a raiz, despacha e traduz exceção em exit code. É por isso que o
`ruff` tem uma exceção de argumento não usado para `cli.py`: nem todo handler usa todos os
argumentos, e a uniformidade da assinatura é o que permite a tabela.

## Value Object

`Case`, `Observation`, `RunRecord`, `PairedInterval`, `NonInferiority`, `CheckResult` e os
demais são `@dataclass(frozen=True, slots=True)`. Imutáveis, comparáveis por valor e com
`to_dict` e `from_dict` explícitos. É o que permite ao `fit --check` e ao
`calibrate --check` compararem o artefato commitado com o recalculado por igualdade simples,
e ao `RunRecord` ter uma impressão digital estável.

## Retry com backoff e jitter

```python
ceiling = min(self._max, self._base * 2 ** (attempt - 1))
self._sleep(ceiling * self._rng.random())
```

Backoff exponencial com teto e *full jitter*: a espera é sorteada entre zero e o teto da
tentativa, o que espalha clientes que falharam juntos. Só erro transitório repete (conexão
recusada, timeout, 5xx, 429). Um 400 significa que o request está errado, e repetir só
multiplica o custo do erro.

## Disjuntor de orçamento

`BudgetGuard` recusa a chamada que passaria do teto de chamadas ou de tokens do candidato,
**antes** de fazê-la. Não é um circuit breaker clássico (não tem estado meio aberto, não
volta sozinho): é um disjuntor que desarma uma vez por gravação e exige gente para religar,
que é o comportamento certo quando o recurso protegido é dinheiro.

## Anti-corruption layer

A saída do modelo é texto arbitrário. `parsing.parse_output` aceita uma coisa, um objeto
JSON cujo `label` pertence ao catálogo, e transforma todo o resto em `invalid`, que entra na
métrica de formato válido. Ele nunca adivinha: mapear "a pool problem" para `pool-lock`
inflaria a nota do modelo com a opinião do parser, e o gate estaria medindo o componente
errado.

## Functional core, imperative shell

No painel, cada rota é uma função pura que recebe o estado e os parâmetros e devolve
`(status, payload)`. O `Handler` HTTP é casca fina: lê o corpo com limite, confere tipo e
origem, chama a função, escreve a resposta. A API inteira é testada sem abrir socket, e a
fronteira de confiança cabe em funções que um teste exercita diretamente.

`stats.py` segue a mesma linha: matemática sem IO, determinística, com cada método nomeado
e citado, o que é o que permite à calibração decidir milhares de simulações com exatamente o
código que o CI usa.

## O que ficou de fora de propósito

- **Injeção de dependência por container.** Os construtores recebem o que precisam
  (`clock`, `sleep`, `transport`, `rng`) com defaults. Para um pacote deste tamanho, um
  container seria cerimônia.
- **Herança entre providers.** Nenhum provider herda de outro. Composição por decorator
  cobre tudo o que herança faria, sem acoplamento de hierarquia.
- **Plugin por entry point.** O registry é um dicionário. Um sistema de plugins resolveria
  um problema que ninguém tem ainda.
