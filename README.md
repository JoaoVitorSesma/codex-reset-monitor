# Codex Reset Monitor

Monitor público para detectar anúncios relevantes de reset de uso do OpenAI Codex e enviar alertas para um canal privado do Discord.

## Alertas

- 🟢 **RESET CODEX AUTOMÁTICO CONFIRMADO** — reset global/automático explicitamente confirmado por fonte oficial.
- 🟡 **RESET CODEX AUTOMÁTICO MUITO PROVÁVEL** — sinal forte e recente da equipe OpenAI/Codex, mas ainda sem confirmação explícita.
- 🟣 **BANKED RESET CODEX — AÇÃO MANUAL** — reset armazenado que precisa ser resgatado manualmente em `Settings → Usage`.
- ⚪ Sinais fracos, rumores, perguntas de usuários e previsões puramente estatísticas não geram alerta.

## Arquitetura

```text
GitHub Actions (a cada 15 min)
        ↓
OpenAI Help Center + OpenAI Developer Community
        ↓
Filtro de recência + confiabilidade + regras semânticas
        ↓
Classificação 🟢 / 🟡 / 🟣 / ignorar
        ↓
Deduplicação em state/alerts.json
        ↓
Discord Webhook → #codex-alerts
```

A frequência padrão é de **15 minutos**, executando nos minutos `:07`, `:22`, `:37` e `:52`. Como o repositório é público e usa runner GitHub-hosted padrão (`ubuntu-latest`), essas execuções não consomem a franquia mensal de minutos de Actions destinada a repositórios privados.

## Heartbeat

O workflow `Repository Heartbeat` roda uma vez por semana, aos domingos às 03:17 UTC, e atualiza `state/heartbeat.txt`. O objetivo é manter atividade periódica no repositório e reduzir o risco de o GitHub desabilitar workflows agendados após longos períodos sem atividade.

## Segurança

O webhook do Discord **não fica no código**. Ele deve existir apenas como GitHub Actions Secret:

`DISCORD_WEBHOOK_URL`

Nunca publique ou faça commit da URL do webhook. Se ela vazar, revogue o webhook no Discord e crie outro.

Como o repositório é público, revise cuidadosamente qualquer Pull Request ou alteração externa antes de incorporá-la à `main`, especialmente mudanças em `.github/workflows/` ou em código que use secrets.

## Teste manual

1. Abra a aba **Actions** do repositório.
2. Selecione **Codex Reset Monitor**.
3. Clique em **Run workflow**.
4. Marque `Send a Discord test notification`.
5. Execute.

O Discord deverá receber uma mensagem iniciada por `🧪 CODEX MONITOR — TESTE`.

## Fontes e confiança

O monitor prioriza:

1. OpenAI Help Center.
2. Publicações recentes no OpenAI Developer Community, com confiança maior quando o autor ou o conteúdo está diretamente ligado à equipe OpenAI/Codex.
3. Evidências secundárias somente como contexto/corroboração — nunca como base única para um reset automático confirmado.

O monitor não tenta ler a porcentagem individual da sua conta Codex. Ele acompanha **anúncios públicos**, não o seu painel `Settings → Usage`.

## Estado e deduplicação

`state/alerts.json` guarda apenas identificadores de alertas já enviados. O workflow só cria commit quando esse estado muda, evitando commits a cada execução.

Um alerta 🟡 pode posteriormente gerar um novo 🟢 quando houver confirmação. Um banked reset nunca é convertido em reset automático sem evidência separada e explícita.
