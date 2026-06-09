import os
import re
import time
import json
import sqlite3
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, field
from enum import Enum

import requests
from dotenv import load_dotenv
from openai import AzureOpenAI

load_dotenv()


# =========================
# ENUMS AND DATA MODELS
# =========================

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
    forum_posts: List[Dict[str, Any]] = field(default_factory=list)
    teams_results: List[Dict[str, Any]] = field(default_factory=list)
    history: List[Dict[str, Any]] = field(default_factory=list)


# =========================
# CLASSIFICATION
# =========================

SECTOR_KEYWORDS: Dict[Sector, List[str]] = {
    Sector.LEGAL: [
        "law", "legal", "court", "attorney", "lawyer", "contract",
        "legge", "avvocato", "tribunale", "contratto", "diritto",
        "gdpr", "normativa", "reato", "codice civile", "lease", "landlord"
    ],
    Sector.MEDICAL: [
        "doctor", "medical", "health", "symptom", "diagnosis",
        "medico", "salute", "sintomo", "diagnosi", "farmaco",
        "febbre", "mal di testa", "headache", "fever"
    ],
    Sector.TECH: [
        "code", "programming", "software", "bug", "api", "deploy",
        "codice", "programmazione", "server", "database", "python",
        "segfault", "pointer", "cloud", "devops", "sql", "sqlite"
    ],
    Sector.FINANCE: [
        "tax", "investment", "stock", "budget", "accounting",
        "tasse", "investimento", "bilancio", "contabilità",
        "portfolio", "rebalance", "audit", "financial"
    ],
}

IT_MARKERS = [
    "come", "cosa", "perché", "quando", "dove", "quale", "chi",
    "posso", "devo", "vorrei", "buongiorno", "salve", "grazie",
    "è", "sono", "può", "della", "nella", "sulla", "contratto",
    "proprietario", "ripara", "codice", "database"
]

# This mapping is used when searching internal experts in Microsoft Graph.
SECTOR_TO_SKILLS: Dict[Sector, List[str]] = {
    Sector.LEGAL: ["Legal", "Contract Law", "GDPR", "Compliance", "Corporate Law"],
    Sector.MEDICAL: ["Medicine", "Clinical Research", "Healthcare", "Pharmacology"],
    Sector.TECH: ["Software Engineering", "Python", "Cloud Architecture", "DevOps"],
    Sector.FINANCE: ["Financial Analysis", "Accounting", "Tax Planning", "Auditing"],
    Sector.GENERAL: [],
}


class ClassifierAgent:
    """Detect the likely language and sector of the incoming request."""

    def detect_language(self, text: str) -> Language:
        lower = text.lower()
        score = sum(1 for token in IT_MARKERS if f" {token} " in f" {lower} ")
        return Language.IT if score >= 2 else Language.EN

    def detect_sector(self, text: str) -> Sector:
        lower = text.lower()
        scores = {
            sector: sum(1 for keyword in keywords if keyword in lower)
            for sector, keywords in SECTOR_KEYWORDS.items()
        }
        best = max(scores, key=scores.get)
        return best if scores[best] > 0 else Sector.GENERAL

    def classify(self, text: str) -> Request:
        return Request(
            text=text,
            sector=self.detect_sector(text),
            language=self.detect_language(text),
            date=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        )


# =========================
# SQLITE PERSISTENCE LAYER
# =========================

