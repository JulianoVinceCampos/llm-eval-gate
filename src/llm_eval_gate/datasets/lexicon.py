"""Hard-split vocabulary: the same observations, described without the template wording.

What this split measures, stated plainly (ADR-0008): robustness to vocabulary shift.
Every sentence here describes the same observation as its templated counterpart in
families.py, written from the failure mechanism and deliberately avoiding the keywords
the rule table looks for. That makes the paraphrases adversarial to keyword rules by
construction, and the report says so instead of presenting it as a neutral benchmark.

To stay close to how people actually write, the generator keeps the templated sentence
with a fixed probability, so most hard cases mix both vocabularies. The dataset records
per case whether it is a pure shift (`vocab-shift`) or a mix (`vocab-mix`).

Distractors are the second adversarial axis: sentences that *rule out* a failure mode
while naming it ("no OOM this time"). A keyword matcher reads the keyword; a reader reads
the negation. Each case records which distractor it carries, so accuracy can be sliced by it.
"""

from __future__ import annotations

from dataclasses import dataclass

from llm_eval_gate.labels import Label


@dataclass(frozen=True, slots=True)
class Phrasings:
    en: tuple[str, ...]
    pt: tuple[str, ...]

    def pick_pool(self, lang: str) -> tuple[str, ...]:
        return self.pt if lang == "pt" else self.en


@dataclass(frozen=True, slots=True)
class HardFamily:
    label: Label
    observations: tuple[Phrasings, ...]
    trigger: Phrasings
    mitigation: Phrasings


def _p(en: tuple[str, ...], pt: tuple[str, ...]) -> Phrasings:
    return Phrasings(en=en, pt=pt)


