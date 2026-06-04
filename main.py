import re
import os
import time
import json
import requests
from datetime import datetime
from typing import Dict, List, Optional
from dataclasses import dataclass, field
from enum import Enum
from dotenv import load_dotenv

load_dotenv()

# -------------------------
# MODELS & CLASSIFICATION
# -------------------------

class Sector(Enum):
    LEGAL = "legal"
    MEDICAL = "medical"
    TECH = "tech"
    FINANCE = "finance"
    GENERAL = "general"

class Language(Enum):
    EN = "en"
    IT = "it"

@dataclass
class Request:
    text: str
    sector: Sector = Sector.GENERAL
    language: Language = Language.EN
    date: str = ""
    answer: str = ""
    forum_posts: List[Dict] = field(default_factory=list)
    teams_results: List[Dict] = field(default_factory=list)


# -------------------
# CLASSIFIER AGENT
# -------------------

SECTOR_KEYWORDS = {
    Sector.LEGAL:   ["law", "legal", "court", "attorney", "lawyer", "contract",
                     "legge", "avvocato", "tribunale", "contratto", "diritto",
                     "gdpr", "normativa", "reato", "codice civile"],
    Sector.MEDICAL: ["doctor", "medical", "health", "symptom", "diagnosis",
                     "medico", "salute", "sintomo", "diagnosi", "farmaco"],
    Sector.TECH:    ["code", "programming", "software", "bug", "api", "deploy",
                     "codice", "programmazione", "server", "database"],
    Sector.FINANCE: ["tax", "investment", "stock", "budget", "accounting",
                     "tasse", "investimento", "bilancio", "contabilità"],
}

IT_MARKERS = [
    "come", "cosa", "perché", "quando", "dove", "quale", "chi",
    "posso", "devo", "vorrei", "buongiorno", "salve", "grazie",
    "è", "sono", "può", "della", "nella", "sulla",
]

# Sector → Microsoft Graph skills for people search
SECTOR_TO_SKILLS: Dict[Sector, List[str]] = {
    Sector.LEGAL:   ["Legal", "Contract Law", "GDPR", "Compliance", "Corporate Law"],
    Sector.MEDICAL: ["Medicine", "Clinical Research", "Healthcare", "Pharmacology"],
    Sector.TECH:    ["Software Engineering", "Python", "Cloud Architecture", "DevOps"],
    Sector.FINANCE: ["Financial Analysis", "Accounting", "Tax Planning", "Auditing"],
    Sector.GENERAL: [],
}


class ClassifierAgent:
    """Detects language and sector from the user's question."""

    def detect_language(self, text: str) -> Language:
        lower = text.lower()
        score = sum(1 for m in IT_MARKERS if f" {m} " in f" {lower} ")
        return Language.IT if score >= 2 else Language.EN

    def detect_sector(self, text: str) -> Sector:
        lower = text.lower()
        scores = {s: sum(1 for kw in kws if kw in lower) for s, kws in SECTOR_KEYWORDS.items()}
        best = max(scores, key=scores.get)
        return best if scores[best] > 0 else Sector.GENERAL

    def classify(self, text: str) -> Request:
        return Request(
            text=text,
            sector=self.detect_sector(text),
            language=self.detect_language(text),
            date=datetime.now().strftime("%Y-%m-%d"),
        )


# ---------------------------------
# ANSWER AGENTS ----------------
# ---------------------------------

class BaseAnswerAgent:
    """Base class — in production, call an LLM here."""
    sector: Sector = Sector.GENERAL
    system_prompt: str = "You are a helpful assistant."

    def answer(self, req: Request) -> str:
        # ── Production: call OpenAI / pydantic_ai ──
        # model = OpenAIModel("gpt-4o-mini")
        # agent = Agent(model, system_prompt=self.system_prompt)
        # result = agent.run_sync(req.text)
        # return result.data
        return f"[{self.sector.value.upper()} AGENT] Answer for: {req.text[:80]}..."


class LegalAgent(BaseAnswerAgent):
    sector = Sector.LEGAL
    system_prompt = (
        "You are a legal expert. "
        "If the question is in English, answer based on Common Law principles. "
        "If in Italian, answer based on Italian and EU law (Codice Civile, GDPR, etc.)."
    )