class SQLiteStore:
    """
    Persist every run in a SQLite database and retrieve related history
    to enrich future answers.
    """

    def __init__(self, db_path: str = "multi_agent_store.db"):
        self.db_path = db_path
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        return connection

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS runs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TEXT NOT NULL,
                    question TEXT NOT NULL,
                    sector TEXT NOT NULL,
                    language TEXT NOT NULL,
                    answer TEXT,
                    forum_posts_json TEXT,
                    teams_results_json TEXT
                )
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_runs_sector_language
                ON runs(sector, language)
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_runs_created_at
                ON runs(created_at DESC)
                """
            )
            conn.commit()

    def save_run(self, req: Request) -> int:
        with self._connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO runs (
                    created_at,
                    question,
                    sector,
                    language,
                    answer,
                    forum_posts_json,
                    teams_results_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    req.date,
                    req.text,
                    req.sector.value,
                    req.language.value,
                    req.answer,
                    json.dumps(req.forum_posts, ensure_ascii=False),
                    json.dumps(req.teams_results, ensure_ascii=False),
                ),
            )
            conn.commit()
            return int(cur.lastrowid)

    def list_runs(self, limit: int = 20) -> List[Dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT id, created_at, question, sector, language, answer,
                       forum_posts_json, teams_results_json
                FROM runs
                ORDER BY id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()

        return [self._row_to_dict(row) for row in rows]

    def get_recent_runs(
        self,
        sector: Sector,
        language: Language,
        limit: int = 5
    ) -> List[Dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT id, created_at, question, sector, language, answer,
                       forum_posts_json, teams_results_json
                FROM runs
                WHERE sector = ? AND language = ?
                ORDER BY id DESC
                LIMIT ?
                """,
                (sector.value, language.value, limit),
            ).fetchall()

        return [self._row_to_dict(row) for row in rows]

    def find_related_runs(
        self,
        text: str,
        sector: Sector,
        language: Language,
        limit: int = 5
    ) -> List[Dict[str, Any]]:
        """
        Retrieve related runs using simple keyword matching inside the same
        sector and language. Fall back to recent runs if no match is found.
        """
        keywords = self._extract_keywords(text)

        if not keywords:
            return self.get_recent_runs(sector, language, limit=limit)

        clauses = " OR ".join(["LOWER(question) LIKE ?"] * len(keywords))
        params = [f"%{keyword.lower()}%" for keyword in keywords]

        query = f"""
            SELECT id, created_at, question, sector, language, answer,
                   forum_posts_json, teams_results_json
            FROM runs
            WHERE sector = ?
              AND language = ?
              AND ({clauses})
            ORDER BY id DESC
            LIMIT ?
        """

        with self._connect() as conn:
            rows = conn.execute(
                query,
                [sector.value, language.value, *params, limit],
            ).fetchall()

        results = [self._row_to_dict(row) for row in rows]

        if not results:
            results = self.get_recent_runs(sector, language, limit=limit)

        return results

    def _extract_keywords(self, text: str) -> List[str]:
        words = re.findall(r"\b[a-zA-ZàèéìòùÀÈÉÌÒÙ]{4,}\b", text.lower())

        stopwords = {
            "this", "that", "with", "from", "have", "what", "when", "where",
            "would", "could", "should", "about", "your", "will", "there",
            "please", "come", "cosa", "quando", "dove", "quale", "sono",
            "della", "delle", "degli", "dello", "dalla", "dalle", "salve",
            "buongiorno", "grazie"
        }

        filtered = [word for word in words if word not in stopwords]

        seen = set()
        output: List[str] = []

        for word in filtered:
            if word not in seen:
                seen.add(word)
                output.append(word)

        return output[:6]

    def _row_to_dict(self, row: sqlite3.Row) -> Dict[str, Any]:
        return {
            "id": row["id"],
            "created_at": row["created_at"],
            "question": row["question"],
            "sector": row["sector"],
            "language": row["language"],
            "answer": row["answer"],
            "forum_posts": json.loads(row["forum_posts_json"] or "[]"),
            "teams_results": json.loads(row["teams_results_json"] or "[]"),
        }


# =========================
# AZURE OPENAI CLIENT
# =========================

class AzureOpenAIClient:
    """
    Minimal wrapper for Azure OpenAI chat completions.

    Required environment variables:
      AZURE_OPENAI_API_KEY
      AZURE_OPENAI_ENDPOINT
      AZURE_OPENAI_DEPLOYMENT
      AZURE_OPENAI_API_VERSION
    """

    def __init__(self):
        self.api_key = os.getenv("AZURE_OPENAI_API_KEY", "")
        self.endpoint = os.getenv("AZURE_OPENAI_ENDPOINT", "")
        self.deployment = os.getenv("AZURE_OPENAI_DEPLOYMENT", "")
        self.api_version = os.getenv("AZURE_OPENAI_API_VERSION", "2024-02-01")

        self.enabled = all([
            self.api_key,
            self.endpoint,
            self.deployment,
            self.api_version,
        ])

        self.client: Optional[AzureOpenAI] = None

        if self.enabled:
            self.client = AzureOpenAI(
                api_key=self.api_key,
                azure_endpoint=self.endpoint,
                api_version=self.api_version,
            )

    def chat(self, system_prompt: str, user_prompt: str, temperature: float = 0.2) -> str:
        if not self.enabled or not self.client:
            raise RuntimeError("Azure OpenAI is not configured correctly.")

        response = self.client.chat.completions.create(
            model=self.deployment,
            temperature=temperature,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        )

        return response.choices[0].message.content.strip()


