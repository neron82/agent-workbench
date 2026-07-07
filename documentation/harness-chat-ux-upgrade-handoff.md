# Harness + Chat UX Upgrade Handoff

Datum: 2026-07-05
Repo: /home/neron/projects/agent-workbench
Status: Analyse delegiert, Implementierung absichtlich noch NICHT begonnen

## Ziel
Die nächsten zwei Ausbaustufen der Agent Workbench umsetzen:

1. Echte Harness-Agenten aus der UI startbar machen
   - Hermes
   - OpenCode
   - Shell
   - SSH

2. Chat-UX deutlich verbessern
   - kompakte Teilnehmerchips
   - Nachrichtenblasen statt Routing-Metadaten-Look
   - dynamische Updates per SSE/Event-Feed oder klar begründetem Fallback

## Wichtige Benutzer-Vorgaben
- Subagenten zuerst Ergebnisse liefern lassen, nicht parallel dieselbe Arbeit im Parent doppelt anfangen.
- Wegen knapper Kontextreserve soll die eigentliche Implementierung möglichst ebenfalls an Subagenten delegiert werden.
- Kein unnötiger Architektur-Neubau; additiver MVP auf Basis der bestehenden Flask/SQLite/Jinja-Struktur.

## Bereits gesichtete Ist-Bausteine
- Web:
  - src/agent_workbench/web/runs.py
  - src/agent_workbench/web/task_specs.py
  - src/agent_workbench/web/sessions.py
  - src/agent_workbench/web/channels.py
  - src/agent_workbench/web/messages.py
- Adapter:
  - src/agent_workbench/adapters/hermes_adapter.py
  - src/agent_workbench/adapters/opencode.py
  - src/agent_workbench/adapters/shell.py
  - src/agent_workbench/adapters/ssh.py
- Modelle:
  - src/agent_workbench/models/harness_run.py
  - src/agent_workbench/models/task_spec.py
- Chat-UX aktuell:
  - src/agent_workbench/web/templates/session_view.html
  - src/agent_workbench/web/templates/message_row.html
  - src/agent_workbench/web/templates/message_list.html
  - src/agent_workbench/web/static/chat-poll.js

## Laufende Read-only Delegationen
Delegation-ID: deleg_00998658

Drei parallele Analyseaufträge laufen/werden erwartet:
1. Harness-Agenten aus Session-UI startbar machen
2. Konkrete Chat-UX-Umsetzung (SSE vs Polling, Bubbles, Chips)
3. Ehrliche Adapter-Reifegradanalyse + minimaler sicherer Scope

## Geplanter Implementierungsmodus nach Eingang der Analysen
NICHT alles im Parent umsetzen.
Stattdessen Implementierung in getrennte Subagent-Aufträge zerlegen, z. B.:

### Lane A — Harness UI + Run Start
- Start-Flow aus Session/Work-UI
- TaskSpec-/Run-Verknüpfung
- Run-Start-Endpunkte
- Run-Listen/Run-Detail-Verlinkung
- Tests für UI-Startpfad

### Lane B — Adapter-/Runtime-Ergänzungen
- minimal nötige Ergänzungen an Hermes/OpenCode/Shell/SSH
- ehrliche Capabilities / Fehlerpfade
- mindestens ein echter verifizierbarer Run-Pfad
- Adapter-Tests / Live-Smoke-Checks

### Lane C — Chat-UX
- Teilnehmerchips
- Message bubbles
- SSE/Event-Feed + JS-Fallback
- Session-/Message-Templates + Web-Tests

Parent-Agent-Aufgabe danach:
- Subagent-Diffs prüfen
- gezielte Korrekturen
- Gesamttests laufen lassen
- Live-HTTP verifizieren
- nur verifizierte Ergebnisse berichten

## Nicht vergessen
- Keine bloße Mock-Behauptung, dass Harness-Runs “echt” seien.
- Mindestens ein realer UI-gestarteter Run muss verifiziert werden.
- Bei Chat-Transport klare Entscheidung zwischen SSE und Polling treffen; kein halbes Zombie-Doppelmodell ohne Grund.
- Kein Secret im Chat oder in SQLite speichern.

## Erwartete Verifikation am Ende
- pytest gesamt
- gezielte UI-/Adapter-/Run-Tests
- Live-HTTP-Check für Session-Ansicht und Run-Ansicht
- mindestens ein echter gestarteter Harness-Run mit Evidenz
