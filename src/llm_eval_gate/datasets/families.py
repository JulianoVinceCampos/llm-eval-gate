"""Templated vocabulary: the eight failure families of postmortem-miner, ported as-is.

This is the in-distribution side of the corpus. The sentences below are the ones the
postmortem-miner generator uses (MIT, same author), so the rule baseline meets the exact
vocabulary it was written for. That is deliberate: the `in-dist` split measures the
comfortable case, and the `hard` split (see lexicon.py) measures what happens outside it.

Every incident is fabricated. Service names are placeholders and addresses come from the
RFC 5737 documentation ranges.
"""

from __future__ import annotations

from dataclasses import dataclass

from llm_eval_gate.labels import Label


@dataclass(frozen=True, slots=True)
class Family:
    label: Label
    title_en: str
    title_pt: str
    signals_en: tuple[str, ...]
    signals_pt: tuple[str, ...]
    trigger_en: str
    trigger_pt: str
    mitigation_en: str
    mitigation_pt: str
    root_cause_open: bool


FAMILIES: tuple[Family, ...] = (
    Family(
        label=Label.POOL_LOCK,
        title_en="Connection pool exhaustion with database lock contention",
        title_pt="Esgotamento do pool de conexoes com contencao de locks no banco",
        signals_en=(
            "Database CPU climbed to {cpu}% and stayed there for {mins} minutes.",
            "The JDBC pool sat at {pool}/{pool} with WaitCount above zero on every node.",
            "The DBA found sessions holding locks on the main write table.",
            "All nodes showed the same CPU profile, so this was not isolated to one host.",
        ),
        signals_pt=(
            "CPU do banco subiu para {cpu}% e ficou nesse patamar por {mins} minutos.",
            "Pool JDBC em {pool}/{pool} com WaitCount acima de zero em todos os nos.",
            "O DBA identificou sessoes em lock na tabela principal de escrita.",
            "Todos os nos apresentaram o mesmo perfil de CPU - nao ficou isolado em um no.",
        ),
        trigger_en="A cascading ORM flush turned one business operation into dozens of statements.",
        trigger_pt="Um flush em cascata do ORM transformou uma operacao em dezenas de statements.",
        mitigation_en="Sequential restart of the application nodes plus the DBA killing locked sessions.",
        mitigation_pt="Restart sequencial dos nos da aplicacao e o DBA encerrando sessoes em lock.",
        root_cause_open=True,
    ),
    Family(
        label=Label.HEAP_OOM,
        title_en="Heap exhaustion while handling an oversized payload",
        title_pt="Estouro de heap ao processar payload muito grande",
        signals_en=(
            "Node {node} threw OOM at {hour}:17 while parsing a {mb} MB request.",
            "Full GC ran back to back and old gen stayed full.",
            "Only 1 node was affected; the rest of the fleet kept serving traffic.",
            "The inbound file carried {count} records in a single request.",
        ),
        signals_pt=(
            "O no {node} lancou OOM as {hour}:17 ao parsear uma requisicao de {mb} MB.",
            "Full GC rodou em sequencia e a old gen permaneceu cheia.",
            "Apenas 1 no foi afetado; o restante da frota seguiu atendendo.",
            "O arquivo de entrada trazia {count} registros numa unica requisicao.",
        ),
        trigger_en="The whole document is materialised in memory before persistence begins.",
        trigger_pt="O documento inteiro e materializado em memoria antes de iniciar a persistencia.",
        mitigation_en="Restarted the affected process and asked the partner to split the batch.",
        mitigation_pt="Reiniciamos o processo afetado e pedimos ao parceiro para dividir a remessa.",
        root_cause_open=True,
    ),
    Family(
        label=Label.RETRY_STORM,
        title_en="Scheduled job retry loop turned into a thundering herd",
        title_pt="Loop de retry de job agendado virou thundering herd",
        signals_en=(
            "Database CPU stayed high for {mins} minutes while the batch host CPU was 2%.",
            "Logs showed a retry loop with no backoff across three scheduled timers.",
            "The nightly batch window overlapped with the first heavy query of the day.",
            "Thread pool on the batch node reached {threads} threads.",
        ),
        signals_pt=(
            "CPU do banco alta por {mins} minutos enquanto a CPU do host de batch ficou em 2%.",
            "Logs mostraram retry em loop sem backoff em tres timers agendados.",
            "A janela de batch noturno coincidiu com a primeira query pesada do dia.",
            "O thread pool do no de batch alcancou {threads} threads.",
        ),
        trigger_en="A transient database spike made every timer fail and immediately retry.",
        trigger_pt="Um spike transitorio no banco fez cada timer falhar e reintentar de imediato.",
        mitigation_en="Restarted the batch process only - no reboot - and staggered the schedule.",
        mitigation_pt="Reiniciamos apenas o processo de batch - sem reboot - e escalonamos o schedule.",
        root_cause_open=True,
    ),
    Family(
        label=Label.ROLLBACK,
        title_en="Long rollback of a monolithic transaction",
        title_pt="Rollback longo de uma transacao monolitica",
        signals_en=(
            "The database looked busy with no new operations arriving - it was undoing work.",
            "A single huge transaction had been open for {mins} minutes before failing.",
            "Restarting did not help: crash recovery resumed the rollback.",
            "Undo log growth tracked the volume already written.",
        ),
        signals_pt=(
            "O banco parecia ocupado sem novas operacoes chegando - estava desfazendo trabalho.",
            "Uma transacao unica ficou aberta por {mins} minutos antes de falhar.",
            "Reiniciar nao ajudou: o crash recovery retomou o rollback.",
            "O crescimento do undo log acompanhou o volume ja escrito.",
        ),
        trigger_en="One request equals one transaction, so failure cost is proportional to volume.",
        trigger_pt="Uma requisicao equivale a uma transacao, e o custo da falha e proporcional ao volume.",
        mitigation_en="Freed CPU by ending waiting sessions and waited it out. Forcing a restart is worse.",
        mitigation_pt="Liberamos CPU encerrando sessoes em espera e aguardamos. Forcar restart e pior.",
        root_cause_open=True,
    ),
    Family(
        label=Label.CERT,
        title_en="Expired TLS certificate took the public endpoint down",
        title_pt="Certificado TLS expirado derrubou o endpoint publico",
        signals_en=(
            "The certificate on the edge listener had expired {days} days earlier.",
            "Health check failures started at the same minute for every target.",
            "Clients outside the VPN saw connection timed out; internal calls were fine.",
            "No deploy had happened in the previous week.",
        ),
        signals_pt=(
            "O certificado do listener de borda havia expirado {days} dias antes.",
            "Falhas de health check comecaram no mesmo minuto para todos os targets.",
            "Clientes fora da VPN viam connection timed out; chamadas internas seguiam ok.",
            "Nenhum deploy havia ocorrido na semana anterior.",
        ),
        trigger_en="Renewal was manual and the calendar reminder had no owner.",
        trigger_pt="A renovacao era manual e o lembrete no calendario nao tinha responsavel.",
        mitigation_en="Replaced the keystore and reloaded the listener. Root cause addressed: renewal automated.",
        mitigation_pt="Trocamos o keystore e recarregamos o listener. Causa raiz tratada: renovacao automatizada.",
        root_cause_open=False,
    ),
    Family(
        label=Label.ACL,
        title_en="External access blocked by a security group range gap",
        title_pt="Acesso externo bloqueado por lacuna de range no security group",
        signals_en=(
            "The app answered through the VPN but timed out from outside.",
            "Load balancer health check was failing from the subnet range {ip}0/24.",
            "The security group ingress rule did not cover the balancer subnets.",
            "No application error appeared in the logs at all.",
        ),
        signals_pt=(
            "A aplicacao respondia pela VPN mas dava timeout de fora da VPN.",
            "O health check do balanceador falhava a partir do range {ip}0/24.",
            "A regra de entrada do security group nao cobria as subnets do balanceador.",
            "Nenhum erro de aplicacao apareceu nos logs.",
        ),
        trigger_en="Client IP preservation exposed source addresses no ingress rule accepted.",
        trigger_pt="A preservacao do IP de origem expos enderecos que nenhuma regra de entrada aceitava.",
        mitigation_en="Allowed the balancer subnets on the required ports. Root cause addressed.",
        mitigation_pt="Liberamos as subnets do balanceador nas portas necessarias. Causa raiz tratada.",
        root_cause_open=False,
    ),
    Family(
        label=Label.LB_APP,
        title_en="Traffic imbalance plus an unguarded optional in the signing flow",
        title_pt="Desbalanceamento de trafego e optional sem guarda no fluxo de assinatura",
        signals_en=(
            "Node {node} carried twice the CPU of its peers with no traffic spike.",
            "Stickiness rehashed after the daily schedule restarted part of the fleet.",
            "Optional.get raised NoSuchElementException about {count} times.",
            "A ClassCastException followed in the same code path.",
            "The partner callback was never sent, so the document was invalid downstream.",
        ),
        signals_pt=(
            "O no {node} concentrou o dobro da CPU dos pares sem pico de trafego.",
            "A stickiness recalculou o hash depois que o schedule diario reiniciou parte da frota.",
            "Optional.get lancou NoSuchElementException cerca de {count} vezes.",
            "Um ClassCastException apareceu em seguida no mesmo caminho de codigo.",
            "O callback do parceiro nunca foi enviado, e o documento ficou invalido do lado dele.",
        ),
        trigger_en="A discriminator mismatch made the lookup return empty for a valid party.",
        trigger_pt="Divergencia de discriminator fez a busca retornar vazio para uma parte valida.",
        mitigation_en="DBA released stuck sessions while the team patched the lookup guard.",
        mitigation_pt="O DBA liberou sessoes travadas enquanto o time corrigia a guarda da busca.",
        root_cause_open=True,
    ),
    Family(
        label=Label.SLOW_QUERY,
        title_en="Slow queries after a statistics refresh",
        title_pt="Queries lentas depois de um refresh de estatisticas",
        signals_en=(
            "Query p99 went from {ms} ms to {ms2} ms right after the maintenance window.",
            "Slow query log filled with the same three statements.",
            "Database CPU reached {cpu}% without any pool saturation.",
            "A release had been deployed the evening before.",
        ),
        signals_pt=(
            "O p99 das queries saiu de {ms} ms para {ms2} ms logo apos a janela de manutencao.",
            "O log de query lenta encheu com os mesmos tres statements.",
            "CPU do banco chegou a {cpu}% sem qualquer saturacao de pool.",
            "Uma release havia sido publicada na noite anterior.",
        ),
        trigger_en="A stale execution plan survived the statistics refresh.",
        trigger_pt="Um plano de execucao velho sobreviveu ao refresh de estatisticas.",
        mitigation_en="Forced a plan invalidation. Root cause addressed with a scheduled refresh job.",
        mitigation_pt="Forcamos invalidacao do plano. Causa raiz tratada com job de refresh agendado.",
        root_cause_open=False,
    ),
)


