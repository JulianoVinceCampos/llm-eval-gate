# Fluxos e sequências

Como uma decisão acontece, do push ao veredito. Primeiro os fluxos (o que é decidido e em
que ordem), depois as sequências (quem fala com quem). A estrutura estática está em
[arquitetura.md](arquitetura.md).

## Fluxo 1: o que `llm-eval-gate ci` faz

É o comando que o job `eval-gate` roda em todo PR, e o mesmo que qualquer time copiaria
para o próprio pipeline.

```mermaid
flowchart TD
    start(["llm-eval-gate ci"]) --> ds{"datasets --check<br/>bytes iguais aos do gerador?"}
    ds -->|não| drift["fora de sincronia<br/>exit 1, nada mais roda"]
    ds -->|sim| fit{"fit --check<br/>baseline igual ao ajuste do train?"}
    fit -->|não| drift
    fit -->|sim| each["para cada candidato em evals/candidates/"]
    each --> evidence{"precisa de cassette<br/>e ela existe?"}
    evidence -->|"falta, sem referência"| pending["PENDENTE<br/>não bloqueia"]
    evidence -->|"falta, com referência"| gone["REPROVA<br/>a evidência sumiu"]
    evidence -->|"existe, ou é regra ou sintético"| run["executa os casos em replay<br/>métricas por split"]
    run -->|"request sem resposta gravada"| miss["ERRO, bloqueia<br/>prompt ou parâmetro mudou"]
    run --> engine["GateEngine<br/>compatibilidade, não inferioridade<br/>por split, um check por requisito"]
    engine --> verdict["veredito do candidato"]
    pending --> agg{"algum candidato bloqueia?"}
    gone --> agg
    miss --> agg
    verdict --> agg
    agg -->|sim| red["exit 1: PR reprovado"]
    agg -->|não| green["exit 0"]
```

A ordem é o argumento: se os artefatos não são o que os geradores produzem, nenhum número
seguinte merece confiança, então o comando para ali. Os exit codes separam as causas:
`1` é "o modelo regrediu" ou "um artefato divergiu", `2` é configuração quebrada (TOML
inválido, prompt ausente, schema de artefato antigo).

## Fluxo 2: um check de não inferioridade

Um por split (`in-dist` e `hard`), contra a referência aceita do candidato.

```mermaid
flowchart TD
    pairs["casos pareados do split<br/>d = acerto do candidato menos o da referência, por caso"] --> tango["intervalo do score de Tango para a média de d<br/>cada limite a alfa unilateral 0,05"]
    tango --> lo{"lo ≥ -margem?"}
    lo -->|sim| pass["PASSA<br/>perda maior que a margem está excluída"]
    lo -->|não| hi{"hi abaixo de -margem?"}
    hi -->|sim| fail["REPROVA<br/>está excluído que a perda caiba na margem"]
    hi -->|não| inc["INCONCLUSIVO<br/>os dados não sustentam nenhuma das duas"]
    inc --> policy{"on_inconclusive = fail?"}
    policy -->|sim| block["bloqueia o PR"]
    policy -->|não| report["publicado, não bloqueia"]
```

