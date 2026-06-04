#  OmniRoute AI

> *One question, every channel, the right expert.*

A multi-agent orchestration system that **answers questions**, **publishes to sector-specific forums**, and **finds internal experts via Microsoft Teams** — all automatically routed by topic and language.

---

##  Architecture

```
┌────────────┐
│    User     │
└─────┬──────┘
      ▼
┌─────────────────────────────────────────────────────────────────┐
│                       ORCHESTRATOR                              │
│                                                                 │
│  ┌────────────────┐                                             │
│  │ Classifier Agent│──▶ Sector + Language                       │
│  └──────┬─────────┘                                             │
│         ▼                                                       │
│  ┌───────────────────┐                                          │
│  │ Answer Agent       │  Legal / Medical / Tech / Finance       │
│  └──────┬────────────┘                                          │
│         ▼                                                       │
│  ┌────────────────┐       ┌──────────────────────────────────┐  │
│  │ Forum Publisher │       │ Teams Expert Agent               │  │
│  │                 │       │                                  │  │
│  │ Reddit          │       │ 1. Graph API: search by skill    │  │
│  │ Quora           │       │ 2. Teams 1:1: ask top expert     │  │
│  │ Avvo / King     │       │ 3. Teams channel: broadcast      │  │
│  │ StackOverflow   │       │                                  │  │
│  │ Medicitalia     │       │                                  │  │
│  └────────────────┘       └──────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────┘
```

---

##  Features

| Feature | Description |
|---|---|
| **Multi-sector routing** | Automatically detects Legal, Medical, Tech, Finance, or General from keywords |
| **Bilingual support** | Detects English vs Italian and routes to language-appropriate forums |
| **Sector-specific AI answers** | Each sector has a dedicated agent with a specialized system prompt |
| **Forum auto-publishing** | Posts to Reddit, Quora, Avvo, King, Medicitalia, StackOverflow, HealthTap |
| **Microsoft Teams integration** | Searches Azure AD for experts by skill, sends 1:1 messages and channel posts |
| **Simulation mode** | Runs fully offline with fake data when Azure credentials are not configured |
| **Extensible** | Add a new sector in 5 steps — enum, keywords, skills, forums, agent |

---

##  Forum Routing Table

| Sector | Language | Forums |
|---|---|---|
| **Legal** | 🇬🇧 EN | Reddit r/legaladvice · Quora Law · **Avvo** (Common Law) |
| **Legal** | 🇮🇹 IT | Reddit r/italy · Quora Diritto · **King** (Italian Law) |
| **Medical** | 🇬🇧 EN | Reddit r/AskDocs · Quora Medicine · **HealthTap** |
| **Medical** | 🇮🇹 IT | Reddit r/italy · Quora Medicina · **Medicitalia** |
| **Tech** | 🇬🇧 EN | Reddit r/learnprogramming · **StackOverflow** |
| **Tech** | 🇮🇹 IT | Reddit r/ItalyInformatica |
| **Finance** | 🇬🇧 EN | Reddit r/personalfinance |
| **Finance** | 🇮🇹 IT | Reddit r/ItaliaPersonalFinance |
| **General** | Any | Reddit r/AskReddit · Quora General |

---

##  Teams Expert Agent — How It Works

```
User Question
     │
     ▼
┌──────────────────────┐
│  Sector → Skill Map  │
│  Legal → "GDPR",     │
│    "Contract Law"    │
│  Medical → "Medicine",│
│    "Healthcare"      │
│  Tech → "Python",    │
│    "Cloud Arch"      │
└──────┬───────────────┘
       ▼
┌──────────────────────┐
│  Microsoft Graph API │
│  POST /search/query  │
│  entityTypes: person │
│  query: skill keywords│
└──────┬───────────────┘
       ▼
┌──────────────────────┐     ┌──────────────────────┐
│  1:1 Teams Chat      │     │  Sector Channel Post │
│  → Top expert        │     │  → #legal-team       │
│  "Can you confirm    │     │  "Can anyone help    │
│   this answer?"      │     │   validate this?"    │
└──────────────────────┘     └──────────────────────┘
```

### Microsoft Graph Permissions Required

| Permission | Type | Purpose |
|---|---|---|
| `People.Read.All` | Application | Search people by skill |
| `User.Read.All` | Application | Read user profiles |
| `Chat.Create` | Application | Create 1:1 chats |
| `ChatMessage.Send` | Application | Send messages in chats |
| `ChannelMessage.Send` | Application | Post to Teams channels |