class MedicalAgent(BaseAnswerAgent):
    sector = Sector.MEDICAL
    system_prompt = (
        "You are a medical information assistant. "
        "Provide general health information. Always recommend consulting a doctor."
    )

class TechAgent(BaseAnswerAgent):
    sector = Sector.TECH
    system_prompt = "You are a senior software engineer. Give clear, practical answers."

class FinanceAgent(BaseAnswerAgent):
    sector = Sector.FINANCE
    system_prompt = "You are a financial advisor. Provide accurate financial guidance."

class GeneralAgent(BaseAnswerAgent):
    sector = Sector.GENERAL
    system_prompt = "You are a helpful general-purpose assistant."


# ------------------------
# FORUM PUBLISHER AGENT
# ------------------------

# Routing table: (sector, language) → list of target forums
FORUM_ROUTING: Dict[tuple, List[Dict]] = {
    # Default (any sector without a specific mapping)
    ("default", "any"):             [{"platform": "reddit", "subreddit": "AskReddit"},
                                     {"platform": "quora",  "topic": "General"}],
    # Legal
    (Sector.LEGAL, Language.EN):    [{"platform": "reddit", "subreddit": "legaladvice"},
                                     {"platform": "quora",  "topic": "Law"},
                                     {"platform": "avvo",   "section": "common-law"}],
    (Sector.LEGAL, Language.IT):    [{"platform": "reddit", "subreddit": "italy"},
                                     {"platform": "quora",  "topic": "Diritto"},
                                     {"platform": "king",   "section": "italian-law"}],
    # Medical
    (Sector.MEDICAL, Language.EN):  [{"platform": "reddit", "subreddit": "AskDocs"},
                                     {"platform": "quora",  "topic": "Medicine"},
                                     {"platform": "healthtap", "section": "general"}],
    (Sector.MEDICAL, Language.IT):  [{"platform": "reddit", "subreddit": "italy"},
                                     {"platform": "quora",  "topic": "Medicina"},
                                     {"platform": "medicitalia", "section": "consulti"}],
    # Tech
    (Sector.TECH, Language.EN):     [{"platform": "reddit", "subreddit": "learnprogramming"},
                                     {"platform": "stackoverflow", "tags": "general"}],
    (Sector.TECH, Language.IT):     [{"platform": "reddit", "subreddit": "ItalyInformatica"}],
    # Finance
    (Sector.FINANCE, Language.EN):  [{"platform": "reddit", "subreddit": "personalfinance"}],
    (Sector.FINANCE, Language.IT):  [{"platform": "reddit", "subreddit": "ItaliaPersonalFinance"}],
}


class ForumPublisherAgent:
    """Publishes the question + answer to sector/language-specific forums."""

    def get_target_forums(self, req: Request) -> List[Dict]:
        return FORUM_ROUTING.get((req.sector, req.language),
                                FORUM_ROUTING[("default", "any")])

    def publish(self, req: Request) -> List[Dict]:
        """
        Post to each target forum.
        In production: use PRAW (Reddit), Quora API, Avvo API, etc.
        """
        results = []
        for forum in self.get_target_forums(req):
            print(f"POST → {forum['platform']} | {forum}")
            results.append({
                "platform": forum["platform"],
                "config": forum,
                "ok": True,
                "simulated": True,
            })
        return results


# -----------------------------------------------
# MICROSOFT GRAPH CLIENT + TEAMS EXPERT AGENT
# -----------------------------------------------