Com a política atual (margem de 3 p.p., `on_inconclusive = "fail"`), um candidato só passa
com pelo menos 88 casos pareados por split, mesmo idêntico à referência. A conta está em
[metodologia-estatistica.md](metodologia-estatistica.md#6-dimensionamento).

## Fluxo 3: como o motor junta os checks

```mermaid
flowchart LR
    checks["todos os checks rodam<br/>mesmo depois de um reprovar"] --> enforced{"o check vale<br/>para este candidato?"}
    enforced -->|não| shown["aparece no relatório,<br/>não entra no veredito"]
    enforced -->|sim| worst["pior veredito entre os que valem<br/>REPROVA pesa mais que INCONCLUSIVO,<br/>que pesa mais que PASSA"]
    worst --> blocking{"REPROVA, ou INCONCLUSIVO<br/>com on_inconclusive = fail?"}
    blocking -->|sim| yes["bloqueia"]
    blocking -->|não| no["libera"]
```

Quem vale para quem:

| Check | Vale quando |
|---|---|
| Compatibilidade com a referência | há referência, ou o candidato está em produção (produção sem referência reprova) |
| Não inferioridade por split | há referência, em qualquer estágio |
| Requisito `block` | o candidato está em `production` |
| Requisito `report` | nunca bloqueia; é medido e publicado |

## Fluxo 4: a vida de um candidato

```mermaid
stateDiagram-v2
    direction LR
    state "Sem evidência" as semEvidencia
    state "Primeira avaliação" as primeira
    state "Com referência" as comReferencia
    state "Em produção" as producao
    [*] --> semEvidencia: o TOML entra por PR
    semEvidencia --> primeira: live-eval grava a cassette
    primeira --> comReferencia: accept, diff revisado
    comReferencia --> comReferencia: nova gravação não regride
    comReferencia --> producao: stage = production
    producao --> producao: passa na não inferioridade e nos requisitos block
```

- **Sem evidência:** aparece como pendente e não bloqueia. Nenhum número é publicado.
- **Primeira avaliação:** há cassette, não há referência. Nada a comparar; os requisitos
  são medidos e publicados.
- **Com referência:** toda nova evidência precisa ser não inferior à aceita. Apagar a
  cassette a partir daqui reprova.
- **Em produção:** além disso, todos os requisitos `block` precisam ser atendidos.

## Sequência 1: um pull request comum

```mermaid
sequenceDiagram
    autonumber
    actor Dev as Pessoa que abre o PR
    participant GH as GitHub
    participant CI as pr-ci
    participant CLI as llm-eval-gate ci
    participant Repo as Evidência versionada
    Dev->>GH: push de prompt, modelo ou código
    GH->>CI: evento pull_request
    CI->>CI: sanitize, lint, build-test
    CI->>CLI: job eval-gate
    CLI->>Repo: datasets e baseline batem com os geradores?
    Repo-->>CLI: sim
    loop cada candidato
        CLI->>Repo: TOML, cassette e referência
        CLI->>CLI: replay dos casos e métricas por split
        CLI->>CLI: compatibilidade, não inferioridade, requisitos
    end
    CLI-->>CI: resumo no job summary e exit code
    CI-->>GH: check ci-status
    GH-->>Dev: PR liberado ou bloqueado, com o motivo de cada check
```

## Sequência 2: gravar evidência nova (`live-eval`)

```mermaid
sequenceDiagram
    autonumber
    actor M as Mantenedor
    participant GH as GitHub
    participant R as Runner do live-eval
    participant O as Ollama
    participant CLI as llm-eval-gate
    M->>GH: workflow_dispatch com candidato e accept_reference
    GH->>R: inicia o job
    R->>R: valida o nome do candidato antes de qualquer shell
    R->>R: baixa o Ollama fixado e confere o sha256
    R->>O: ollama serve e ollama pull do modelo
    R->>CLI: run --mode record
    Note over CLI,O: Recording, Retry e Budget em volta do provider real
    loop cada caso e cada repetição
        CLI->>O: request com a seed da repetição
        O-->>CLI: resposta
        CLI->>CLI: grava pela chave sha256 do request
    end
    R->>CLI: gate contra a referência atual
    opt accept_reference marcado
        R->>CLI: accept e readme
    end
    R->>GH: atestação de proveniência da cassette
    R->>GH: PR com commit assinado
    GH->>GH: pr-ci julga o PR como qualquer outro
```

O veredito da gravação vai para o corpo do PR, mas não impede a gravação: o PR existe
justamente para que o gate diga, na frente de todos, se a evidência nova regrediu.

## Sequência 3: aceitar uma referência

```mermaid
sequenceDiagram
    autonumber
    actor M as Mantenedor
    participant CLI as llm-eval-gate accept
    participant Ref as evals/reference
    participant PR as Pull request
    M->>CLI: accept candidato --note "motivo"
    CLI->>CLI: executa em replay, ou lê o run indicado em --run
    CLI->>CLI: confere candidato e versão do dataset
    CLI->>Ref: run + accepted_at + source_commit + note
    M->>PR: commit do arquivo da referência
    PR->>PR: revisão lê o diff da referência
    Note over Ref,PR: a referência nunca muda como efeito colateral
```

## Sequência 4: o painel

```mermaid
sequenceDiagram
    autonumber
    actor V as Visitante
    participant B as Navegador
    participant W as webapp
    participant S as Estado em memória
    Note over W,S: na subida, todo candidato é avaliado uma vez, em replay
    V->>B: abre o painel
    B->>W: GET /api/session
    W-->>B: authenticated false
    V->>B: usuário e senha
    B->>W: POST /api/login com JSON, mesma origem
    W->>W: limite de tentativas por cliente e compare_digest
    W-->>B: Set-Cookie HttpOnly, SameSite Strict, assinado com HMAC
    B->>W: GET /api/overview
    W->>S: leitura
    S-->>W: candidatos, cenários e calibração
    W-->>B: JSON
    B->>W: GET /api/whatif com margem, alfa e n
    W->>W: valida contra a grade fechada
    W->>S: cache, ou redecide com non_inferiority
    W-->>B: veredito recalculado
```

O what-if é a única computação exposta à rede. A grade fechada (alfa em quatro valores,
margem em passos de 0,5 p.p. até 20 p.p., n em cinco tamanhos) e o cache LRU impedem que o
endpoint vire amplificador de CPU.