HARD_FAMILIES: tuple[HardFamily, ...] = (
    HardFamily(
        Label.POOL_LOCK,
        observations=(
            _p(
                (
                    "The primary database sat near the top of its processor graph for most of an hour.",
                    "Processor usage on the database stayed close to the ceiling for {mins} minutes.",
                ),
                (
                    "O banco principal ficou colado no topo do grafico de processador por quase uma hora.",
                    "O uso de processador do banco ficou perto do teto por {mins} minutos.",
                ),
            ),
            _p(
                (
                    "Every application instance ran out of free database connections, and new requests waited for one to be handed back.",
                    "No connection was left to borrow on any instance; callers queued until one came back.",
                ),
                (
                    "Todas as instancias ficaram sem conexao livre com o banco, e as requisicoes novas esperavam alguma ser devolvida.",
                    "Nenhuma conexao estava disponivel em nenhuma instancia; as chamadas enfileiravam ate uma voltar.",
                ),
            ),
            _p(
                (
                    "The DBA found writers stuck behind a few sessions that never released the main table.",
                    "A handful of sessions kept exclusive hold of the busiest table and everyone else lined up behind them.",
                ),
                (
                    "O DBA encontrou escritas presas atras de poucas sessoes que nunca liberavam a tabela principal.",
                    "Algumas sessoes mantinham posse exclusiva da tabela mais usada e o resto fazia fila atras delas.",
                ),
            ),
            _p(
                (
                    "The symptom looked identical on each host, which ruled out a single bad machine.",
                    "Every instance showed the same curve at the same time.",
                ),
                (
                    "O sintoma era identico em cada host, o que descartou uma maquina isolada.",
                    "Cada instancia mostrou a mesma curva no mesmo horario.",
                ),
            ),
        ),
        trigger=_p(
            (
                "One save in the domain layer fanned out into dozens of SQL writes because of how the entity graph was mapped.",
                "A single business operation generated dozens of statements through cascading entity updates.",
            ),
            (
                "Uma gravacao na camada de dominio se desdobrou em dezenas de escritas SQL por causa do mapeamento das entidades.",
                "Uma unica operacao de negocio gerou dezenas de comandos por atualizacoes em cascata das entidades.",
            ),
        ),
        mitigation=_p(
            (
                "Bounced the application servers one at a time while the DBA ended the sessions holding the table.",
                "Cycled the app servers in sequence and terminated the sessions that were holding everyone up.",
            ),
            (
                "Reiniciamos os servidores de aplicacao um por vez enquanto o DBA encerrava as sessoes que seguravam a tabela.",
                "Ciclamos os servidores de aplicacao em sequencia e encerramos as sessoes que travavam os demais.",
            ),
        ),
    ),
    HardFamily(
        Label.HEAP_OOM,
        observations=(
            _p(
                (
                    "One instance died with an out-of-memory error halfway through reading a very large upload.",
                    "A single process crashed after using up all of its memory while it read one enormous request.",
                ),
                (
                    "Uma instancia morreu por falta de memoria no meio da leitura de um envio muito grande.",
                    "Um unico processo caiu depois de consumir toda a memoria lendo uma requisicao enorme.",
                ),
            ),
            _p(
                (
                    "The garbage collector ran nonstop and reclaimed almost nothing.",
                    "Collection cycles came back to back while the long-lived area stayed full.",
                ),
                (
                    "O coletor de lixo rodou sem parar e quase nao recuperou nada.",
                    "Ciclos de coleta vieram um atras do outro enquanto a area de longa duracao seguia cheia.",
                ),
            ),
            _p(
                (
                    "The other instances never noticed and kept serving traffic.",
                    "Only that process went down; its peers carried on normally.",
                ),
                (
                    "As outras instancias nem perceberam e seguiram atendendo.",
                    "So aquele processo caiu; os demais seguiram normais.",
                ),
            ),
            _p(
                (
                    "The partner had packed several weeks of records into one submission.",
                    "That upload carried tens of thousands of rows in a single file.",
                ),
                (
                    "O parceiro juntou varias semanas de registros em um unico envio.",
                    "Aquele envio trazia dezenas de milhares de linhas em um unico arquivo.",
                ),
            ),
        ),
        trigger=_p(
            (
                "The importer builds the whole document in memory before the first row is saved.",
                "Everything is loaded into memory up front and only then persisted.",
            ),
            (
                "O importador monta o documento inteiro em memoria antes de salvar a primeira linha.",
                "Tudo e carregado em memoria de uma vez e so depois persistido.",
            ),
        ),
        mitigation=_p(
            (
                "Restarted that one process and asked the sender to split the file into smaller submissions.",
                "Brought the process back and agreed with the partner on smaller batches.",
            ),
            (
                "Reiniciamos aquele processo e pedimos ao remetente para dividir o arquivo em envios menores.",
                "Subimos o processo de novo e combinamos lotes menores com o parceiro.",
            ),
        ),
    ),
    HardFamily(
        Label.RETRY_STORM,
        observations=(
            _p(
                (
                    "The database stayed busy for most of an hour while the machine running the jobs was almost idle.",
                    "Load on the database stayed high even though the job server itself was doing nothing.",
                ),
                (
                    "O banco ficou ocupado por quase uma hora enquanto a maquina que roda os jobs estava praticamente ociosa.",
                    "A carga no banco seguiu alta mesmo com o servidor de jobs sem fazer nada.",
                ),
            ),
            _p(
                (
                    "Three timed jobs kept failing and firing again immediately, with no pause between attempts.",
                    "The same jobs hammered the database, trying again the instant they failed.",
                ),
                (
                    "Tres jobs com hora marcada falhavam e disparavam de novo na hora, sem pausa entre tentativas.",
                    "Os mesmos jobs martelavam o banco, tentando outra vez no instante em que falhavam.",
                ),
            ),
            _p(
                (
                    "The overnight processing run collided with the first heavy report of the morning.",
                    "Work left over from the night overlapped with the morning peak.",
                ),
                (
                    "A rodada da madrugada colidiu com o primeiro relatorio pesado da manha.",
                    "O trabalho que sobrou da noite se sobrepos ao pico da manha.",
                ),
            ),
            _p(
                (
                    "The worker on the job server kept spawning threads until it had thousands of them.",
                    "Thread count on the job machine grew without bound.",
                ),
                (
                    "O worker do servidor de jobs criou threads sem parar ate ter milhares delas.",
                    "A quantidade de threads na maquina de jobs cresceu sem limite.",
                ),
            ),
        ),
        trigger=_p(
            (
                "A short database hiccup made every timer fail at once, and each one tried again right away.",
                "A brief blip in the database turned into a loop of immediate new attempts.",
            ),
            (
                "Um soluco curto no banco fez todos os timers falharem juntos, e cada um tentou de novo na hora.",
                "Uma oscilacao breve no banco virou um ciclo de novas tentativas imediatas.",
            ),
        ),
        mitigation=_p(
            (
                "Restarted only the job runner and spread the timers across the hour.",
                "Stopped the job process alone and staggered the start times.",
            ),
            (
                "Reiniciamos apenas o executor de jobs e espalhamos os timers ao longo da hora.",
                "Paramos so o processo de jobs e escalonamos os horarios de inicio.",
            ),
        ),
    ),
    HardFamily(
        Label.ROLLBACK,
        observations=(
            _p(
                (
                    "The database was working hard even though no new requests were arriving; it was reverting what had already been written.",
                    "Heavy database activity continued with no incoming traffic at all.",
                ),
                (
                    "O banco trabalhava pesado mesmo sem requisicoes novas; estava revertendo o que ja tinha sido escrito.",
                    "Atividade intensa no banco continuou sem nenhum trafego chegando.",
                ),
            ),
            _p(
                (
                    "One request had kept a single unit of work open for close to an hour before it failed.",
                    "A single enormous piece of work stayed uncommitted for {mins} minutes.",
                ),
                (
                    "Uma requisicao manteve uma unica unidade de trabalho aberta por quase uma hora antes de falhar.",
                    "Um unico bloco enorme de trabalho ficou sem commit por {mins} minutos.",
                ),
            ),
            _p(
                (
                    "Bouncing the database made no difference; recovery picked the reversal up where it stopped.",
                    "A restart did not help, the database resumed undoing the work on its way back.",
                ),
                (
                    "Reiniciar o banco nao mudou nada; a recuperacao retomou a reversao de onde tinha parado.",
                    "O restart nao ajudou, o banco voltou retomando o desfazimento.",
                ),
            ),
            _p(
                (
                    "The space holding prior row versions grew in step with the volume already written.",
                    "The before-image storage kept growing alongside the data written so far.",
                ),
                (
                    "O espaco que guarda as versoes anteriores das linhas cresceu junto com o volume ja escrito.",
                    "O armazenamento de imagens anteriores crescia junto com os dados ja gravados.",
                ),
            ),
        ),
        trigger=_p(
            (
                "Each request is committed as one indivisible piece of work, so a failure costs as much as everything already done.",
                "The whole payload goes in as one all-or-nothing unit.",
            ),
            (
                "Cada requisicao vira uma unica peca indivisivel de trabalho, entao a falha custa tanto quanto tudo que ja foi feito.",
                "A carga inteira entra como uma unidade tudo ou nada.",
            ),
        ),
        mitigation=_p(
            (
                "Freed capacity by ending idle sessions and let the reversal finish; forcing a restart only makes it longer.",
                "Waited it out after clearing waiting sessions, since a forced restart would only extend it.",
            ),
            (
                "Liberamos capacidade encerrando sessoes ociosas e deixamos a reversao terminar; forcar restart so prolonga.",
                "Esperamos terminar depois de limpar sessoes em espera, porque um restart forcado so estenderia.",
            ),
        ),
    ),
    HardFamily(
        Label.CERT,
        observations=(
            _p(
                (
                    "The public listener was presenting a credential whose validity window had closed a few days before.",
                    "The edge was serving an identity document that had stopped being valid {days} days earlier.",
                ),
                (
                    "O listener publico apresentava uma credencial cuja validade tinha terminado alguns dias antes.",
                    "A borda servia um documento de identidade que deixou de valer {days} dias antes.",
                ),
            ),
            _p(
                (
                    "Every target behind the balancer started failing its probe in the same minute.",
                    "All backends were marked down at exactly the same moment.",
                ),
                (
                    "Todos os alvos atras do balanceador comecaram a falhar na verificacao no mesmo minuto.",
                    "Todos os backends foram marcados como fora no mesmo instante.",
                ),
            ),
            _p(
                (
                    "Customers on the internet could not complete a handshake, while calls from inside the network worked.",
                    "External users got secure-channel errors; internal traffic was fine.",
                ),
                (
                    "Clientes na internet nao completavam o handshake, enquanto chamadas de dentro da rede funcionavam.",
                    "Usuarios externos recebiam erro de canal seguro; o trafego interno seguia normal.",
                ),
            ),
            _p(
                (
                    "Nothing had been released in the week before.",
                    "There had been no change to the service for days.",
                ),
                (
                    "Nada tinha sido publicado na semana anterior.",
                    "Nao houve mudanca no servico havia dias.",
                ),
            ),
        ),
        trigger=_p(
            (
                "Renewal depended on someone remembering, and the reminder belonged to nobody.",
                "Nobody owned the renewal date.",
            ),
            (
                "A renovacao dependia de alguem lembrar, e o lembrete nao era de ninguem.",
                "Ninguem era dono da data de renovacao.",
            ),
        ),
        mitigation=_p(
            (
                "Installed a fresh credential on the listener and automated the renewal.",
                "Replaced the identity on the edge and put renewal on autopilot.",
            ),
            (
                "Instalamos uma credencial nova no listener e automatizamos a renovacao.",
                "Trocamos a identidade da borda e deixamos a renovacao automatica.",
            ),
        ),
    ),
    HardFamily(
        Label.ACL,
        observations=(
            _p(
                (
                    "Through the corporate tunnel the application answered normally; from the internet it simply hung.",
                    "Inside the private network everything worked, outside requests never came back.",
                ),
                (
                    "Pelo tunel corporativo a aplicacao respondia normal; pela internet ela simplesmente travava.",
                    "Dentro da rede privada tudo funcionava, requisicoes de fora nunca voltavam.",
                ),
            ),
            _p(
                (
                    "The balancer's probes coming from its own address range never got an answer.",
                    "Probes from the balancer subnets were silently dropped.",
                ),
                (
                    "As verificacoes do balanceador vindas da propria faixa de enderecos nunca tinham resposta.",
                    "As verificacoes vindas das subnets do balanceador eram descartadas em silencio.",
                ),
            ),
            _p(
                (
                    "The inbound filtering on the instances did not include the addresses the balancer uses.",
                    "The instance-level allow list was missing the balancer ranges.",
                ),
                (
                    "A filtragem de entrada nas instancias nao incluia os enderecos que o balanceador usa.",
                    "A lista de permissao das instancias nao tinha as faixas do balanceador.",
                ),
            ),
            _p(
                (
                    "The application logs were clean the whole time.",
                    "Not a single error came from the application itself.",
                ),
                (
                    "Os logs da aplicacao ficaram limpos o tempo todo.",
                    "Nenhum erro veio da propria aplicacao.",
                ),
            ),
        ),
        trigger=_p(
            (
                "Preserving the caller's address exposed source addresses that no inbound entry allowed.",
                "Keeping the original client address meant traffic arrived from ranges nobody had allowed.",
            ),
            (
                "Preservar o endereco de origem expos enderecos que nenhuma entrada permitia.",
                "Manter o endereco original do cliente fez o trafego chegar de faixas que ninguem tinha liberado.",
            ),
        ),
        mitigation=_p(
            (
                "Opened the required ports to the balancer's address ranges.",
                "Allowed the balancer ranges on the ports that were needed.",
            ),
            (
                "Liberamos as portas necessarias para as faixas do balanceador.",
                "Permitimos as faixas do balanceador nas portas necessarias.",
            ),
        ),
    ),
    HardFamily(
        Label.LB_APP,
        observations=(
            _p(
                (
                    "One instance was doing twice the work of its peers even though overall traffic was flat.",
                    "A single node carried double the load of the others with no rise in requests.",
                ),
                (
                    "Uma instancia fazia o dobro do trabalho das outras mesmo com o trafego estavel.",
                    "Um unico no carregava o dobro da carga dos demais sem aumento de requisicoes.",
                ),
            ),
            _p(
                (
                    "Session affinity was recalculated after the nightly restart took part of the fleet down.",
                    "Sticky routing reshuffled once part of the fleet came back from the daily restart.",
                ),
                (
                    "A afinidade de sessao foi recalculada depois que o restart noturno derrubou parte da frota.",
                    "O roteamento por afinidade embaralhou quando parte da frota voltou do restart diario.",
                ),
            ),
            _p(
                (
                    "A lookup that assumed a value was always present came back empty a few thousand times.",
                    "Code that expected a result to exist hit an empty answer again and again.",
                ),
                (
                    "Uma busca que assumia valor sempre presente voltou vazia alguns milhares de vezes.",
                    "Um codigo que esperava existir um resultado encontrou resposta vazia seguidas vezes.",
                ),
            ),
            _p(
                (
                    "Right after that, the same code path failed trying to treat one object type as another.",
                    "The same flow then broke on a type conversion that could not work.",
                ),
                (
                    "Logo depois, o mesmo caminho de codigo falhou ao tratar um tipo de objeto como outro.",
                    "O mesmo fluxo quebrou em seguida numa conversao de tipo impossivel.",
                ),
            ),
            _p(
                (
                    "The partner was never notified, so on their side the signed document stayed invalid.",
                    "No notification reached the partner, and the document was left unusable on their end.",
                ),
                (
                    "O parceiro nunca foi notificado, entao do lado dele o documento assinado ficou invalido.",
                    "Nenhuma notificacao chegou ao parceiro, e o documento ficou inutilizavel do lado dele.",
                ),
            ),
        ),
        trigger=_p(
            (
                "A mismatch in how the record type was stored made the lookup miss a valid party.",
                "The record type column disagreed with the mapping, so a valid party was not found.",
            ),
            (
                "Uma divergencia na forma como o tipo do registro era gravado fez a busca nao achar uma parte valida.",
                "A coluna de tipo divergia do mapeamento, e uma parte valida nao era encontrada.",
            ),
        ),
        mitigation=_p(
            (
                "Released stuck sessions and guarded the lookup against an empty result.",
                "Cleared the stuck sessions and made the lookup handle a missing value.",
            ),
            (
                "Liberamos as sessoes presas e protegemos a busca contra resultado vazio.",
                "Limpamos as sessoes presas e fizemos a busca tratar valor ausente.",
            ),
        ),
    ),
    HardFamily(
        Label.SLOW_QUERY,
        observations=(
            _p(
                (
                    "Right after the maintenance, the slowest one percent of database calls went from tens of milliseconds to whole seconds.",
                    "Tail latency of database calls jumped from about {ms} milliseconds to over a second after maintenance.",
                ),
                (
                    "Depois da manutencao programada, a cauda de latencia das chamadas ao banco saltou de cerca de {ms} milissegundos para mais de um segundo.",
                    "Logo apos a manutencao, o 1% mais lento das chamadas ao banco saiu de dezenas de milissegundos para segundos inteiros.",
                ),
            ),
            _p(
                (
                    "The log of long-running statements filled up with the same three statements.",
                    "The same three statements dominated the list of slowest calls.",
                ),
                (
                    "O registro de comandos demorados encheu com os mesmos tres comandos.",
                    "Os mesmos tres comandos dominaram a lista de chamadas mais lentas.",
                ),
            ),
            _p(
                (
                    "The database was busy, but connections were plentiful the whole time.",
                    "Processor use on the database was high while plenty of connections sat free.",
                ),
                (
                    "O banco estava ocupado, mas havia conexoes de sobra o tempo todo.",
                    "O uso de processador do banco estava alto enquanto muitas conexoes seguiam livres.",
                ),
            ),
            _p(
                (
                    "A new version had gone out the evening before.",
                    "The service had been updated the previous evening.",
                ),
                (
                    "Uma versao nova tinha saido na noite anterior.",
                    "O servico tinha sido atualizado na noite anterior.",
                ),
            ),
        ),
        trigger=_p(
            (
                "An outdated execution plan survived the statistics refresh.",
                "The optimizer kept using an old plan after the statistics were rebuilt.",
            ),
            (
                "Um plano de execucao desatualizado sobreviveu a atualizacao de estatisticas.",
                "O otimizador seguiu usando um plano antigo depois que as estatisticas foram refeitas.",
            ),
        ),
        mitigation=_p(
            (
                "Invalidated the cached plan and set up a recurring statistics refresh.",
                "Flushed the stored plan and made the statistics refresh run regularly.",
            ),
            (
                "Invalidamos o plano em cache e deixamos a atualizacao de estatisticas recorrente.",
                "Descartamos o plano guardado e fizemos a atualizacao de estatisticas rodar com frequencia.",
            ),
        ),
    ),
)

