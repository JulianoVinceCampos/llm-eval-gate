# Política de segurança

## Como reportar uma vulnerabilidade

Abra um [security advisory privado](https://github.com/JulianoVinceCampos/llm-eval-gate/security/advisories/new).
Não abra issue pública para nada explorável.

A confirmação de recebimento sai em até 7 dias. Se o relato for válido, combino com você o
prazo de divulgação antes de publicar qualquer coisa.

## O que este projeto protege

O gate decide se um pull request pode entrar. O ativo principal, portanto, é a **integridade
do veredito**: ninguém deve conseguir fazer um modelo pior passar sem que isso apareça no
diff. Os demais são a chave de API de quem grava evidência com um provider pago e a
instância pública do dashboard.

| Risco | Mitigação |
|---|---|
| Prompt injection no texto do incidente | O prompt marca o texto como dado entre delimitadores. A saída passa por um parser estrito que só aceita um rótulo do catálogo fechado; qualquer outra coisa conta como falha de formato e entra na métrica. O pior efeito possível é um rótulo errado, que o gate mede |
| Cassette editada à mão para o modelo parecer melhor | Cassette e referência são arquivos versionados: toda mudança aparece no diff do PR. A chave de cada resposta é o sha256 do request, então uma resposta não serve para outro prompt. A gravação oficial roda no CI, com o link da execução no corpo do PR |
| Passar no gate apagando evidência | Candidato com referência aceita e sem cassette reprova. Candidato de produção sem referência reprova. Dataset diferente do da referência reprova (checagem de compatibilidade) |
| Referência trocada em silêncio | Referência só muda por `llm-eval-gate accept`, que grava data, commit de origem e nota, e chega como diff revisado |
| URL de provider usada para SSRF ou leitura local | Só `http` e `https`, sem credencial embutida na URL, opener montado à mão sem `file://`, `ftp://`, `data:` e sem seguir redirect. Resposta limitada a 2 MB e a um timeout |
| Vazamento de chave de API | A chave vem de uma variável de ambiente nomeada no TOML, nunca do arquivo. Mensagens de erro não carregam cabeçalho. A cassette guarda o digest do request, não os cabeçalhos |
| Abuso do dashboard público | Sessão em cookie assinado com HMAC (`HttpOnly`, `SameSite=Strict`, `Secure` atrás de TLS), limite de 10 tentativas de login por minuto por cliente com memória limitada, corpo de no máximo 16 KB, JSON obrigatório, checagem de `Origin`, CSP sem `unsafe-inline`, arquivos estáticos por mapa fixo (sem path traversal) e what-if restrito a uma grade fechada com cache |
| Supply chain | Zero dependência de runtime. Ferramental de dev com pin exato. Actions pinadas por SHA, imagem base pinada por digest, Renovate com espera mínima de 3 dias, `pip-audit`, dependency review, SBOM e atestação de proveniência |
| Contexto de organização vazando para este repositório | formas genéricas (ids de nuvem, documentos fiscais, endereços privados) em duas engines, `tools/sanitize_scan.py` e Semgrep; nomes da organização a partir de uma lista privada, que vem de um secret do CI e de um arquivo local ignorado pelo git e nunca é publicada; gitleaks sobre o histórico inteiro. Tudo como primeiro estágio do CI |

O modelo de ameaça completo, com fronteiras de confiança e riscos residuais, está em
[docs/threat-model.md](docs/threat-model.md).

## O portão de login do dashboard

É um portão de demonstração sobre dado sintético e somente leitura, com credencial
documentada no README. Ele existe para que a instância pública não seja um endpoint aberto,
não para proteger segredo: não há segredo atrás dele. Quem sobe a própria instância troca a
credencial por `LEG_USER` e `LEG_PASSWORD`.

## Versões suportadas

A última versão minor publicada recebe correções. O projeto é pré-1.0: o contrato da CLI e o
formato dos artefatos podem mudar entre versões minor, e o CHANGELOG diz quando mudam.