class MicrosoftGraphClient:
    """
    Client for Microsoft Graph API.
    Uses OAuth2 client_credentials flow for app-level access.

    Required environment variables:
      AZURE_TENANT_ID, AZURE_CLIENT_ID, AZURE_CLIENT_SECRET
    """

    def __init__(self):
        self.tenant_id = os.getenv("AZURE_TENANT_ID", "")
        self.client_id = os.getenv("AZURE_CLIENT_ID", "")
        self.client_secret = os.getenv("AZURE_CLIENT_SECRET", "")
        self.base_url = "https://graph.microsoft.com/v1.0"
        self._token: Optional[str] = None
        self._token_expires: float = 0

    def _get_token(self) -> str:
        """Acquire access token via client credentials flow."""
        if self._token and time.time() < self._token_expires:
            return self._token

        url = f"https://login.microsoftonline.com/{self.tenant_id}/oauth2/v2.0/token"
        resp = requests.post(url, data={
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "scope": "https://graph.microsoft.com/.default",
            "grant_type": "client_credentials",
        })
        resp.raise_for_status()
        data = resp.json()
        self._token = data["access_token"]
        self._token_expires = time.time() + data.get("expires_in", 3600) - 60
        return self._token

    def _headers(self) -> Dict:
        return {
            "Authorization": f"Bearer {self._get_token()}",
            "Content-Type": "application/json",
        }
    #---------------------------
    #Search people by skill-----
    #---------------------------
    def search_people_by_skill(self, skills: List[str], top: int = 5) -> List[Dict]:
        """
        Search for users in the Azure AD tenant who have matching skills.
        Uses the Microsoft Search API (People entity type).
        """
        skill_query = " OR ".join(skills)

        payload = {
            "requests": [{
                "entityTypes": ["person"],
                "query": {"queryString": skill_query},
                "from": 0,
                "size": top,
            }]
        }

        try:
            resp = requests.post(
                f"{self.base_url}/search/query",
                headers=self._headers(),
                json=payload,
            )
            resp.raise_for_status()
            hits = resp.json()["value"][0].get("hitsContainers", [{}])[0].get("hits", [])

            people = []
            for hit in hits:
                resource = hit.get("resource", {})
                people.append({
                    "displayName": resource.get("displayName", ""),
                    "email": resource.get("emailAddresses", [{}])[0].get("address", ""),
                    "jobTitle": resource.get("jobTitle", ""),
                    "department": resource.get("department", ""),
                    "skills": skills,
                    "rank": hit.get("rank", 0),
                })
            return people

        except Exception as e:
            print(f"Graph search failed: {e}")
            return []
    #-------------------------
    # Send a Teams 1:1 message 
    #-------------------------
    def send_teams_message(self, user_email: str, message: str) -> Dict:
        """
        Send a direct 1:1 message on Teams to the specified user.
        Requires: Chat.Create + ChatMessage.Send permissions.
        """
        try:
            # 1. Create (or retrieve) 1:1 chat
            chat_payload = {
                "chatType": "oneOnOne",
                "members": [
                    {
                        "@odata.type": "#microsoft.graph.aadUserConversationMember",
                        "roles": ["owner"],
                        "user@odata.bind": f"https://graph.microsoft.com/v1.0/users('{user_email}')",
                    }
                ],
            }
            chat_resp = requests.post(
                f"{self.base_url}/chats",
                headers=self._headers(),
                json=chat_payload,
            )
            chat_resp.raise_for_status()
            chat_id = chat_resp.json()["id"]

            # 2. Send message in the chat
            msg_payload = {
                "body": {
                    "contentType": "html",
                    "content": message,
                }
            }
            msg_resp = requests.post(
                f"{self.base_url}/chats/{chat_id}/messages",
                headers=self._headers(),
                json=msg_payload,
            )
            msg_resp.raise_for_status()

            return {"ok": True, "chat_id": chat_id, "recipient": user_email}

        except Exception as e:
            print(f"Teams message failed: {e}")
            return {"ok": False, "error": str(e), "recipient": user_email}
    #------------------------
    # Post to a Teams channel 
    #------------------------
    def post_to_channel(self, team_id: str, channel_id: str, subject: str, body: str) -> Dict:
        """Post a message to a specific Teams channel."""
        try:
            payload = {
                "subject": subject,
                "body": {"contentType": "html", "content": body},
            }
            resp = requests.post(
                f"{self.base_url}/teams/{team_id}/channels/{channel_id}/messages",
                headers=self._headers(),
                json=payload,
            )
            resp.raise_for_status()
            return {"ok": True, "message_id": resp.json().get("id")}
        except Exception as e:
            return {"ok": False, "error": str(e)}

