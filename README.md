# Codex Reset Monitor

Monitor público e gratuito para detectar anúncios relevantes de reset de uso do OpenAI Codex e enviar alertas para um canal privado do Discord.

## Alertas

- 🟢 **RESET CODEX AUTOMÁTICO CONFIRMADO** — reset global/automático concluído e sustentado por evidência forte.
- 🟡 **RESET CODEX AUTOMÁTICO MUITO PROVÁVEL** — anúncio futuro forte, normalmente com escopo e/ou horário identificável.
- 🟣 **BANKED RESET CODEX — AÇÃO MANUAL** — crédito de reset que precisa ser resgatado manualmente em `Settings → Usage`.
- ⚪ Rumores, perguntas de usuários, previsões puramente estatísticas e sinais fracos são ignorados.

## Arquitetura V3

```text
Cloudflare Cron (:07, :22, :37, :52) ──→ workflow_dispatch
                    │
                    ▼
GitHub Actions ← GitHub schedule (fallback)
        ↓
┌───────────────────────────────┐
│ OpenAI Help Center            │
│ OpenAI Developer Community    │
│ codexreset.org (redundância)  │
│ Tibo via tracker público      │
└───────────────────────────────┘
        ↓
normalização + confiança por fonte
        ↓
interpretação determinística
(tipo / tempo / escopo / certeza)
        ↓
conversão de horários PT/PST/PDT → BRT
        ↓
event engine + deduplicação entre fontes
        ↓
🟡 provável → 🟢 confirmado
        ↓
Discord Webhook → #codex-alerts
```

A V3 não usa X API paga e não usa LLM/API externa. O objetivo é manter o sistema sob nosso controle e com custo recorrente esperado de **R$ 0**.

A camada Cloudflare é um relógio externo opcional e independente do scheduler do GitHub. O código já está em `cloudflare/`, mas precisa ser implantado uma única vez na conta Cloudflare do proprietário para se tornar o gatilho principal. Até essa implantação, o cron nativo do GitHub continua operando normalmente.

## Fontes e confiança

O monitor atribui níveis de confiança diferentes às fontes. OpenAI Help Center e sinais diretamente ligados à equipe OpenAI/Codex recebem maior peso. `codexreset.org` e `willcodexresets.com` funcionam como fontes secundárias independentes de redundância/corroboração e não substituem as fontes da OpenAI.

Antes da classificação, a V3 normaliza pontuação Unicode e contrações comuns. Assim, formas equivalentes como `we’ll reset`, `we'll reset` e `we will reset` chegam ao classificador com a mesma intenção. A decisão também combina ação + temporalidade + escopo, em vez de depender somente de frases exatas.

O classificador também separa explicitamente:

- reset automático concluído;
- reset automático futuro;
- banked reset;
- escopo global/compartilhado;
- linguagem especulativa ou negativa.

A classificação é determinística e possui testes de regressão.

## Event engine e deduplicação

Em vez de tratar cada URL como um alerta independente, a V3 agrupa sinais compatíveis no mesmo evento lógico. Assim, um anúncio do Tibo, uma reprodução na Community e uma confirmação no tracker podem virar um único evento.

Exemplo:

```text
RESET-20260908-XXXXXXXX
├── Tibo / tracker
├── OpenAI Community
└── codexreset.org
```

Um evento pode evoluir de 🟡 para 🟢 sem ser confundido com um reset diferente. Banked resets permanecem em uma categoria separada e nunca são promovidos para reset automático sem evidência explícita independente.

## Horários em BRT

A V3 interpreta horários concretos em PT/PST/PDT, como `6pm PST`, além de janelas relativas simples, como `within the next hour`.

Os alertas do Discord usam rótulos diferentes para evitar apresentar uma previsão como se fosse um fato confirmado:

- 🟡 eventos futuros exibem **Horário previsto do reset** e, quando aplicável, **Tempo restante**;
- 🟢 eventos concluídos exibem **Horário do reset** quando a fonte fornece um timestamp confiável;
- quando só existe um horário previamente anunciado, ele aparece como **Horário anunciado do reset**;
- quando a fonte não informa um horário utilizável, o monitor mostra explicitamente **Não informado pela fonte** em vez de estimar;
- todos os alertas exibem **Detectado pelo monitor**, permitindo distinguir o momento do evento do momento em que o watcher o encontrou.