# =========================
# ANSWER AGENTS
# =========================

class BaseAnswerAgent:
    """
    Base answer agent.
    If Azure OpenAI is configured, it uses the model.
    Otherwise, it falls back to a local placeholder answer.
    """

    sector: Sector = Sector.GENERAL
    system_prompt: str = "You are a helpful assistant."

    def __init__(self, llm_client: Optional[AzureOpenAIClient] = None):
        self.llm_client = llm_client or AzureOpenAIClient()

    def build_history_context(self, req: Request) -> str:
        if not req.history:
            return "No previous related runs found."

        chunks: List[str] = []

        for i, item in enumerate(req.history[:3], start=1):
            chunks.append(
                f"[Previous Run #{i}]\n"
                f"Date: {item.get('created_at', '')}\n"
                f"Question: {item.get('question', '')}\n"
                f"Answer: {item.get('answer', '')}\n"
            )

        return "\n".join(chunks)

    def build_user_prompt(self, req: Request) -> str:
        language_instruction = (
            "Answer in Italian."
            if req.language == Language.IT
            else "Answer in English."
        )

        return f"""
{language_instruction}

Current question:
{req.text}

Detected sector: {req.sector.value}
Detected language: {req.language.value}

Relevant historical context from previous runs:
{self.build_history_context(req)}

Instructions:
- Answer the current question clearly and directly.
- Reuse historical information only when it is relevant.
- Ignore irrelevant historical information.
- Be practical, concise, and accurate.
"""

    def fallback_answer(self, req: Request) -> str:
        if req.history:
            return (
                f"[{self.sector.value.upper()} AGENT]\n"
                f"Question: {req.text}\n"
                f"Answer generated by integrating {min(len(req.history), 3)} previous related run(s) from SQLite history."
            )

        return (
            f"[{self.sector.value.upper()} AGENT]\n"
            f"Question: {req.text}\n"
            f"Answer generated without prior history."
        )

    def answer(self, req: Request) -> str:
        user_prompt = self.build_user_prompt(req)

        if self.llm_client.enabled:
            try:
                return self.llm_client.chat(
                    system_prompt=self.system_prompt,
                    user_prompt=user_prompt,
                    temperature=0.2,
                )
            except Exception as exc:
                print(f"Azure OpenAI call failed, using fallback: {exc}")

        return self.fallback_answer(req)


class LegalAgent(BaseAnswerAgent):
    sector = Sector.LEGAL
    system_prompt = (
        "You are a legal expert assistant. "
        "If the question is in English, reason using Common Law style guidance. "
        "If the question is in Italian, answer with general guidance based on Italian and EU law. "
        "Do not present yourself as a lawyer. "
        "Do not claim to replace legal counsel. "
        "Be precise and practical."
    )


class MedicalAgent(BaseAnswerAgent):
    sector = Sector.MEDICAL
    system_prompt = (
        "You are a medical information assistant. "
        "Provide general health information only. "
        "Always recommend consulting a licensed doctor for diagnosis or urgent symptoms. "
        "Be cautious and clear."
    )


class TechAgent(BaseAnswerAgent):
    sector = Sector.TECH
    system_prompt = (
        "You are a senior software engineer. "
        "Provide practical, implementation-oriented, clear technical answers. "
        "Prefer structured steps and concise examples."
    )


class FinanceAgent(BaseAnswerAgent):
    sector = Sector.FINANCE
    system_prompt = (
        "You are a financial information assistant. "
        "Provide accurate and practical financial guidance in general terms. "
        "Do not claim regulated advisory authority."
    )


class GeneralAgent(BaseAnswerAgent):
    sector = Sector.GENERAL
    system_prompt = (
        "You are a helpful general-purpose assistant. "
        "Answer clearly, practically, and directly."
    )


# =========================
# FORUM PUBLISHER
# =========================

