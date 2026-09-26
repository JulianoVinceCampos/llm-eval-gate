## O que muda

<!-- Um parágrafo. O que fica diferente depois deste PR. -->

## Por quê

<!-- O problema resolvido. Link para a issue, se houver. -->

## Como verificar

<!-- Comandos exatos que quem revisa consegue rodar. `make check` costuma bastar. -->

```bash
make check
```

## Evidência e referência

<!-- Obrigatório quando o PR toca prompt, cassette, referência, spec, política ou estatística. -->

- [ ] Nenhuma referência em `evals/reference/` mudou, ou a mudança é intencional e explicada acima
- [ ] Prompt ou parâmetro de modelo alterado vem acompanhado de nova gravação pelo `live-eval`
- [ ] Mudança em `stats.py`, `gate.py` ou `calibration.py` vem com `llm-eval-gate calibrate` rodado e a tabela revisada

## Riscos e rollback

<!-- O que pode quebrar e como desfazer. "Nenhum" vale, se for verdade. -->

## Checklist

- [ ] `make check` passa localmente
- [ ] Comportamento novo coberto por teste que falha sem a mudança
- [ ] Nenhum contexto corporativo (hostname, id de conta, endereço, documento fiscal, texto real)
- [ ] Docs ou ADR atualizados quando uma decisão de desenho mudou
