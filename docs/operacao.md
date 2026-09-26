# Operação

Runbook do gate: rotina local, evidência nova, leitura de um vermelho, deploy e a
configuração que o repositório precisa uma vez.

## Rotina local

```bash
make install     # extras de dev e hooks de pre-commit
make check       # sanitize, lint, testes com ratchet e o gate, na ordem do CI
make serve       # painel em http://127.0.0.1:8000
```

Sem `make` (Windows, por exemplo): `python tools/tasks.py check`. `python tools/tasks.py list`
mostra os passos de cada grupo.

O pre-commit roda o mesmo scanner de sanitização do CI. Para ele conhecer os nomes da sua
organização sem publicá-los, crie na raiz um `.sanitize-denylist` com uma regex por linha
(linhas com `#` são comentário). O arquivo é ignorado pelo git e nunca é varrido, e um
achado dele aparece só com arquivo e linha.

## Exit codes da CLI

| Código | Significado | O que fazer |
|---|---|---|
| `0` | passou | nada |
| `1` | o gate bloqueou, ou um artefato divergiu do gerador | ler o relatório; a seção abaixo diz como |
| `2` | configuração quebrada: TOML inválido, prompt ausente, artefato de schema antigo, candidato pendente pedido diretamente | corrigir a configuração; não é regressão de modelo |

## Ler um gate vermelho

O resumo do job (`GITHUB_STEP_SUMMARY`) e o artefato `eval-gate-report` trazem um bloco por
candidato, com cada check, o número medido e os casos que puxam o número para baixo.

| O relatório diz | Causa provável | Caminho |
|---|---|---|
| `datasets` ou `baseline` fora de sincronia | gerador ou ajuste mudou sem regenerar | `llm-eval-gate datasets` ou `llm-eval-gate fit`, revisar o diff, commitar |
| README desatualizado | referência ou calibração mudou sem regenerar o bloco | `llm-eval-gate readme` e commitar |
| Não inferioridade: **REPROVA** | regressão real, maior que a margem | corrigir; ou, se a troca é desejada, aceitar a perda explicitamente com `accept` e nota |
| Não inferioridade: **INCONCLUSIVO** | poucos casos pareados para a margem (menos de 88 por split com 3 p.p.) | aumentar `[sample] fraction` do candidato e gravar de novo; mudar margem ou `on_inconclusive` é decisão de política, em PR próprio |
| Requisito `block` não atendido | candidato em produção abaixo de uma meta | ver os casos listados na rastreabilidade |
| Compatibilidade | dataset mudou, amostra mudou ou a referência é de outro candidato | gravar de novo e aceitar a nova referência |
| REPROVA com "evidência sumiu" | a cassette de um candidato com referência foi apagada | restaurar a cassette ou gravar de novo |
| ERRO por resposta ausente na cassette | prompt, modelo ou parâmetro mudou | gravar de novo com o `live-eval` |

Para entender *onde* um candidato perde, compare caso a caso:

```bash
llm-eval-gate compare rules-signature <candidato> --split hard
```

A saída traz a diferença pareada com intervalo, McNemar, recall por classe com Holm e a
acurácia por operador do split difícil.

## Adicionar um candidato

1. Criar `evals/candidates/<nome>.toml` (modelo em [CONTRIBUTING.md](../CONTRIBUTING.md)).
2. Abrir o PR. O candidato aparece como **pendente** e não bloqueia.
3. Gravar a evidência (próxima seção). O PR da gravação mostra a primeira avaliação.
4. Aceitar a referência, no mesmo PR da gravação ou depois.

## Gravar evidência nova

Actions > **live-eval** > Run workflow, com o nome do candidato. Marcar
`accept_reference` aceita a gravação como referência no mesmo PR.

- A gravação roda em CPU: 250 casos com três repetições levam dezenas de minutos.
- O Ollama é baixado numa versão fixa e conferido por sha256. Trocar a versão é mudar duas
  linhas do workflow, revisadas.
- O PR aberto com o `GITHUB_TOKEN` não dispara o `pr-ci` (regra do GitHub para evitar
  recursão). Com o secret `LIVE_EVAL_TOKEN` (token fine-grained com `contents` e
  `pull-requests` em escrita neste repositório) o PR dispara normalmente. Sem ele, fechar e
  reabrir o PR roda os checks.