FORUM_ROUTING: Dict[tuple, List[Dict[str, Any]]] = {
    ("default", "any"): [
        {"platform": "reddit", "subreddit": "AskReddit"},
        {"platform": "quora", "topic": "General"},
    ],

    (Sector.LEGAL, Language.EN): [
        {"platform": "reddit", "subreddit": "legaladvice"},
        {"platform": "quora", "topic": "Law"},
        {"platform": "avvo", "section": "common-law"},
    ],
    (Sector.LEGAL, Language.IT): [
        {"platform": "reddit", "subreddit": "italy"},
        {"platform": "quora", "topic": "Diritto"},
        {"platform": "king", "section": "italian-law"},
    ],

    (Sector.MEDICAL, Language.EN): [
        {"platform": "reddit", "subreddit": "AskDocs"},
        {"platform": "quora", "topic": "Medicine"},
        {"platform": "healthtap", "section": "general"},
    ],
    (Sector.MEDICAL, Language.IT): [
        {"platform": "reddit", "subreddit": "italy"},
        {"platform": "quora", "topic": "Medicina"},
        {"platform": "medicitalia", "section": "consulti"},
    ],

    (Sector.TECH, Language.EN): [
        {"platform": "reddit", "subreddit": "learnprogramming"},
        {"platform": "stackoverflow", "tags": "general"},
    ],
    (Sector.TECH, Language.IT): [
        {"platform": "reddit", "subreddit": "ItalyInformatica"},
    ],

    (Sector.FINANCE, Language.EN): [
        {"platform": "reddit", "subreddit": "personalfinance"},
    ],
    (Sector.FINANCE, Language.IT): [
        {"platform": "reddit", "subreddit": "ItaliaPersonalFinance"},
    ],
}


class ForumPublisherAgent:
    """Publish or simulate publishing to forum targets based on sector and language."""

    def get_target_forums(self, req: Request) -> List[Dict[str, Any]]:
        return FORUM_ROUTING.get(
            (req.sector, req.language),
            FORUM_ROUTING[("default", "any")]
        )

    def publish(self, req: Request) -> List[Dict[str, Any]]:
        results: List[Dict[str, Any]] = []

        for forum in self.get_target_forums(req):
            print(f"POST -> {forum['platform']} | {forum}")
            results.append(
                {
                    "platform": forum["platform"],
                    "config": forum,
                    "ok": True,
                    "simulated": True,
                    "posted_question": req.text,
                    "posted_answer": req.answer,
                }
            )

        return results


# =========================
# MICROSOFT GRAPH CLIENT
# =========================

