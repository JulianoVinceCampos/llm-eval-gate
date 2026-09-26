# Arquitetura

Três níveis de zoom no estilo C4 (contexto, containers, componentes) e, no fim, as
fronteiras de confiança. Os fluxos e as sequências ficam em [fluxos.md](fluxos.md); os
padrões de projeto, em [design-patterns.md](design-patterns.md).

Os diagramas usam `flowchart` do Mermaid com a notação do C4 (pessoa, sistema, container,
componente) em vez do tipo `C4Context`, que o GitHub ainda renderiza como experimental.

## Nível 1: contexto

O que o sistema é para quem está de fora, e com quem ele conversa.

```mermaid
flowchart TB
    dev(["Pessoa que abre o PR<br/><small>muda prompt, modelo, parâmetro ou código</small>"])
    maint(["Mantenedor<br/><small>grava evidência e aceita referência</small>"])
    visitor(["Visitante<br/><small>explora o painel público</small>"])

    gate["<b>llm-eval-gate</b><br/><small>gate de CI que reprova o PR quando a qualidade<br/>da saída do LLM regride</small>"]

    gh["GitHub<br/><small>repositório, Actions, PRs, code scanning,<br/>GHCR e atestações</small>"]
    user(["Quem roda a imagem<br/><small>docker run a partir do GHCR</small>"])
    model["Provider de modelo<br/><small>Ollama, ou API compatível com OpenAI</small>"]
    sonar["SonarCloud<br/><small>quality gate e cobertura</small>"]
    scorecard["OpenSSF Scorecard<br/><small>postura de supply chain</small>"]
    render["Render<br/><small>hospeda o painel</small>"]

    dev -->|"abre PR"| gh
    maint -->|"dispara o live-eval, revisa e aceita"| gh
    gh -->|"roda em todo PR, em replay"| gate
    gate -->|"veredito, resumo do job e exit code"| gh
    gate -.->|"chama o modelo só no live-eval"| model
    gh -->|"relatório de cobertura"| sonar
    gh -->|"análise semanal"| scorecard
    gh -->|"commit da main, depois dos checks"| render
    visitor -->|"HTTPS"| render
    gh -->|"imagem com SBOM e atestação"| user

    classDef person fill:#0d4e88,stroke:#0d4e88,color:#ffffff
    classDef system fill:#1668b3,stroke:#0d4e88,color:#ffffff
    classDef external fill:#f4f6f8,stroke:#5b6b7c,color:#16222f
    class dev,maint,visitor,user person
    class gate system
    class gh,model,sonar,scorecard,render external
```

A seta pontilhada é a decisão central do desenho ([ADR-0004](adr/ADR-0004-replay-no-pr-gravacao-no-ci.md)):
o PR nunca fala com o modelo. Ele reproduz a evidência gravada, então roda em segundos,
sem chave e com o mesmo resultado em qualquer máquina.

## Nível 2: containers

Onde o código roda e o que cada lugar lê ou escreve. A evidência não mora num banco: mora
no próprio repositório, versionada, e toda mudança nela chega como diff de PR.

```mermaid
flowchart LR
    subgraph repo["Repositório git: evidência versionada"]
        direction TB
        data[("datasets/<br/><small>train, in-dist, hard, manifest</small>")]
        config[("evals/candidates, spec, prompts<br/><small>TOML</small>")]
        evidence[("evals/cassettes, evals/reference<br/><small>respostas gravadas e runs aceitos</small>")]
        frozen[("evals/models, evals/calibration.json<br/><small>baseline ajustado e calibração</small>")]
    end

    subgraph prci["Runner do pr-ci: modo replay"]
        cli_pr["CLI llm-eval-gate<br/><small>ci, readme --check, scenarios</small>"]
    end

    subgraph live["Runner do live-eval: modo record"]
        cli_live["CLI llm-eval-gate<br/><small>run --mode record, gate, accept</small>"]
        ollama["Ollama v0.34.4<br/><small>binário conferido por sha256</small>"]
    end

    subgraph web["Container do painel: Render, ou a imagem do GHCR"]
        dash["webapp<br/><small>http.server, sessão HMAC, replay</small>"]
        ui["frontend<br/><small>HTML, CSS e JS sem framework</small>"]
    end

    data --> cli_pr
    config --> cli_pr
    evidence --> cli_pr
    frozen --> cli_pr
    cli_live -->|"HTTP local"| ollama
    cli_live -->|"cassette atestada, entra por PR"| evidence
    data --> dash
    config --> dash
    evidence --> dash
    frozen --> dash
    dash <-->|"JSON sobre HTTP"| ui

    classDef store fill:#ffffff,stroke:#5b6b7c,color:#16222f
    classDef run fill:#e8f1fa,stroke:#1668b3,color:#0d4e88
    class data,config,evidence,frozen store
    class cli_pr,cli_live,ollama,dash,ui run
```

| Container | Tecnologia | Responsabilidade |
|---|---|---|
| CLI no runner do PR | Python 3.11+, só biblioteca padrão | provar que os artefatos batem com os geradores e julgar cada candidato contra a referência e a spec |
| CLI no runner do live-eval | a mesma CLI, mais Ollama em versão fixa | gravar a evidência nova, atestar e abrir o PR |
| Painel | `http.server`, imagem `python:3.13-slim` pinada por digest, usuário não-root | expor o mesmo julgamento para exploração, somente leitura |
| Repositório | git | ser a única fonte de verdade da evidência |

## Nível 3: componentes do pacote

Os módulos de `src/llm_eval_gate/` e a direção das dependências. As setas apontam de quem
usa para quem é usado.