Todos esses horários são apresentados em **BRT (America/Sao_Paulo)**.

## Monitoramento da saúde das fontes

O monitor mantém estado por fonte e atualiza `last_success` em toda execução bem-sucedida. Uma falha isolada não gera ruído. O alerta de cobertura degradada ocorre após **3 falhas consecutivas** ou quando a primeira falha persiste por aproximadamente **45 minutos**. Quando a fonte volta a responder, é enviado um aviso de recuperação.

O Worker externo também atua como watchdog do próprio GitHub Actions: se a execução principal ficar atrasada além da janela configurada, ele pode alertar diretamente no Discord e continuar tentando disparar o workflow.

Isso diferencia:

```text
nenhum reset novo ✅
```

de:

```text
uma fonte deixou de funcionar ⚠️
```

## Testes

O workflow `Tests` executa `pytest` automaticamente em pushes de branches de feature/fix e em Pull Requests para `main`.

Os testes atuais cobrem:

- reset global concluído → 🟢;
- reset futuro → 🟡;
- banked reset → 🟣;
- perguntas/ruído → ignorar;
- conversão PT/PST/PDT → BRT;
- horário efetivo do reset quando fornecido pelo ledger;
- horário previsto em alertas futuros;
- fallback `Não informado pela fonte` quando não existe horário confiável;
- horário de detecção do watcher;
- deduplicação e upgrade 🟡 → 🟢;
- parser resiliente do ledger secundário;
- segundo tracker independente;
- normalização de `we’ll` / `we'll` / `we will`;
- regressão do reset perdido de 26/09/2026;
- exemplos/documentação do Help Center não virarem falsos alertas;
- alerta de saúde por falhas consecutivas ou tempo degradado;
- sintaxe do Worker Cloudflare.

## Frequência, scheduler externo e custo

O workflow mantém o cron GitHub a cada **15 minutos**, nos minutos `:07`, `:22`, `:37` e `:52`, como fallback. A V3 inclui em `cloudflare/` um Worker que pode disparar o mesmo workflow externamente nesses horários e verificar se o GitHub ficou sem executar.

Como o repositório é público e usa runner GitHub-hosted padrão (`ubuntu-latest`), o monitor não consome a franquia mensal destinada a runners padrão de repositórios privados. O Worker foi projetado para caber no uso gratuito esperado deste monitor.

Para ativar o relógio externo, siga `cloudflare/README.md`. Os segredos necessários ficam apenas no Cloudflare: um fine-grained GitHub token limitado ao repositório e o webhook já existente do Discord.

## Heartbeat

O workflow `Repository Heartbeat` roda uma vez por semana, aos domingos às 03:17 UTC, e atualiza `state/heartbeat.txt`. Isso mantém atividade periódica no repositório e reduz o risco de workflows agendados serem desativados após longos períodos sem atividade.

## Segurança

O webhook do Discord não fica no código. Ele existe apenas como GitHub Actions Secret:

`DISCORD_WEBHOOK_URL`

Nunca publique ou faça commit da URL do webhook. Se ela vazar, revogue o webhook no Discord e crie outro.

Como o repositório é público, revise cuidadosamente qualquer Pull Request externo antes de incorporá-lo à `main`, principalmente alterações em `.github/workflows/` ou em código que tenha acesso a secrets.

## Teste manual do Discord

1. Abra **Actions**.
2. Selecione **Codex Reset Monitor**.
3. Clique em **Run workflow**.
4. Marque `Send a Discord test notification`.
5. Execute.

A V3 envia uma mensagem iniciada por `🧪 CODEX MONITOR V3 — TESTE`.

## Estado persistente

`state/alerts.json` armazena:

- eventos lógicos;
- fontes/evidências associadas;
- status já notificados;
- fingerprints para deduplicação;
- saúde das fontes.

Na primeira execução normal após a migração V1 → V3, o monitor cria uma **baseline silenciosa** para não enviar novamente eventos históricos já existentes.

## Limites atuais

O monitor acompanha anúncios públicos e não lê a porcentagem da quota da conta pessoal. A leitura direta da quota individual seria uma evolução separada, provavelmente com um componente local autenticado no dispositivo do usuário.