class MicrosoftGraphClient:
    """
    Microsoft Graph client using client credentials flow.

    Required environment variables:
      AZURE_TENANT_ID
      AZURE_CLIENT_ID
      AZURE_CLIENT_SECRET
    """

    def __init__(self):
        self.tenant_id = os.getenv("AZURE_TENANT_ID", "")
        self.client_id = os.getenv("AZURE_CLIENT_ID", "")
        self.client_secret = os.getenv("AZURE_CLIENT_SECRET", "")
        self.base_url = "https://graph.microsoft.com/v1.0"
        self._token: Optional[str] = None
        self._token_expires: float = 0.0

    def _get_token(self) -> str:
        if self._token and time.time() < self._token_expires:
            return self._token

        url = f"https://login.microsoftonline.com/{self.tenant_id}/oauth2/v2.0/token"
        response = requests.post(
            url,
            data={
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "scope": "https://graph.microsoft.com/.default",
                "grant_type": "client_credentials",
            },
            timeout=30,
        )
        response.raise_for_status()

        data = response.json()
        self._token = data["access_token"]
        self._token_expires = time.time() + data.get("expires_in", 3600) - 60
        return self._token

    def _headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {self._get_token()}",
            "Content-Type": "application/json",
        }

    def search_people_by_skill(self, skills: List[str], top: int = 5) -> List[Dict[str, Any]]:
        if not skills:
            return []

        skill_query = " OR ".join(skills)

        payload = {
            "requests": [
                {
                    "entityTypes": ["person"],
                    "query": {"queryString": skill_query},
                    "from": 0,
                    "size": top,
                }
            ]
        }

        try:
            response = requests.post(
                f"{self.base_url}/search/query",
                headers=self._headers(),
                json=payload,
                timeout=30,
            )
            response.raise_for_status()

            data = response.json()
            hits = data["value"][0].get("hitsContainers", [{}])[0].get("hits", [])

            people: List[Dict[str, Any]] = []

            for hit in hits:
                resource = hit.get("resource", {})
                email_list = resource.get("emailAddresses", [])
                first_email = email_list[0].get("address", "") if email_list else ""

                people.append(
                    {
                        "displayName": resource.get("displayName", ""),
                        "email": first_email,
                        "jobTitle": resource.get("jobTitle", ""),
                        "department": resource.get("department", ""),
                        "skills": skills,
                        "rank": hit.get("rank", 0),
                    }
                )

            return people

        except Exception as exc:
            print(f"Graph search failed: {exc}")
            return []

    def send_teams_message(self, user_email: str, message: str) -> Dict[str, Any]:
        try:
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

            chat_response = requests.post(
                f"{self.base_url}/chats",
                headers=self._headers(),
                json=chat_payload,
                timeout=30,
            )
            chat_response.raise_for_status()
            chat_id = chat_response.json()["id"]

            message_payload = {
                "body": {
                    "contentType": "html",
                    "content": message,
                }
            }

            msg_response = requests.post(
                f"{self.base_url}/chats/{chat_id}/messages",
                headers=self._headers(),
                json=message_payload,
                timeout=30,
            )
            msg_response.raise_for_status()

            return {
                "ok": True,
                "chat_id": chat_id,
                "recipient": user_email,
            }

        except Exception as exc:
            print(f"Teams direct message failed: {exc}")
            return {
                "ok": False,
                "error": str(exc),
                "recipient": user_email,
            }

    def post_to_channel(
        self,
        team_id: str,
        channel_id: str,
        subject: str,
        body: str
    ) -> Dict[str, Any]:
        try:
            payload = {
                "subject": subject,
                "body": {
                    "contentType": "html",
                    "content": body,
                },
            }

            response = requests.post(
                f"{self.base_url}/teams/{team_id}/channels/{channel_id}/messages",
                headers=self._headers(),
                json=payload,
                timeout=30,
            )
            response.raise_for_status()

            return {
                "ok": True,
                "message_id": response.json().get("id"),
            }

        except Exception as exc:
            print(f"Teams channel post failed: {exc}")
            return {
                "ok": False,
                "error": str(exc),
            }


# =========================
# TEAMS CONFIGURATION
# =========================

TEAMS_CHANNELS: Dict[Sector, Dict[str, str]] = {
    Sector.LEGAL: {
        "team_id": os.getenv("TEAMS_LEGAL_TEAM_ID", ""),
        "channel_id": os.getenv("TEAMS_LEGAL_CHANNEL_ID", ""),
    },
    Sector.MEDICAL: {
        "team_id": os.getenv("TEAMS_MEDICAL_TEAM_ID", ""),
        "channel_id": os.getenv("TEAMS_MEDICAL_CHANNEL_ID", ""),
    },
    Sector.TECH: {
        "team_id": os.getenv("TEAMS_TECH_TEAM_ID", ""),
        "channel_id": os.getenv("TEAMS_TECH_CHANNEL_ID", ""),
    },
    Sector.FINANCE: {
        "team_id": os.getenv("TEAMS_FINANCE_TEAM_ID", ""),
        "channel_id": os.getenv("TEAMS_FINANCE_CHANNEL_ID", ""),
    },
}


# =========================
# TEAMS EXPERT AGENT
# =========================