```mermaid
flowchart TB
    subgraph entradas["Entradas"]
        cli["cli<br/><small>tabela de comandos</small>"]
        webapp["webapp<br/><small>rotas puras, casca HTTP fina</small>"]
    end

    subgraph servicos["Serviços de aplicação"]
        pipeline["pipeline<br/><small>Facade: ci, accept, compare,<br/>calibrate, readme</small>"]
    end

    subgraph avaliacao["Avaliação"]
        factory["factory<br/><small>Factory + Registry</small>"]
        classifiers["classifiers<br/><small>Strategy: regras, LLM, sintético</small>"]
        runner["runner<br/><small>amostra estratificada, Observer</small>"]
        metrics["metrics<br/><small>Wilson-Kish, F1, p95, custo</small>"]
    end

    subgraph decisao["Decisão"]
        gate["gate<br/><small>Composite de checks,<br/>veredito em três estados</small>"]
        stats["stats<br/><small>Tango, Wald+2, bootstrap,<br/>McNemar, Holm</small>"]
        spec["spec<br/><small>requisitos e política</small>"]
        trace["trace<br/><small>requisito, medida, casos</small>"]
    end

    subgraph fronteira["Fronteira com o modelo"]
        prompt["prompt, parsing<br/><small>template versionado,<br/>parser estrito</small>"]
        providers["providers<br/><small>Adapter e Decorator: Ollama, OpenAI,<br/>replay, retry, orçamento</small>"]
    end

    subgraph fundacao["Fundação"]
        datasets["datasets<br/><small>gerador determinístico</small>"]
        baseline["baseline<br/><small>31 regras + assinatura</small>"]
        reference["reference<br/><small>aceite explícito</small>"]
        calibration["calibration, scenarios"]
        core["domain, labels, rng, jsonio"]
    end

    cli --> pipeline
    webapp --> pipeline
    pipeline --> factory
    pipeline --> runner
    pipeline --> metrics
    pipeline --> gate
    pipeline --> trace
    pipeline --> reference
    pipeline --> datasets
    pipeline --> calibration
    factory --> classifiers
    factory --> providers
    runner --> classifiers
    classifiers --> prompt
    classifiers --> providers
    classifiers --> baseline
    gate --> stats
    gate --> spec
    gate --> metrics
    calibration --> gate

    classDef edge fill:#f2f8f4,stroke:#0f8b8d,color:#1c6b45
    classDef core fill:#e8f1fa,stroke:#1668b3,color:#0d4e88
    class cli,webapp edge
    class pipeline,factory,classifiers,runner,metrics,gate,stats,spec,trace,prompt,providers,datasets,baseline,reference,calibration,core core
```

Todo módulo depende de `core` (tipos de domínio, rótulos, aleatoriedade determinística,
IO canônico); essas setas ficam de fora para o desenho continuar legível.

### O que cada componente garante

**`pipeline`** é a única porta de entrada para as operações. A CLI, o CI e o painel chamam
as mesmas funções, então o painel não pode virar uma segunda fonte de verdade: a tabela que
ele mostra é o resultado de `evaluate_candidate`, a mesma que o `ci` imprime.

**`factory`** decide o que fica atrás de um candidato a partir do TOML e do modo
(`replay`, `live`, `record`). É o único lugar que conhece provider concreto.

**`classifiers`** implementa um protocolo, três estratégias. O `runner`, as métricas e o
gate nunca sabem qual estão medindo.

**`gate`** compõe os checks (compatibilidade, não inferioridade por split, um check por
requisito) e reduz os vereditos ao pior deles entre os que valem para o estágio. Todo check
roda, mesmo depois de o primeiro reprovar, para o relatório sair completo.

**`stats`** é matemática pura, sem IO, com cada método nomeado e citado. A escolha do
método que decide é da política, não do código ([ADR-0002](adr/ADR-0002-nao-inferioridade-com-score-de-tango.md)).

**`providers`** é a porta para fora. O protocolo tem um método; o adapter de cada vendor,
o replay da cassette, a gravação, o retry e o orçamento implementam o mesmo protocolo e se
empilham.

**`parsing`** trata a saída do modelo como entrada não confiável: aceita um objeto JSON com
um rótulo do catálogo e transforma o resto em `invalid`, que entra na métrica de formato.

### O que a direção das setas garante

Nada abaixo de `pipeline` importa `cli` ou `webapp`. O núcleo roda igual num runner, num
laptop e dentro do container, e o painel existe sem que o caminho da CLI ganhe dependência
ou ramo condicional. `stats` e `gate` não fazem IO, o que é o que permite à calibração
decidir 24 mil vezes com exatamente o código que o CI roda.

## Fronteiras de confiança

| Fronteira | Do lado de fora | Controle |
|---|---|---|
| Texto do caso para o modelo | conteúdo do dataset, que num uso real vem de gente | prompt marca o texto como dado entre delimitadores; a saída passa pelo parser estrito |
| Saída do modelo para o gate | texto arbitrário | `parsing` aceita só rótulo do catálogo; o resto conta como falha de formato |
| Configuração para a rede | URL e nome de variável de chave vindos do TOML | só `http` e `https`, sem credencial na URL, opener sem `file://` nem redirect, resposta limitada em tamanho e tempo |
| Internet para o painel | qualquer requisição | sessão assinada, limite de login, corpo limitado, JSON obrigatório, checagem de `Origin`, CSP estrita, grade fechada no what-if |
| PR para `main` | qualquer mudança de evidência | cassette e referência são arquivos versionados; mudança aparece no diff e o gate a julga |

O detalhamento de ameaças e riscos residuais está em [threat-model.md](threat-model.md).