#---------------------------------
# Teams channels mapped per sector 
#---------------------------------
TEAMS_CHANNELS: Dict[Sector, Dict] = {
    Sector.LEGAL:   {"team_id": os.getenv("TEAMS_LEGAL_TEAM_ID", ""),
                     "channel_id": os.getenv("TEAMS_LEGAL_CHANNEL_ID", "")},
    Sector.MEDICAL: {"team_id": os.getenv("TEAMS_MEDICAL_TEAM_ID", ""),
                     "channel_id": os.getenv("TEAMS_MEDICAL_CHANNEL_ID", "")},
    Sector.TECH:    {"team_id": os.getenv("TEAMS_TECH_TEAM_ID", ""),
                     "channel_id": os.getenv("TEAMS_TECH_CHANNEL_ID", "")},
    Sector.FINANCE: {"team_id": os.getenv("TEAMS_FINANCE_TEAM_ID", ""),
                     "channel_id": os.getenv("TEAMS_FINANCE_CHANNEL_ID", "")},
}


class TeamsExpertAgent:
    """
    Agent that:
    1. Searches Azure AD for professionals with matching skills
    2. Sends the question via Teams (1:1 chat to the top expert)
    3. Posts to the sector-specific Teams channel for broader visibility
    """

    def __init__(self, graph_client: Optional[MicrosoftGraphClient] = None):
        self.graph = graph_client or MicrosoftGraphClient()
        self._simulation_mode = not bool(os.getenv("AZURE_TENANT_ID"))

    def find_experts(self, req: Request, top: int = 3) -> List[Dict]:
        """Find experts in the tenant matching the question's sector."""
        skills = SECTOR_TO_SKILLS.get(req.sector, [])
        if not skills:
            return []

        if self._simulation_mode:
            return self._simulate_experts(req.sector, skills, top)

        return self.graph.search_people_by_skill(skills, top=top)

    def ask_expert_via_teams(self, req: Request, expert: Dict) -> Dict:
        """Send the question to a specific expert via Teams 1:1 chat."""
        email = expert.get("email", "")
        if not email:
            return {"ok": False, "error": "No email for expert"}

        message = (
            f"<b>Automated question from multi-agent system</b><br><br>"
            f"<b>Sector:</b> {req.sector.value}<br>"
            f"<b>Question:</b> {req.text}<br><br>"
            f"<b>Auto-generated answer:</b><br>{req.answer}<br><br>"
            f"<i>Could you please confirm or improve this answer?</i>"
        )

        if self._simulation_mode:
            print(f"[SIM] Teams msg → {expert['displayName']} ({email})")
            return {"ok": True, "simulated": True, "recipient": email}

        return self.graph.send_teams_message(email, message)

    def post_to_sector_channel(self, req: Request) -> Dict:
        """Post the question to the sector's Teams channel."""
        channel_config = TEAMS_CHANNELS.get(req.sector)
        if not channel_config or not channel_config.get("team_id"):
            return {"ok": False, "error": f"No Teams channel configured for {req.sector.value}"}

        subject = f"[{req.sector.value.upper()}] Automated question"
        body = (
            f"<b>Question:</b> {req.text}<br><br>"
            f"<b>Auto-generated answer:</b> {req.answer}<br><br>"
            f"<i>Can anyone help validate or improve this?</i>"
        )

        if self._simulation_mode:
            print(f"[SIM] Teams channel post → {req.sector.value}")
            return {"ok": True, "simulated": True, "sector": req.sector.value}

        return self.graph.post_to_channel(
            channel_config["team_id"],
            channel_config["channel_id"],
            subject, body,
        )

    def handle(self, req: Request) -> List[Dict]:
        """
        Full flow:
        1. Search for experts by skill
        2. Send 1:1 Teams message to the top expert
        3. Post to the sector's Teams channel
        """
        results = []

        # 1. Find experts
        experts = self.find_experts(req)
        print(f"Found {len(experts)} expert(s) for {req.sector.value}")

        # 2. Contact the best expert via 1:1 chat
        if experts:
            best = experts[0]
            msg_result = self.ask_expert_via_teams(req, best)
            results.append({
                "action": "teams_direct_message",
                "expert": best,
                **msg_result,
            })

        # 3. Post to the sector channel
        channel_result = self.post_to_sector_channel(req)
        results.append({
            "action": "teams_channel_post",
            **channel_result,
        })

        return results

    def _simulate_experts(self, sector: Sector, skills: List[str], top: int) -> List[Dict]:
        """Simulated data for testing without Azure AD credentials."""
        fake_experts = {
            Sector.LEGAL: [
                {"displayName": "Marco Rossi, Esq.", "email": "m.rossi@contoso.com",
                 "jobTitle": "Senior Legal Counsel", "department": "Legal"},
                {"displayName": "Sarah Johnson", "email": "s.johnson@contoso.com",
                 "jobTitle": "Compliance Officer", "department": "Legal & Compliance"},
            ],
            Sector.MEDICAL: [
                {"displayName": "Dr. Elena Bianchi", "email": "e.bianchi@contoso.com",
                 "jobTitle": "Chief Medical Officer", "department": "Health Services"},
                {"displayName": "Dr. James Wilson", "email": "j.wilson@contoso.com",
                 "jobTitle": "Clinical Researcher", "department": "R&D"},
            ],
            Sector.TECH: [
                {"displayName": "Luca Verdi", "email": "l.verdi@contoso.com",
                 "jobTitle": "Principal Engineer", "department": "Engineering"},
                {"displayName": "Aisha Patel", "email": "a.patel@contoso.com",
                 "jobTitle": "Cloud Architect", "department": "Platform"},
            ],
            Sector.FINANCE: [
                {"displayName": "Paolo Neri, CPA", "email": "p.neri@contoso.com",
                 "jobTitle": "CFO", "department": "Finance"},
                {"displayName": "Emily Chen", "email": "e.chen@contoso.com",
                 "jobTitle": "Tax Specialist", "department": "Finance"},
            ],
        }
        experts = fake_experts.get(sector, [])[:top]
        for e in experts:
            e["skills"] = skills
        return experts