class TeamsExpertAgent:
    """
    Workflow:
    1. Find experts by skill
    2. Send a direct Teams message to the best expert
    3. Post the same question and answer to the sector channel
    """

    def __init__(self, graph_client: Optional[MicrosoftGraphClient] = None):
        self.graph = graph_client or MicrosoftGraphClient()
        self.simulation_mode = not bool(os.getenv("AZURE_TENANT_ID"))

    def find_experts(self, req: Request, top: int = 3) -> List[Dict[str, Any]]:
        skills = SECTOR_TO_SKILLS.get(req.sector, [])
        if not skills:
            return []

        if self.simulation_mode:
            return self._simulate_experts(req.sector, skills, top)

        return self.graph.search_people_by_skill(skills, top=top)

    def ask_expert_via_teams(self, req: Request, expert: Dict[str, Any]) -> Dict[str, Any]:
        email = expert.get("email", "")
        if not email:
            return {"ok": False, "error": "No email found for expert."}

        message = (
            f"<b>Automated question from multi-agent system</b><br><br>"
            f"<b>Sector:</b> {req.sector.value}<br>"
            f"<b>Question:</b> {req.text}<br><br>"
            f"<b>Auto-generated answer:</b><br>{req.answer}<br><br>"
            f"<i>Could you validate or improve this answer?</i>"
        )

        if self.simulation_mode:
            print(f"[SIM] Teams DM -> {expert.get('displayName')} ({email})")
            return {
                "ok": True,
                "simulated": True,
                "recipient": email,
            }

        return self.graph.send_teams_message(email, message)

    def post_to_sector_channel(self, req: Request) -> Dict[str, Any]:
        channel_config = TEAMS_CHANNELS.get(req.sector)

        if (
            not channel_config
            or not channel_config.get("team_id")
            or not channel_config.get("channel_id")
        ):
            return {
                "ok": False,
                "error": f"No Teams channel configured for sector {req.sector.value}.",
            }

        subject = f"[{req.sector.value.upper()}] Automated question"
        body = (
            f"<b>Question:</b> {req.text}<br><br>"
            f"<b>Auto-generated answer:</b><br>{req.answer}<br><br>"
            f"<i>Can anyone help validate or improve this?</i>"
        )

        if self.simulation_mode:
            print(f"[SIM] Teams channel post -> sector={req.sector.value}")
            return {
                "ok": True,
                "simulated": True,
                "sector": req.sector.value,
            }

        return self.graph.post_to_channel(
            channel_config["team_id"],
            channel_config["channel_id"],
            subject,
            body,
        )

    def handle(self, req: Request) -> List[Dict[str, Any]]:
        results: List[Dict[str, Any]] = []

        experts = self.find_experts(req)
        print(f"Found {len(experts)} expert(s) for sector={req.sector.value}")

        if experts:
            best = experts[0]
            dm_result = self.ask_expert_via_teams(req, best)
            results.append(
                {
                    "action": "teams_direct_message",
                    "expert": best,
                    **dm_result,
                }
            )

        channel_result = self.post_to_sector_channel(req)
        results.append(
            {
                "action": "teams_channel_post",
                **channel_result,
            }
        )

        return results

    def _simulate_experts(
        self,
        sector: Sector,
        skills: List[str],
        top: int
    ) -> List[Dict[str, Any]]:
        fake_experts: Dict[Sector, List[Dict[str, Any]]] = {
            Sector.LEGAL: [
                {
                    "displayName": "Marco Rossi, Esq.",
                    "email": "m.rossi@contoso.com",
                    "jobTitle": "Senior Legal Counsel",
                    "department": "Legal",
                },
                {
                    "displayName": "Sarah Johnson",
                    "email": "s.johnson@contoso.com",
                    "jobTitle": "Compliance Officer",
                    "department": "Legal & Compliance",
                },
            ],
            Sector.MEDICAL: [
                {
                    "displayName": "Dr. Elena Bianchi",
                    "email": "e.bianchi@contoso.com",
                    "jobTitle": "Chief Medical Officer",
                    "department": "Health Services",
                },
                {
                    "displayName": "Dr. James Wilson",
                    "email": "j.wilson@contoso.com",
                    "jobTitle": "Clinical Researcher",
                    "department": "R&D",
                },
            ],
            Sector.TECH: [
                {
                    "displayName": "Luca Verdi",
                    "email": "l.verdi@contoso.com",
                    "jobTitle": "Principal Engineer",
                    "department": "Engineering",
                },
                {
                    "displayName": "Aisha Patel",
                    "email": "a.patel@contoso.com",
                    "jobTitle": "Cloud Architect",
                    "department": "Platform",
                },
            ],
            Sector.FINANCE: [
                {
                    "displayName": "Paolo Neri, CPA",
                    "email": "p.neri@contoso.com",
                    "jobTitle": "CFO",
                    "department": "Finance",
                },
                {
                    "displayName": "Emily Chen",
                    "email": "e.chen@contoso.com",
                    "jobTitle": "Tax Specialist",
                    "department": "Finance",
                },
            ],
        }

        selected = fake_experts.get(sector, [])[:top]
        for expert in selected:
            expert["skills"] = skills

        return selected