@dataclass(frozen=True, slots=True)
class OneOff:
    """An incident that belongs to no recurring family. Ground truth is `none`."""

    key: str
    title_en: str
    text_en: str
    title_pt: str
    text_pt: str


# One-off incidents on purpose: a classifier that finds a pattern in everything is broken.
ONE_OFFS: tuple[OneOff, ...] = (
    OneOff(
        "disk-full",
        "Disk filled on the log volume",
        "The log volume hit 100% disk full after debug logging was left enabled overnight. No space left on device appeared in the application log. Root cause addressed: log rotation restored and the debug flag reverted.",
        "Disco cheio no volume de logs",
        "O volume de logs chegou a 100% de disco cheio depois que o log de debug ficou ligado durante a noite. No space left on device apareceu no log da aplicacao. Causa raiz tratada: rotacao de log restaurada e flag de debug revertida.",
    ),
    OneOff(
        "partner-backlog",
        "Backlog after a partner outage",
        "A partner endpoint was unavailable for {hours} hours and the outbound queue depth grew steadily. Nothing was saturated locally. The backlog drained on its own once the partner recovered.",
        "Backlog apos indisponibilidade de parceiro",
        "O endpoint de um parceiro ficou indisponivel por {hours} horas e a fila de saida cresceu sem parar. Nada saturou localmente. O backlog drenou sozinho quando o parceiro voltou.",
    ),
    OneOff(
        "dns",
        "Internal name pointed to a retired address",
        "A manual edit left the internal DNS record of {service} pointing at a decommissioned address. Callers failed to resolve the new host until the record was fixed and caches expired.",
        "Nome interno apontando para endereco desativado",
        "Uma edicao manual deixou o registro DNS interno do {service} apontando para um endereco desativado. Os clientes falharam ate o registro ser corrigido e os caches expirarem.",
    ),
    OneOff(
        "clock",
        "Clock drift broke token validation",
        "Time synchronization was disabled on one host and its clock drifted {mins} minutes. Tokens issued there were rejected as not yet valid by every other service.",
        "Desvio de relogio quebrou validacao de token",
        "A sincronizacao de horario estava desligada em um host e o relogio desviou {mins} minutos. Tokens emitidos ali foram rejeitados como ainda nao validos pelos outros servicos.",
    ),
    OneOff(
        "feature-flag",
        "Feature flag enabled for every tenant",
        "A feature flag meant for one pilot customer was switched on for all tenants. The new flow had not been validated for the rest and was switched off {mins} minutes later.",
        "Feature flag ligada para todos os clientes",
        "Uma feature flag destinada a um cliente piloto foi ligada para todos os tenants. O fluxo novo nao tinha sido validado para os demais e foi desligado {mins} minutos depois.",
    ),
    OneOff(
        "quota",
        "Cloud API throttled after quota exhaustion",
        "An unrelated inventory script consumed the account API quota and the provider started throttling calls. Uploads from {service} were rejected with rate exceeded until the quota reset.",
        "API da nuvem limitada por cota esgotada",
        "Um script de inventario sem relacao consumiu a cota de API da conta e o provedor passou a limitar chamadas. Uploads do {service} foram recusados com rate exceeded ate a cota renovar.",
    ),
    OneOff(
        "dst",
        "Daylight saving change ran a report twice",
        "The daylight saving time change made the monthly statement job run twice in the same night. Customers received duplicated statements.",
        "Horario de verao executou relatorio duas vezes",
        "A mudanca de horario de verao fez o job de extrato mensal rodar duas vezes na mesma noite. Clientes receberam extratos duplicados.",
    ),
    OneOff(
        "unit-mismatch",
        "Timeout configured in the wrong unit",
        "A configuration value meant as {secs} seconds was read as milliseconds, so outbound calls from {service} timed out almost immediately after the change.",
        "Timeout configurado na unidade errada",
        "Um valor de configuracao pensado como {secs} segundos foi lido como milissegundos, e as chamadas de saida do {service} expiravam quase de imediato apos a mudanca.",
    ),
    OneOff(
        "email-block",
        "Email provider rejected outbound messages",
        "The transactional email provider blocked our sender after a reputation drop, and notifications from {service} bounced for {hours} hours.",
        "Provedor de e-mail recusou mensagens",
        "O provedor de e-mail transacional bloqueou nosso remetente depois de uma queda de reputacao, e as notificacoes do {service} voltaram por {hours} horas.",
    ),
    OneOff(
        "password",
        "Expired service account password stopped an export",
        "The password of a service account expired and the nightly export to the data warehouse failed to log in. No other system was affected.",
        "Senha expirada de conta de servico parou exportacao",
        "A senha de uma conta de servico expirou e a exportacao noturna para o data warehouse falhou no login. Nenhum outro sistema foi afetado.",
    ),
    OneOff(
        "cdn-cache",
        "Stale frontend bundle served after a CDN change",
        "A CDN rule change kept serving the previous frontend bundle, so browsers loaded code that called an endpoint already removed.",
        "Bundle antigo servido apos mudanca na CDN",
        "Uma mudanca de regra na CDN continuou servindo o bundle anterior do frontend, e os navegadores carregaram codigo que chamava um endpoint ja removido.",
    ),
    OneOff(
        "migration",
        "Data migration updated the wrong column",
        "A one-time data migration updated the notification preference column instead of the marketing flag. The change was reverted from the audit table.",
        "Migracao de dados alterou a coluna errada",
        "Uma migracao pontual alterou a coluna de preferencia de notificacao em vez da flag de marketing. A mudanca foi revertida a partir da tabela de auditoria.",
    ),
)


def family_for(label: Label) -> Family:
    return next(family for family in FAMILIES if family.label is label)
