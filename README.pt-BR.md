# Direct Publication

**A urna cifra. A blockchain registra. Você confere.**

Este protótipo mantém a urna offline enquanto você vota. Ela emite um recibo assinado depois da escolha e antes de CONFIRM ou TEST; o recibo não revela o candidato. Após o encerramento, a urna envia seu lote assinado diretamente ao contrato. A assinatura protege autenticidade e integridade sob uma chave confiável, não a intenção do eleitor; os mesmos bytes assinados continuam verificando da mesma forma quando encaminhados. Três de cinco guardiões contribuem para abrir a soma cifrada, e um programa separado confere os recibos fornecidos e verifica a apuração.

[English](README.md) · [Rodar a demo](#rodar) · [Estudo](#estudo)

[![Como um recibo liga a urna offline à blockchain e à conferência pública](docs/intro.pt-BR.gif)](docs/intro.pt-BR.mp4)

A animação acima roda automaticamente e dura 56 segundos. **[Abrir o vídeo com pausa e avanço](docs/intro.pt-BR.mp4).** Acompanhe uma cédula do recibo até a conferência. Animação ilustrativa com legendas na imagem, sem áudio; `R7` é um código simbólico, não um recibo real.

## Quem faz o quê

1. **Urna offline: cifra e emite o recibo.** Depois da escolha do candidato e antes da decisão do eleitor, a urna emite o recibo assinado. Antes disso, reserva capacidade em bytes canônicos para as duas possibilidades: CAST ou SPOILED. Uma urna honesta rejeita a próxima cédula se o lote estiver cheio, sem emitir recibo, e preserva as cédulas aceitas anteriormente. Isso não é recuperação persistente após falha. `CONFIRM` mantém a cédula cifrada e registra o voto. `TEST` abre e anula a cédula. A criptografia rejeita uma abertura inválida, mas uma abertura válida de outra escolha só é detectável comparando-a independentemente com a intenção lembrada pelo eleitor.
2. **Blockchain: registra os lotes assinados.** Depois do encerramento, a urna envia seu lote diretamente. O contrato Solidity confere a assinatura e aceita um único lote por urna registrada, dentro do prazo. O mesário habilita essa conexão simulada só depois do encerramento; o programa da urna não liga a rede antes. As provas dos votos são conferidas fora do contrato. Na demo, a blockchain é um nó Anvil local. Encaminhar exatamente os mesmos bytes assinados não altera a validade da assinatura; isso não significa que um intermediário acrescente ou remova integridade criptográfica.
3. **Guardiões: contribuem para abrir a soma.** Três guardiões distintos de cinco precisam enviar o mesmo digest agregado antes que o contrato o fixe. O fluxo não publica a abertura dos votos confirmados.
4. **Verificador independente: confere recibos e resultado.** Com arquivos públicos e recibos fornecidos, verifica inclusão, assinaturas, provas das cédulas e apuração. Se um recibo fornecido estiver ausente, a conferência acusa; cédulas de teste não entram na contagem. Contribuições inválidas de trustees são excluídas e listadas se restarem ao menos três contribuições válidas correspondentes. Isso prova uma apuração válida com contribuições inválidas reportadas, não que todo o transcript seja válido.

**Contexto brasileiro.** O TSE documenta a publicação de totais por seção, registros digitais dos votos e logs para auditoria. O Boletim de Urna informa totais, sem identificar uma cédula individual. [Contexto e fontes oficiais](docs/media.md#brazilian-system-context).

## Execução real no terminal

![Eleição real da demo: votar, testar, publicar e conferir](docs/demo.pt-BR.gif)

Gravação histórica de referência: duas urnas, seis votos confirmados, um teste anulado e apuração `(2, 2, 2)`, reproduzida a **3×**. Os recibos e resultados são daquela execução registrada, não de resultados novos para as mudanças atuais. [Roteiro VHS](docs/demo.pt-BR.tape) · [Como reproduzir a gravação](docs/media.md).

## Rodar

Instale Python 3.12, uv 0.11.7, Foundry 1.8.5 (`forge` e `anvil`) e solc 0.8.28.

```sh
uv sync --locked
make demo DEMO_LANG=pt-BR
```

`make demo` compila o contrato, sobe um Anvil só dessa execução e imprime a apuração do verificador. Os tempos, gas e resultados do notebook incluídos no repositório são evidências históricas do checkpoint `f8d9358`, não benchmarks novos da árvore de trabalho atual. Consulte [`demo/results/measurements.json`](demo/results/measurements.json) e [`demo/notebook.ipynb`](demo/notebook.ipynb).

Para executar o verificador separado sobre os arquivos sintéticos incluídos, use a raiz do repositório:

```sh
.venv/bin/python -m verifier.cli --manifest demo/results/manifest.json --evidence demo/results/evidence.json --receipts demo/results/receipts.json
```

O comando confere os arquivos fornecidos entre si. Não estabelece a origem autêntica deles, e a inclusão não prova que a cédula corresponde à escolha pretendida pelo eleitor.

### Docker Compose

O Compose sobe o Anvil e a demo numa rede interna, sem portas publicadas e sem volumes do host. Só inicia depois que você declara que o ambiente é uma VM privada isolada, sem montagens do host. Use um ambiente descartável. A semente do Anvil no arquivo é material público de teste.

```sh
RUN_ID="$(python3 -c 'import uuid; print(uuid.uuid4().hex)')" && \
URNA_COMPOSE_RUN_ID="$RUN_ID" \
URNA_COMPOSE_ISOLATED_PRIVATE_VM=true \
URNA_COMPOSE_NO_HOST_MOUNTS=true \
docker compose -p "urna-aberta-$RUN_ID" up --build --abort-on-container-exit --exit-code-from demo
```

Em ARM, a imagem usa emulação amd64 só na etapa do compilador Solidity. Python e Anvil rodam nativos. O exemplo registrado de uma VM local sem montagens terminou com dois votos confirmados e apuração `(1, 1)`; é evidência histórica, não uma execução nova.

## O que a demo pega

`make attacks` roda as oito. Cada comando monta a falha e só termina bem quando a conferência pega.

| Falha | O que aparece | Comando |
| --- | --- | --- |
| Substituição pela urna | Rejeita abertura inválida; abertura válida de outra escolha só é detectada comparando-a com a intenção lembrada independentemente | [`attacks/machine.py`](attacks/machine.py), `make attack-machine` |
| Transporte alterado | Bytes assinados que deixaram de bater, recusados pelo contrato | [`attacks/transport.py`](attacks/transport.py), `make attack-transport` |
| Voto sumido | Recibo que falta num lote reassinado | [`attacks/drop_vote.py`](attacks/drop_vote.py), `make attack-drop-vote` |
| Apuração falsa | Total anunciado diferente do total conferido | [`attacks/tally.py`](attacks/tally.py), `make attack-tally` |
| Segundo lote | A mesma urna publicando de novo | [`attacks/duplicate.py`](attacks/duplicate.py), `make attack-duplicate` |
| Urna desconhecida | Assinatura de quem não foi registrado | [`attacks/rogue_machine.py`](attacks/rogue_machine.py), `make attack-rogue-machine` |
| Duas escolhas | Cédula com dois candidatos marcados | [`attacks/double_vote.py`](attacks/double_vote.py), `make attack-double-vote` |
| Rede cedo | Transporte habilitado antes do encerramento | [`attacks/early_network.py`](attacks/early_network.py), `make attack-early-network` |

## Para que serve o mural

O mural é o registro público dos lotes assinados e das partes dos trustees. O contrato aceita um lote de cada urna registrada e recusa repetição, assinante desconhecido ou bytes alterados. As provas das cédulas ficam com o verificador. Qualquer pessoa roda esse verificador nos arquivos publicados.

Um banco replicado também guardaria esses bytes. A blockchain deixa a regra de inclusão explícita e repetível. O Anvil da demo é local. Ele executa o contrato de verdade. É um processo só, então não faz o papel de validadores independentes, e um JSON copiado não prova que uma rede chegou a consenso. A assinatura da urna autentica bytes sob sua chave confiável; não atesta a intenção do eleitor nem acrescenta integridade quando os mesmos bytes assinados são encaminhados.

## Até onde isto vai

- O dealer das chaves é confiável, e a demo guarda as cinco partes. Três trustees que juntarem as partes leem votos individuais. A API pública devolve a apuração auditada.
- Interruptor, lacre, reinstalação e verificação de hash rodam num diretório temporário. Modelam o ritual. O recibo prova que aquela urna assinou aquela cédula. O prefixo curto de 80 bits só serve para achar. Decidem o hash completo e a assinatura.
- Não há lista de eleitores. A urna aceita outra cédula bem formada. Elegibilidade, comparecimento e coerção ficam fora deste código.
- `TEST` revela publicamente a escolha e a aleatoriedade da cédula testada. Uma abertura criptograficamente inválida é rejeitada, mas uma abertura válida para uma escolha substituída passa pela criptografia; detectá-la exige comparação independente com a intenção lembrada pelo eleitor. Não houve teste de usabilidade ou acessibilidade, e a demo não testa cédulas não testadas. Se os desafios forem independentes e imprevisíveis após o recibo, a expressão `1 - (1 - p)^k` no gráfico é um modelo estatístico, não evidência empírica nem garantia.
- Um manifesto aceita no máximo 16 urnas, 1000 cédulas em cada e apuração até 16000. Esses limites do protótipo não demonstram desempenho em escala nacional.
- A publicação direta usa o lote finalizado em memória. Falhar ao criar a cópia opcional de transporte gera um aviso, sem impedir esse envio. Isso não recupera uma conexão indisponível nem a memória perdida da urna.
- O mural fixa o digest agregado após três envios correspondentes de trustees distintos. O verificador pode reportar e excluir contribuições inválidas se houver pelo menos três válidas correspondentes; ainda para sem totais diante de erros em cédulas, lotes, recibos ou integridade dos eventos.

O desenho e o limite de confiança estão em [`docs/architecture.md`](docs/architecture.md) e [`docs/threat-model.md`](docs/threat-model.md).

## Estudo

João Henrique Menezes Silva Boia, *Direct Publication for Offline Ballots: An Executable Research Prototype* (outubro de 2026). O envio/link do arXiv está pendente; o trabalho não foi enviado. A fonte está em [`paper/main.tex`](paper/main.tex).

É um estudo independente, não um artigo de periódico. `make paper` recompila o PDF com Tectonic 0.17.0 a partir de [`paper/main.tex`](paper/main.tex), [`paper/refs.bib`](paper/refs.bib) e [`paper/figures/evaluation.py`](paper/figures/evaluation.py).