---

##  Quick Start

### 1. Clone & Install

```bash
git clone https://github.com/AvanadeMike33/omniroute-ai.git
cd omniroute-ai
pip install -r requirements.txt
```

### 2. Configure Environment

```bash
cp .env.example .env
# Edit .env with your credentials
```

```ini
# Azure AD App Registration
AZURE_TENANT_ID=your-tenant-id
AZURE_CLIENT_ID=your-app-client-id
AZURE_CLIENT_SECRET=your-app-secret

# OpenAI (for LLM-powered answers)
OPENAI_API_KEY=your-openai-key

# Teams Channel IDs (optional)
TEAMS_LEGAL_TEAM_ID=...
TEAMS_LEGAL_CHANNEL_ID=...
TEAMS_MEDICAL_TEAM_ID=...
TEAMS_MEDICAL_CHANNEL_ID=...
TEAMS_TECH_TEAM_ID=...
TEAMS_TECH_CHANNEL_ID=...
TEAMS_FINANCE_TEAM_ID=...
TEAMS_FINANCE_CHANNEL_ID=...
```

### 3. Run

```bash
python multi_agent_forum_teams.py
```

### 4. Run in Simulation Mode (no credentials needed)

Simply don't set `AZURE_TENANT_ID` — the system automatically uses simulated experts and prints actions to console.

---

##  Project Structure

```
omniroute-ai/
├── multi_agent_forum_teams.py   # Main multi-agent system
├── requirements.txt             # Python dependencies
├── .env.example                 # Environment template
├── docs/
│   ├── architecture.mmd         # Mermaid: component diagram
│   ├── sequence.mmd             # Mermaid: request flow
│   └── routing.mmd              # Mermaid: sector state machine
└── README.md
```

---

##  Test Cases

The built-in test suite covers all sector/language combinations:

| # | Question | Sector | Lang | Forums | Teams |
|---|---|---|---|---|---|
| 1 | "Can I break a lease if landlord won't fix plumbing?" | Legal | EN | r/legaladvice, Avvo | Legal Counsel |
| 2 | "Posso recedere dal contratto se il proprietario non ripara?" | Legal | IT | r/italy, King | Legal Counsel |
| 3 | "Persistent headaches for 3 days. Should I worry?" | Medical | EN | r/AskDocs, HealthTap | CMO |
| 4 | "Ho mal di testa da 3 giorni e febbre." | Medical | IT | Medicitalia | CMO |
| 5 | "How do I fix a segfault with pointers in C?" | Tech | EN | r/learnprogramming, SO | Principal Eng |
| 6 | "How should I rebalance my investment portfolio?" | Finance | EN | r/personalfinance | CFO |

---

##  Adding a New Sector

Example: adding **Architecture**

```python
# 1. Add to Sector enum
class Sector(Enum):
    ARCHITECTURE = "architecture"

# 2. Add keywords
SECTOR_KEYWORDS[Sector.ARCHITECTURE] = [
    "building", "architect", "structural", "blueprint", "zoning"
]

# 3. Add Graph skills
SECTOR_TO_SKILLS[Sector.ARCHITECTURE] = [
    "Architecture", "Urban Planning", "Structural Engineering"
]

# 4. Add forum routes
FORUM_ROUTING[(Sector.ARCHITECTURE, Language.EN)] = [
    {"platform": "reddit", "subreddit": "architecture"},
    {"platform": "archinect", "section": "forum"},
]

# 5. Create agent
class ArchitectureAgent(BaseAnswerAgent):
    sector = Sector.ARCHITECTURE
    system_prompt = "You are a licensed architect..."

# 6. Register in Orchestrator.__init__
self.answer_agents[Sector.ARCHITECTURE] = ArchitectureAgent()
```

---

##  Requirements

```
python >= 3.10
requests
python-dotenv
pydantic-ai      # optional, for LLM answers
openai            # optional, for LLM answers
praw              # optional, for Reddit publishing
```

---

##  License

MIT

---

##  Contributing

1. Fork the repo
2. Create a feature branch (`git checkout -b feature/new-sector`)
3. Commit your changes
4. Push and open a Pull Request

---

<p align="center">
  Built with ❤️ by <a href="https://github.com/AvanadeMike33">AvanadeMike33</a>
</p>