Verificar de onde veio uma cassette:

```bash
gh attestation verify evals/cassettes/<candidato>.jsonl --repo JulianoVinceCampos/llm-eval-gate
```

## Aceitar uma referência

```bash
llm-eval-gate accept <candidato> --note "motivo do aceite"
llm-eval-gate readme
```

Revise o diff de `evals/reference/<candidato>.json` e do README antes de commitar. Aceitar
é decisão, não efeito colateral ([ADR-0005](adr/ADR-0005-referencia-aceita-por-decisao-explicita.md)).

## Mudar o prompt

O prompt é artefato com sha256. Qualquer mudança invalida todas as respostas gravadas, e o
gate responde com erro até existir gravação nova. Duas formas:

- editar `prompts/classify-v1.toml` e gravar de novo todos os candidatos que o usam;
- criar `prompts/classify-v2.toml`, apontar um candidato novo para ele e comparar os dois
  lado a lado. É o caminho que preserva a comparação.

## Mudar a estatística

Mudança em `stats.py`, `gate.py`, `calibration.py` ou `rng.py`:

```bash
llm-eval-gate calibrate      # cerca de um minuto
llm-eval-gate readme
```

O workflow `calibration` refaz a conta a partir da especificação gravada e reprova se o
resultado não bater byte a byte.

## Deploy

**Imagem.** Todo PR constrói a imagem, sobe o container e confere: health, painel servido,
401 sem sessão, login com a credencial padrão e processo não-root. Só a `main` publica no
GHCR, com as tags `latest` e `sha-<commit>`, SBOM e atestação de proveniência. O nome da
imagem é o do repositório em minúsculas, porque registry não aceita maiúscula:

```bash
docker run --rm -p 8000:8000 ghcr.io/julianovincecampos/llm-eval-gate:latest
gh attestation verify oci://ghcr.io/julianovincecampos/llm-eval-gate:latest \
  --repo JulianoVinceCampos/llm-eval-gate
```

**Render.** O blueprint `render.yaml` cria o serviço web no plano gratuito, com health check
em `/api/health`. O Render constrói o mesmo Dockerfile a partir do mesmo commit, e
`autoDeployTrigger: checksPass` faz o deploy esperar os checks desse commit: um commit que
reprova o próprio gate não vai para o ar. Variáveis:

| Variável | Valor | Por quê |
|---|---|---|
| `LEG_TRUST_PROXY` | `1` | o Render termina TLS; com isto o cookie sai `Secure` e a resposta leva HSTS |
| `LEG_USER` | `julianovincedecampos` | usuário do portão de demonstração |
| `LEG_PASSWORD` | definido no painel do Render | fica fora do repositório (`sync: false`); sem valor, vale o default documentado |

O plano gratuito hiberna sem tráfego; a primeira requisição depois disso leva alguns
segundos.

## Configuração do repositório (uma vez)

| Onde | O quê |
|---|---|
| Settings > Actions > General | permissões padrão do workflow **somente leitura**; permitir que o GitHub Actions crie PRs (exigido pelo release-please e pelo live-eval) |
| Settings > Secrets | `SANITIZE_DENYLIST`: nomes da organização que nunca podem aparecer (domínios, hosts, produtos), uma regex por linha; `SONAR_TOKEN` (sem ele o job do Sonar pula em vez de falhar); `LIVE_EVAL_TOKEN`, opcional |
| Settings > Rules | ruleset na `main`: checks obrigatórios `ci-status` e `image`, commits assinados, histórico linear, sem force push, valendo também para admin |
| Settings > Rules | regra de code scanning: bloquear PR com alerta de segurança alto ou crítico. É ela que torna o CodeQL bloqueante; o job em si só falha quando a análise quebra |
| SonarCloud | organização `julianovincecampos`, projeto `JulianoVinceCampos_llm-eval-gate` |
| Renovate | instalar o app no repositório; a configuração já está em `.github/renovate.json5` |
| Render | New > Blueprint, apontando para o repositório |

O `ci-status` agrega todos os jobs do `pr-ci`, então um job novo no pipeline não exige
editar o ruleset. O `image` fica à parte porque vive no workflow `docker`, que também
publica.