# =========================
# ORCHESTRATOR
# =========================

class Orchestrator:
    """
    Full pipeline:
    1. Classify the request
    2. Load related history from SQLite
    3. Generate an answer using Azure OpenAI if available
    4. Publish to external forums
    5. Contact internal experts through Teams
    6. Save the full run to SQLite
    """

    def __init__(self, db_path: str = "multi_agent_store.db"):
        self.classifier = ClassifierAgent()
        self.publisher = ForumPublisherAgent()
        self.teams_agent = TeamsExpertAgent()
        self.store = SQLiteStore(db_path=db_path)
        self.llm_client = AzureOpenAIClient()

        self.answer_agents: Dict[Sector, BaseAnswerAgent] = {
            Sector.LEGAL: LegalAgent(self.llm_client),
            Sector.MEDICAL: MedicalAgent(self.llm_client),
            Sector.TECH: TechAgent(self.llm_client),
            Sector.FINANCE: FinanceAgent(self.llm_client),
            Sector.GENERAL: GeneralAgent(self.llm_client),
        }

    def handle(
        self,
        user_text: str,
        skip_forums: bool = False,
        skip_teams: bool = False
    ) -> Dict[str, Any]:
        # Step 1: classify the input
        req = self.classifier.classify(user_text)
        print(f"\n[INFO] Sector: {req.sector.value} | Language: {req.language.value}")

        # Step 2: load historical runs from SQLite
        req.history = self.store.find_related_runs(
            text=req.text,
            sector=req.sector,
            language=req.language,
            limit=5,
        )
        print(f"[INFO] Loaded {len(req.history)} related historical run(s)")

        # Step 3: generate the answer
        agent = self.answer_agents.get(req.sector, self.answer_agents[Sector.GENERAL])
        req.answer = agent.answer(req)
        print(f"[INFO] Answer generated: {req.answer[:160]}...")

        # Step 4: publish to forums
        if not skip_forums:
            req.forum_posts = self.publisher.publish(req)
            print(f"[INFO] Forum posts created: {len(req.forum_posts)}")

        # Step 5: run Teams expert workflow
        if not skip_teams:
            req.teams_results = self.teams_agent.handle(req)
            print(f"[INFO] Teams actions created: {len(req.teams_results)}")

        # Step 6: save the full run
        run_id = self.store.save_run(req)
        print(f"[INFO] Saved run to SQLite with run_id={run_id}")

        return {
            "run_id": run_id,
            "sector": req.sector.value,
            "language": req.language.value,
            "answer": req.answer,
            "history_used": req.history,
            "forum_posts": req.forum_posts,
            "teams_results": req.teams_results,
        }


# =========================
# MAIN TEST BLOCK
# =========================

if __name__ == "__main__":
    orchestrator = Orchestrator(db_path="multi_agent_store.db")

    test_cases = [
        "Can I break a lease if my landlord refuses to fix plumbing?",
        "Buongiorno, posso recedere dal contratto se il proprietario non ripara?",
        "I have persistent headaches for 3 days. Should I worry?",
        "Salve, ho mal di testa da 3 giorni e febbre. Cosa posso fare?",
        "How do I fix a segfault when using pointers in C?",
        "How should I rebalance my investment portfolio?",
        "How do I deploy a Python API to the cloud?",
        "Buongiorno, come posso gestire un contratto con clausole non chiare?",
    ]

    for index, question in enumerate(test_cases, start=1):
        print("\n" + "=" * 80)
        print(f"TEST {index}: {question}")
        print("=" * 80)

        result = orchestrator.handle(question)

        print(f"Run ID: {result['run_id']}")
        print(f"History used: {len(result['history_used'])}")
        print(f"Forum platforms: {[item['platform'] for item in result['forum_posts']]}")
        print(f"Teams actions: {[item['action'] for item in result['teams_results']]}")

        time.sleep(0.5)

    print("\nRecent runs stored in SQLite:")
    for row in orchestrator.store.list_runs(limit=10):
        print(
            f"- #{row['id']} | {row['created_at']} | "
            f"{row['sector']} | {row['language']} | "
            f"{row['question'][:60]}"
        )