# Sentences that name a failure mode in order to rule it out. Keyed by the mode they name.
DISTRACTORS: dict[Label, Phrasings] = {
    Label.POOL_LOCK: _p(
        (
            "The DBA looked for lock contention and found none.",
            "We ruled out a pool 80/80 situation early; usage peaked at half.",
        ),
        (
            "O DBA procurou contencao de lock e nao encontrou nada.",
            "Descartamos pool esgotado logo no inicio: o uso ficou na metade.",
        ),
    ),
    Label.HEAP_OOM: _p(
        (
            "There was no OOM this time; heap usage stayed flat.",
            "Nobody saw a Full GC during the window.",
        ),
        (
            "Nao houve OOM desta vez; o uso de heap ficou estavel.",
            "Ninguem viu Full GC durante a janela.",
        ),
    ),
    Label.RETRY_STORM: _p(
        (
            "Someone suspected a retry storm, but the job logs showed normal backoff.",
            "The batch jobs were idle during the incident.",
        ),
        (
            "Alguem suspeitou de retry em loop, mas os logs dos jobs mostraram backoff normal.",
            "Os jobs de batch estavam parados durante o incidente.",
        ),
    ),
    Label.ROLLBACK: _p(
        (
            "No rollback was involved; every transaction had already committed.",
            "The undo log stayed small the whole time.",
        ),
        (
            "Nao houve rollback; todas as transacoes ja tinham feito commit.",
            "O undo log ficou pequeno o tempo todo.",
        ),
    ),
    Label.CERT: _p(
        (
            "The TLS certificate was checked first and does not expire until next year.",
            "Certificate expiry was ruled out in the first ten minutes.",
        ),
        (
            "O certificado TLS foi o primeiro a ser verificado e so expira no ano que vem.",
            "O certificado nao estava expirado, isso foi descartado cedo.",
        ),
    ),
    Label.ACL: _p(
        (
            "The security group rules were reviewed and had not changed.",
            "The firewall was not involved.",
        ),
        (
            "As regras do security group foram revisadas e nao tinham mudado.",
            "O firewall nao teve participacao.",
        ),
    ),
    Label.LB_APP: _p(
        (
            "Load was spread evenly; stickiness was not a factor.",
            "No NullPointerException appeared in the logs.",
        ),
        (
            "A carga estava distribuida; stickiness nao teve papel.",
            "Nenhum NullPointerException apareceu nos logs.",
        ),
    ),
    Label.SLOW_QUERY: _p(
        (
            "The slow query log was empty for the whole period.",
            "Query latency at p99 stayed under 50 ms.",
        ),
        (
            "O log de query lenta ficou vazio o periodo todo.",
            "O p99 das queries ficou abaixo de 50 ms.",
        ),
    ),
}

