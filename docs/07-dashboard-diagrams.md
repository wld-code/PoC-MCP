# Schémas fonctionnel, d'architecture et séquences du dashboard

> Généré à partir du code réel de `agent/backend/` et `frontend/src/`, et vérifié en conditions
> réelles : les 10 actions du dashboard ont été exercées par un navigateur piloté (Playwright) contre
> la stack complète (4 APIs + 4 serveurs MCP + backend), sans aucune option en échec.
>
> Une version illustrée (palette de marque, navigation, légende) est publiée en Artifact — demander le
> lien si besoin. Ce document est la version versionnée avec le code, qui reste à jour à chaque évolution.

## Sommaire

1. [Schéma fonctionnel](#schéma-fonctionnel)
2. [Schéma d'architecture](#schéma-darchitecture)
3. [Authentification](#1--authentification)
4. [Mission Control](#2--mission-control--poser-une-question)
5. [Automations](#3--automations--planifier-puis-exécuter)
6. [Deep Dive](#4--deep-dive--dérouler-un-process-flow)
7. [Insights](#5--insights--kpis-et-synthèse-exécutive)
8. [Data Sources](#6--data-sources--connecter-un-serveur-mcp)
9. [AI Models](#7--ai-models--enregistrer-un-fournisseur-llm)
10. [Process Flows](#8--process-flows--gérer-le-catalogue-deep-dive)
11. [Audit Trail](#9--audit-trail--consulter-lhistorique)
12. [Users](#10--users--gérer-les-comptes-et-les-rôles)

---

## Schéma fonctionnel

Qui peut faire quoi. Les trois rôles n'ouvrent pas des écrans différents au hasard : ils élargissent
progressivement l'accès à la même couche — un toolbox d'agent unique, fusionné à travers quatre
systèmes métier.

```mermaid
flowchart LR
  classDef role fill:#0C1A2E,color:#F7F9FC,stroke:#0C1A2E,rx:6,ry:6
  classDef cap fill:#FFFFFF,color:#0C1A2E,stroke:#1456D6,stroke-width:1px,rx:6,ry:6
  classDef core fill:#1456D6,color:#FFFFFF,stroke:#1456D6,rx:6,ry:6
  classDef sys fill:#FFFFFF,color:#0C1A2E,stroke:#8A98AD,rx:6,ry:6

  V["Viewer<br/><small>lecture</small>"]:::role
  O["Operator<br/><small>+ agir, planifier</small>"]:::role
  A["Admin<br/><small>+ gérer la plateforme</small>"]:::role

  subgraph CAP["Écrans du dashboard"]
    direction TB
    MC["Mission Control<br/>poser une question"]:::cap
    AT2["Automations<br/>planifier un run"]:::cap
    DD["Deep Dive<br/>dérouler un flow"]:::cap
    IN["Insights<br/>synthèse exécutive"]:::cap
    PF["Process Flows<br/>catalogue de flows"]:::cap
    AUD["Audit Trail<br/>historique"]:::cap
    DS["Data Sources<br/>serveurs MCP"]:::cap
    AIM["AI Models<br/>fournisseurs LLM"]:::cap
    US["Users<br/>comptes & rôles"]:::cap
  end

  TB["Toolbox agent fusionné<br/>19 outils MCP"]:::core

  subgraph SYS["Systèmes connectés"]
    direction LR
    CVC["CVC<br/>lecture"]:::sys
    ASAP["ASAP<br/>orchestration"]:::sys
    RB["Redbend<br/>exécution OTA"]:::sys
    DL["Data Lake<br/>analytique"]:::sys
  end

  V --> MC & AT2 & DD & IN & PF & AUD
  O --> MC & AT2 & DD & IN & PF & AUD
  A --> MC & AT2 & DD & IN & PF & AUD & DS & AIM & US

  MC --> TB
  AT2 --> TB
  DD --> TB
  IN --> TB

  TB --> CVC & ASAP & RB & DL
  ASAP -. "dispatch campagne" .-> RB
```

**Ce que montre le schéma —** Data Sources, AI Models et Users sont **exclusifs à l'admin** (ils
touchent des secrets ou la gestion des comptes) ; les six autres écrans sont ouverts à tous les rôles
authentifiés, avec des actions d'écriture réservées aux opérateurs+. Quatre écrans (Mission Control,
Automations, Deep Dive, Insights) passent tous par le même toolbox de 19 outils — aucun d'eux n'a sa
propre logique d'accès aux systèmes.

| Rôle | Accès |
| --- | --- |
| **Viewer** | Lecture seule sur les 6 écrans opérationnels. Ne peut ni écrire de configuration, ni gérer de comptes. |
| **Operator** | + chat, exécuter/planifier l'agent, gérer les process flows, lire l'audit trail. |
| **Admin** | + gérer les serveurs MCP, les fournisseurs LLM (et leurs clés), les comptes utilisateurs. |

---

## Schéma d'architecture

La même plateforme, vue cette fois par ses composants techniques réels : le frontend React est un
projet séparé qui ne parle au backend que par REST ; le backend est la seule pièce qui connaît
Postgres, le chiffrement et les serveurs MCP.

```mermaid
flowchart TB
  classDef fe fill:#0C1A2E,color:#F7F9FC,stroke:#0C1A2E,rx:6,ry:6
  classDef be fill:#1456D6,color:#FFFFFF,stroke:#1456D6,rx:6,ry:6
  classDef svc fill:#FFFFFF,color:#0C1A2E,stroke:#1456D6,stroke-width:1px,rx:6,ry:6
  classDef data fill:#FFFFFF,color:#0C1A2E,stroke:#8A98AD,stroke-width:1.5px,rx:6,ry:6
  classDef ext fill:#FFFFFF,color:#0C1A2E,stroke:#8A98AD,rx:6,ry:6

  Browser["Navigateur<br/>React SPA (frontend/)"]:::fe

  subgraph EDGE["nginx (prod) / Vite (dev)"]
    Proxy["reverse-proxy<br/>/api/* -> backend:8002"]:::svc
  end

  subgraph BACKEND["Backend API — agent/backend/"]
    direction TB
    Routers["Routers FastAPI<br/>auth · chat · schedules · mcp · llms · flows · users · audit"]:::be
    Security["security.py<br/>JWT + RBAC (require_role)"]:::svc
    Runner["services/agent_runner.py<br/>boucle outil <-> LLM"]:::svc
    SchedSvc["services/scheduler.py<br/>APScheduler"]:::svc
    Crypto["crypto.py<br/>Fernet (clés LLM)"]:::svc
    MCPMgr["core/mcp_manager.py<br/>DynamicMCPManager"]:::svc
  end

  PG[("PostgreSQL<br/>users · schedules · run_history<br/>llm_configs · audit_log")]:::data

  subgraph MCP["Serveurs MCP"]
    direction LR
    M1["cvc-mcp"]:::ext
    M2["asap-mcp"]:::ext
    M3["redbend-mcp"]:::ext
    M4["datalake-mcp"]:::ext
  end
  subgraph APIS["APIs métier (in-memory)"]
    direction LR
    A1["cvc-api"]:::ext
    A2["asap-api"]:::ext
    A3["redbend-api"]:::ext
    A4["datalake-api"]:::ext
  end

  Browser -- "fetch, JWT bearer<br/>+ cookie refresh httpOnly" --> Proxy --> Routers
  Routers --> Security
  Routers --> Runner
  Routers --> Crypto
  Routers -- "SQLAlchemy async" --> PG
  SchedSvc -- "jobstore persistant" --> PG
  SchedSvc -. "déclenche à l'heure prévue" .-> Runner
  Runner --> MCPMgr
  MCPMgr --> M1 & M2 & M3 & M4
  M1 --> A1
  M2 --> A2
  M3 --> A3
  M4 --> A4
  A2 -. "dispatch campagne" .-> A3
```

**Découplage —** le frontend ne contient *aucune* logique métier : il appelle des routes REST et
affiche le résultat. Le backend est le seul composant qui parle à Postgres, déchiffre une clé LLM, ou
ouvre une session MCP. Le scheduler tourne dans le *même* process que l'API (une seule boucle asyncio)
mais persiste ses jobs dans Postgres, donc un redémarrage du backend ne perd aucune automatisation.

| Composant | Port | Rôle |
| --- | --- | --- |
| `frontend` | 3000 → 80 | SPA React, servie par nginx |
| `backend` | 8002 | API FastAPI (auth, RBAC, scheduler) |
| `postgres` | 5432 | État persisté (users, schedules, run_history, audit_log…) |
| `*-mcp` | 8011–8014 | Adaptateurs MCP (Streamable HTTP) |
| `*-api` | 8021–8024 | APIs métier simulées (CVC/ASAP/Redbend/Data Lake) |

---

## 1 — Authentification

Chaque autre séquence de ce document suppose qu'un token d'accès existe déjà en mémoire côté navigateur — voici comment il y arrive.

**Rôles :** Public (nécessite un compte existant)

```mermaid
sequenceDiagram
  participant B as Navigateur (SPA)
  participant R as auth.py (router)
  participant S as security.py
  participant DB as Postgres (users)

  B->>R: POST /api/auth/login<br/>(email, password)
  activate R
  R->>DB: SELECT user WHERE email=...
  DB-->>R: user | none
  R->>S: verify_password(password, hash)
  S-->>R: bool
  alt identifiants invalides
    R-->>B: 401 Unauthorized
  else identifiants valides
    R->>S: create_access_token(user)<br/>create_refresh_token(user)
    S-->>R: access_token, refresh_token
    R->>DB: INSERT audit_log("login")
    R-->>B: 200 + access_token (JSON)<br/>Set-Cookie: refresh_token (httpOnly, Lax)
    Note over B: access_token gardé en mémoire<br/>(jamais localStorage)
  end
  deactivate R

  Note over B,R: Plus tard — un appel API renvoie 401
  B->>R: POST /api/auth/refresh<br/>(cookie envoyé automatiquement)
  R->>S: decode_token(cookie, "refresh")
  S-->>R: user_id
  R-->>B: 200 + nouveau access_token<br/>+ cookie de refresh régénéré (rotation)
```

**Ce qui compte —** le mot de passe est comparé via Argon2id, jamais en clair ; le refresh token n'est *jamais* lisible en JavaScript (cookie httpOnly) ; l'access token vit 15 minutes et n'existe qu'en mémoire côté navigateur — un rechargement de page force un `/refresh` silencieux.

---

## 2 — Mission Control — poser une question

Le chat interactif. Une session garde son historique de conversation tant que le même LLM/modèle reste sélectionné.

**Rôles :** Viewer (non), Operator, Admin

```mermaid
sequenceDiagram
  participant B as Navigateur
  participant R as chat.py
  participant Run as agent_runner.chat_turn
  participant LLM as Fournisseur LLM
  participant M as DynamicMCPManager

  B->>R: POST /api/chat<br/>(session_id, message, provider)
  activate R
  R->>R: require_role("operator")
  R->>Run: chat_turn(mcp, session_id, cfg, message)
  activate Run
  Run->>M: list_tools()
  M-->>Run: 19 outils (schéma JSON)
  Run->>LLM: send(message, tools, call_tool)
  activate LLM
  loop tant que le modèle demande un outil
    LLM->>Run: call_tool(nom, arguments)
    Run->>M: call_tool(nom, arguments)
    M->>M: route vers le serveur propriétaire
    M-->>Run: résultat de l'outil
    Run-->>LLM: résultat réinjecté
  end
  LLM-->>Run: réponse finale (texte)
  deactivate LLM
  Run-->>R: answer, tool_calls, error
  deactivate Run
  R-->>B: 200 ChatOut (answer + evidence)
  deactivate R
```

**Ce qui compte —** le `session_id` envoyé par le navigateur est préfixé côté serveur par l'id de l'utilisateur (`f"{user.id}:{session_id}"`) — un utilisateur ne peut pas deviner l'id d'un autre pour lire sa conversation. Le panneau « Evidence » de l'écran affiche exactement `tool_calls`, rien de plus.

---

## 3 — Automations — planifier, puis exécuter

Deux moments distincts : la création (immédiate, à l'écran) et le déclenchement (asynchrone, potentiellement des jours plus tard — survit à un redémarrage du backend).

**Rôles :** Viewer (lecture), Operator, Admin

```mermaid
sequenceDiagram
  participant B as Navigateur
  participant R as schedules.py
  participant DB as Postgres
  participant AP as APScheduler
  participant Run as agent_runner

  rect rgb(238,242,248)
  Note over B,AP: Création (synchrone)
  B->>R: POST /api/schedules<br/>(question, cron ou interval)
  activate R
  R->>R: require_role("operator")
  R->>DB: INSERT schedules
  R->>AP: add_job(run_scheduled_agent,<br/>trigger=Cron|Interval, id=schedule.id)
  AP->>DB: persiste le job (jobstore SQL)
  R->>DB: INSERT audit_log("schedule.create")
  R-->>B: 201 ScheduleOut
  deactivate R
  end

  rect rgb(238,242,248)
  Note over AP,Run: Déclenchement (async, à l'heure dite)
  AP->>Run: run_scheduled_agent(schedule_id)
  activate Run
  Run->>DB: relit Schedule + LlmConfig (jamais de cache)
  Run->>Run: run_once(...) — même chemin que le chat
  Run->>DB: INSERT run_history
  Run->>DB: INSERT audit_log("schedule.fire")
  deactivate Run
  end
```

**Ce qui compte —** le job APScheduler est stocké dans Postgres, pas en mémoire process : un redémarrage du backend le recharge automatiquement. Chaque déclenchement relit la config depuis la base (pas de closure figée), donc éditer le LLM par défaut change le comportement du prochain run sans recréer le schedule.

---

## 4 — Deep Dive — dérouler un process flow

Aucun LLM ici : chaque étape appelle un outil MCP directement, et capture une valeur (VIN, id de campagne) réutilisée par l'étape suivante.

**Rôles :** Viewer, Operator, Admin

```mermaid
sequenceDiagram
  participant B as Navigateur
  participant F as flows.py
  participant T as system.py (/api/tool)
  participant M as DynamicMCPManager

  B->>F: GET /api/flows
  F-->>B: catalogue (Bootstrap, Pairing, Activation…)
  Note over B: l'utilisateur choisit "Bootstrap"<br/>et saisit un VIN

  loop pour chaque étape du flow
    B->>B: remplace {vin}/{campaign}<br/>par les valeurs déjà capturées
    B->>T: POST /api/tool (name, arguments)
    activate T
    T->>M: call_tool(name, arguments)
    M-->>T: résultat brut de l'outil
    T-->>B: { result, ok }
    deactivate T
    B->>B: extrait une nouvelle capture<br/>(ex: "vin") du résultat si prévu
  end
  Note over B: tableau des résultats affiché,<br/>étape par étape
```

**Ce qui compte —** une étape sans sa capture requise (ex. pas de dérive trouvée → pas de `campaign`) est marquée *skipped* plutôt que d'échouer — c'est le `skip_note` défini dans le flow lui-même qui l'explique.

---

## 5 — Insights — KPIs et synthèse exécutive

Deux actions bien différentes sur le même écran : charger des chiffres bruts (aucun LLM) et générer une synthèse en langage naturel (via l'agent).

**Rôles :** Viewer, Operator, Admin

```mermaid
sequenceDiagram
  participant B as Navigateur
  participant T as system.py (/api/tool)
  participant AR as agent_runs.py
  participant Run as agent_runner
  participant DL as datalake-mcp

  Note over B,DL: "Load fleet insights" — sans LLM
  par 3 appels directs
    B->>T: POST /api/tool(service_usage)
    T->>DL: call_tool
    DL-->>T: résultat
    T-->>B: usage 30j
  and
    B->>T: POST /api/tool(top_applications)
    T-->>B: top apps + croissance
  and
    B->>T: POST /api/tool(anomalies)
    T-->>B: anomalies signalées
  end

  Note over B,Run: "Generate executive brief" — via l'agent
  B->>AR: POST /api/agents/run<br/>(question libre)
  AR->>AR: require_role("operator")
  AR->>Run: run_once(...)
  Run-->>AR: answer, tool_calls
  AR-->>B: synthèse en langage naturel<br/>+ preuve des outils utilisés
```

**Ce qui compte —** les KPIs bruts ne coûtent ni tour de LLM ni délai — ils passent par le même endpoint générique que Deep Dive. Seule la synthèse exécutive engage un modèle, et est donc aussi enregistrée dans `run_history`.

---

## 6 — Data Sources — connecter un serveur MCP

Réservé à l'admin : une URL de serveur MCP est une cible réseau sortante — l'ouvrir à tout rôle serait une surface SSRF.

**Rôles :** Admin uniquement

```mermaid
sequenceDiagram
  participant B as Navigateur (admin)
  participant R as mcp_servers.py
  participant M as DynamicMCPManager
  participant Srv as Nouveau serveur MCP
  participant DB as Postgres

  B->>R: POST /api/mcp/servers (url, id)
  activate R
  R->>R: require_role("admin")
  R->>M: add(url, slug)
  activate M
  M->>Srv: probe TCP (3s timeout)
  alt hôte injoignable
    M-->>R: view(status="error")
  else joignable
    M->>Srv: ouvre une session MCP
    Srv-->>M: list_tools()
    M-->>R: view(status="connected", tools=[...])
  end
  deactivate M
  R->>DB: INSERT mcp_servers (slug, url)
  R->>DB: INSERT audit_log("mcp_server.create")
  R-->>B: 200 (vue live du serveur)
  deactivate R
```

**Ce qui compte —** un serveur qui répond en erreur n'est pas rejeté : il est *enregistré avec `status="error"`*, visible dans la liste, pour diagnostiquer sans perdre la configuration. Les outils du nouveau serveur rejoignent le toolbox fusionné immédiatement, sans redémarrer le backend.

---

## 7 — AI Models — enregistrer un fournisseur LLM

La lecture (nom, type, modèle) est ouverte à tous ; seule l'écriture — qui touche une clé API — est réservée à l'admin.

**Rôles :** Viewer/Operator (lecture), Admin (écriture)

```mermaid
sequenceDiagram
  participant B as Navigateur (admin)
  participant R as llms.py
  participant C as crypto.py (Fernet)
  participant DB as Postgres
  participant Sess as agent_runner (sessions chat)

  B->>R: POST /api/llms<br/>(name, kind, api_key)
  activate R
  R->>R: require_role("admin")
  R->>C: encrypt_secret(api_key)
  C-->>R: texte chiffré
  R->>DB: INSERT llm_configs<br/>(encrypted_api_key, jamais en clair)
  R->>DB: INSERT audit_log("llm.create")
  R-->>B: 201 LlmOut { has_key: true }<br/>(la clé n'est jamais renvoyée)
  deactivate R

  Note over B,Sess: Modifier ou supprimer un LLM utilisé en chat
  B->>R: PUT /api/llms/{id}  ou  DELETE
  R->>Sess: invalidate_sessions_for(slug)
  Note over Sess: les conversations en cours sur ce LLM<br/>repartiront avec la config à jour
```

**Ce qui compte —** la clé API ne traverse le réseau qu'à la création/mise à jour ; toute lecture ultérieure (même par un admin) ne renvoie que `has_key: true/false`. Supprimer le dernier LLM restant est bloqué (l'agent doit toujours avoir un fournisseur par défaut).

---

## 8 — Process Flows — gérer le catalogue Deep Dive

CRUD classique sur des définitions déclaratives, plus une action de réinitialisation qui restaure les 5 flows d'origine.

**Rôles :** Viewer (lecture), Operator, Admin (écriture)

```mermaid
sequenceDiagram
  participant B as Navigateur
  participant R as flows.py
  participant DB as Postgres

  B->>R: POST /api/flows (name, inputs, steps)
  activate R
  R->>R: require_role("operator")
  R->>R: slugify(name) -> id unique
  R->>DB: INSERT process_flows
  R->>DB: INSERT audit_log("flow.create")
  R-->>B: 201 FlowOut
  deactivate R

  Note over B,DB: Réinitialisation
  B->>R: POST /api/flows/reset
  R->>DB: DELETE FROM process_flows
  R->>DB: INSERT les 5 flows d'origine<br/>(Bootstrap, Pairing, Activation, FOTA, Analytics)
  R-->>B: 200 { flows: [...5] }
```

**Ce qui compte —** les flows créés par un opérateur et les 5 flows d'origine (`is_builtin=true`) vivent dans la même table — le reset écrase tout, y compris les flows personnalisés, ce que l'écran signale avant de confirmer.

---

## 9 — Audit Trail — consulter l'historique

Le seul écran purement passif : rien n'y est jamais écrit directement — il agrège ce que les neuf autres ont déjà produit.

**Rôles :** Viewer, Operator, Admin

```mermaid
sequenceDiagram
  participant B as Navigateur
  participant R as audit.py
  participant DB as Postgres

  B->>R: GET /api/audit
  activate R
  R->>R: require_role("operator")
  R->>DB: SELECT audit_log<br/>LEFT JOIN users (email)<br/>ORDER BY created_at DESC LIMIT 100
  DB-->>R: lignes (action, cible, qui, quand)
  R-->>B: 200 [ {action:"login",...},<br/>{action:"schedule.fire",...}, ... ]
  deactivate R

  Note over B: idem pour /api/agents/runs —<br/>l'historique des réponses de l'agent
```

**Ce qui compte —** chacune des neuf autres séquences de ce document écrit au moins une ligne ici (`login`, `schedule.create`, `llm.delete`, `mcp_server.create`…) — cet écran est la trace de tout ce que ce document décrit ailleurs.

---

## 10 — Users — gérer les comptes et les rôles

Réservé à l'admin, avec deux garde-fous explicites pour éviter qu'un admin ne se verrouille lui-même hors de son propre compte.

**Rôles :** Admin uniquement

```mermaid
sequenceDiagram
  participant B as Navigateur (admin)
  participant R as users.py
  participant S as security.py
  participant DB as Postgres

  B->>R: POST /api/users (email, password, role)
  activate R
  R->>R: require_role("admin")
  R->>S: hash_password(password) — Argon2id
  R->>DB: INSERT users
  R->>DB: INSERT audit_log("user.create")
  R-->>B: 201 UserOut
  deactivate R

  Note over B,DB: Changer un rôle ou désactiver
  B->>R: PUT /api/users/{id} (role | is_active)
  activate R
  alt cible == l'admin courant ET action = auto-rétrograder / auto-désactiver
    R-->>B: 400 — "you cannot demote/deactivate your own account"
  else cible différente ou action neutre
    R->>DB: UPDATE users
    R-->>B: 200 UserOut
  end
  deactivate R

  Note over B,DB: Suppression — même garde-fou sur soi-même
  B->>R: DELETE /api/users/{id}
  R-->>B: 400 si id == admin courant, sinon 200
```

**Ce qui compte —** les deux garde-fous (auto-rétrogradation et auto-suppression) empêchent qu'un unique admin ne se retrouve accidentellement sans accès admin — un risque réel dès qu'il n'y a qu'un seul compte de ce rôle.

---