# ---------------
# ORCHESTRATOR
# ---------------

class Orchestrator:
    """
    Full pipeline:
      1. Classify (language + sector)
      2. Route to the correct answer agent
      3. Publish to external forums (Reddit, Quora, Avvo, King, etc.)
      4. Search for an internal expert via Microsoft Graph and ask via Teams
    """

    def __init__(self):
        self.classifier = ClassifierAgent()
        self.publisher = ForumPublisherAgent()
        self.teams_agent = TeamsExpertAgent()

        self.answer_agents: Dict[Sector, BaseAnswerAgent] = {
            Sector.LEGAL:   LegalAgent(),
            Sector.MEDICAL: MedicalAgent(),
            Sector.TECH:    TechAgent(),
            Sector.FINANCE: FinanceAgent(),
            Sector.GENERAL: GeneralAgent(),
        }

    def handle(self, user_text: str, skip_forums: bool = False,
               skip_teams: bool = False) -> Dict:

        # Classify
        req = self.classifier.classify(user_text)
        print(f"\n🏷  Sector: {req.sector.value} | Language: {req.language.value}")

        # Generate answer
        agent = self.answer_agents.get(req.sector, self.answer_agents[Sector.GENERAL])
        req.answer = agent.answer(req)
        print(f" Answer: {req.answer[:100]}...")

        # Publish to external forums
        if not skip_forums:
            req.forum_posts = self.publisher.publish(req)
            print(f"Forums: {len(req.forum_posts)} post(s)")

        # Find and contact internal expert via Teams
        if not skip_teams:
            req.teams_results = self.teams_agent.handle(req)
            print(f"Teams: {len(req.teams_results)} action(s)")

        return {
            "sector": req.sector.value,
            "language": req.language.value,
            "answer": req.answer,
            "forum_posts": req.forum_posts,
            "teams_results": req.teams_results,
        }


# --------
# 7. TESTS
# --------

if __name__ == "__main__":
    orch = Orchestrator()

    test_cases = [
        "Can I break a lease if my landlord refuses to fix plumbing?",
        "Buongiorno, posso recedere dal contratto se il proprietario non ripara?",
        "I have persistent headaches for 3 days. Should I worry?",
        "Salve, ho mal di testa da 3 giorni e febbre. Cosa posso fare?",
        "How do I fix a segfault when using pointers in C?",
        "How should I rebalance my investment portfolio?",
    ]

    for i, q in enumerate(test_cases, 1):
        print(f"\n{'='*70}")
        print(f"TEST {i}: {q[:70]}...")
        print('='*70)
        result = orch.handle(q)
        print(f"  → Forums: {[p['platform'] for p in result['forum_posts']]}")
        print(f"  → Teams:  {[t['action'] for t in result['teams_results']]}")
        time.sleep(0.5)