NOISE = _p(
    (
        "The incident bridge opened at {hour}:05 and closed about an hour later.",
        "On-call handover happened in the middle of the incident.",
        "A customer ticket arrived before the first alert did.",
        "The status page was updated twice during the event.",
        "Two engineers from another team joined to help.",
        "The runbook link in the alert pointed to an old page.",
    ),
    (
        "A sala de guerra abriu as {hour}:05 e fechou cerca de uma hora depois.",
        "A passagem de plantao aconteceu no meio do incidente.",
        "Um chamado de cliente chegou antes do primeiro alerta.",
        "A pagina de status foi atualizada duas vezes durante o evento.",
        "Dois engenheiros de outro time entraram para ajudar.",
        "O link de runbook do alerta apontava para uma pagina antiga.",
    ),
)

# Paraphrases of the one-off incidents in families.py, keyed by OneOff.key.
ONE_OFFS_HARD: dict[str, Phrasings] = {
    "disk-full": _p(
        (
            "The volume that stores application logs ran out of room after verbose logging stayed on all night, and writes started failing.",
        ),
        (
            "O volume que guarda os logs da aplicacao ficou sem espaco depois que o log detalhado ficou ligado a noite toda, e as escritas passaram a falhar.",
        ),
    ),
    "partner-backlog": _p(
        (
            "A partner system was unreachable for a couple of hours; messages piled up for delivery and drained by themselves once it came back.",
        ),
        (
            "Um sistema parceiro ficou inacessivel por algumas horas; as mensagens acumularam para envio e escoaram sozinhas quando ele voltou.",
        ),
    ),
    "dns": _p(
        (
            "After a manual edit, the internal name of the service resolved to a machine that no longer exists.",
        ),
        (
            "Depois de uma edicao manual, o nome interno do servico passou a resolver para uma maquina que nao existe mais.",
        ),
    ),
    "clock": _p(
        (
            "One host lost time synchronization and its clock ran several minutes ahead, so tokens it issued were refused elsewhere.",
        ),
        (
            "Um host perdeu a sincronizacao de horario e o relogio adiantou varios minutos, e os tokens emitidos ali eram recusados nos outros servicos.",
        ),
    ),
    "feature-flag": _p(
        (
            "A toggle meant for a single pilot customer was turned on for everyone and had to be turned off shortly after.",
        ),
        (
            "Uma chave pensada para um unico cliente piloto foi ligada para todos e precisou ser desligada logo depois.",
        ),
    ),
    "quota": _p(
        (
            "An unrelated script used up the account's API allowance and the provider began refusing our uploads until the allowance reset.",
        ),
        (
            "Um script sem relacao consumiu a franquia de API da conta e o provedor passou a recusar nossos envios ate a franquia renovar.",
        ),
    ),
    "dst": _p(
        (
            "The clock change for daylight saving made the monthly statement run twice in one night.",
        ),
        ("A troca de horario de verao fez o extrato mensal rodar duas vezes na mesma noite.",),
    ),
    "unit-mismatch": _p(
        (
            "A setting written in seconds was interpreted as milliseconds, so outgoing calls gave up almost instantly.",
        ),
        (
            "Uma configuracao escrita em segundos foi interpretada como milissegundos, e as chamadas de saida desistiam quase na hora.",
        ),
    ),
    "email-block": _p(
        (
            "Our transactional email sender was blocked by the provider after a reputation drop, and notifications bounced for hours.",
        ),
        (
            "Nosso remetente de e-mail transacional foi bloqueado pelo provedor depois de uma queda de reputacao, e as notificacoes voltaram por horas.",
        ),
    ),
    "password": _p(
        (
            "A service account password reached its end of life and the nightly export could not sign in.",
        ),
        ("A senha de uma conta de servico venceu e a exportacao noturna nao conseguiu entrar.",),
    ),
    "cdn-cache": _p(
        (
            "An edge caching rule kept handing out the previous frontend build, which called an endpoint that had been removed.",
        ),
        (
            "Uma regra de cache na borda seguiu entregando a versao anterior do frontend, que chamava um endpoint ja removido.",
        ),
    ),
    "migration": _p(
        ("A one-off data fix changed the wrong column and was reverted from the audit history.",),
        (
            "Uma correcao pontual de dados alterou a coluna errada e foi revertida pelo historico de auditoria.",
        ),
    ),
}

OPAQUE_TITLES = _p(
    (
        "Degradation on {service}",
        "Customer impact on {service}",
        "{severity} incident on {service}",
        "Service disruption, {date}",
        "Partial outage on {service}",
    ),
    (
        "Degradacao no {service}",
        "Impacto em clientes no {service}",
        "Incidente {severity} no {service}",
        "Interrupcao de servico, {date}",
        "Indisponibilidade parcial no {service}",
    ),
)

SECTION_HEADERS = _p(
    ("What we saw|What set it off|What we did", "Timeline|Contributing factors|Response"),
    ("O que vimos|O que disparou|O que fizemos", "Linha do tempo|Fatores contribuintes|Resposta"),
)


def hard_family_for(label: Label) -> HardFamily:
    return next(family for family in HARD_FAMILIES if family.label is label)
