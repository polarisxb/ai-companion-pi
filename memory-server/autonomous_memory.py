#!/usr/bin/env python3
"""
AUTONOMOUS MEMORY MANAGER
Decides when to store and retrieve memories automatically.
"""

import re
from typing import Optional, Dict, List
from datetime import datetime


class MemoryDecisionEngine:
    def __init__(self):
        self.conversation_buffer = []
        self.last_retrieval = None

    def should_store(self, message: str, role: str = "user") -> Optional[Dict]:
        if role != "user":
            return None
        message_lower = message.lower()

        explicit_triggers = [
            "remember", "remeber", "don't forget", "dont forget",
            "keep in mind", "make a note", "save this",
        ]
        if any(trigger in message_lower for trigger in explicit_triggers):
            return {
                "content": message,
                "tags": ["explicit-request", "user-directive"],
                "metadata": {"source": "explicit_request"}
            }

        personal_markers = self._detect_personal_info(message)
        if personal_markers:
            return {
                "content": message,
                "tags": personal_markers["tags"],
                "metadata": {"type": "personal", "category": personal_markers["category"]}
            }

        preference_markers = self._detect_preferences(message)
        if preference_markers:
            return {
                "content": message,
                "tags": ["preference"] + preference_markers.get("tags", []),
                "metadata": {"type": "preference"}
            }

        if self._is_decision(message):
            return {
                "content": message,
                "tags": ["decision", "planning"],
                "metadata": {"type": "decision"}
            }

        if self._is_milestone(message):
            return {
                "content": message,
                "tags": ["milestone", "event"],
                "metadata": {"type": "milestone"}
            }

        if self._is_correction(message):
            return {
                "content": message,
                "tags": ["correction", "factual"],
                "metadata": {"type": "correction"}
            }

        return None

    def should_retrieve(self, message: str) -> Optional[str]:
        message_lower = message.lower()

        retrieval_triggers = [
            "do you remember", "what did", "did we", "have we",
            "didn't we", "you mentioned", "you said",
            "what have i told you about", "what do you know about me",
        ]
        if any(trigger in message_lower for trigger in retrieval_triggers):
            query = message_lower
            for trigger in retrieval_triggers:
                query = query.replace(trigger, "")
            return query.strip()

        implicit_patterns = [
            r"\bthat\s+\w+\s+(we|i)\s+(discussed|talked|mentioned)",
            r"\blike\s+(we|i)\s+said",
            r"\bas\s+(we|i)\s+(discussed|mentioned)",
        ]
        for pattern in implicit_patterns:
            if re.search(pattern, message_lower):
                return message

        question_patterns = [
            r"what.*about", r"how.*work", r"why.*did", r"when.*was",
        ]
        is_question = any(re.search(pattern, message_lower) for pattern in question_patterns)
        generic_questions = ["what's up", "how are you", "what can you do"]
        if is_question and not any(generic in message_lower for generic in generic_questions):
            return message

        return None

    def _detect_personal_info(self, message: str) -> Optional[Dict]:
        message_lower = message.lower()
        identity_patterns = {
            "name": [r"(my name is|i'm|i am|call me)\s+(\w+)", r"named\s+(\w+)"],
            "location": [r"(i live in|from|based in)\s+(\w+)", r"i'm in\s+(\w+)"],
            "work": [r"(i work|working) (at|as|for)", r"my job"],
            "relationship": [r"(my wife|my husband|my partner|married to)", r"(boyfriend|girlfriend)"],
            "family": [r"(my son|my daughter|my kid|my child)", r"(mother|father|parent)"],
        }
        for category, patterns in identity_patterns.items():
            if any(re.search(pattern, message_lower) for pattern in patterns):
                return {"category": category, "tags": ["personal", category]}
        return None

    def _detect_preferences(self, message: str) -> Optional[Dict]:
        message_lower = message.lower()
        preference_markers = [
            "i prefer", "i like", "i don't like", "i dont like",
            "i hate", "i love", "my favorite", "favorite",
            "i always", "i never",
        ]
        if any(marker in message_lower for marker in preference_markers):
            tags = []
            if "don't" in message_lower or "hate" in message_lower:
                tags.append("dislike")
            else:
                tags.append("like")
            return {"tags": tags}
        return None

    def _is_decision(self, message: str) -> bool:
        message_lower = message.lower()
        markers = [
            "i've decided", "i'm going to", "i will", "i plan to",
            "let's do", "we should", "i want to",
        ]
        return any(marker in message_lower for marker in markers)

    def _is_milestone(self, message: str) -> bool:
        message_lower = message.lower()
        markers = [
            "we built", "we finished", "we completed", "it worked",
            "success", "milestone", "achieved",
        ]
        return any(marker in message_lower for marker in markers)

    def _is_correction(self, message: str) -> bool:
        message_lower = message.lower()
        markers = [
            "actually", "no, ", "that's wrong", "incorrect",
            "i meant", "to clarify",
        ]
        return any(marker in message_lower for marker in markers)

    def summarize_conversation(self, messages: List[Dict]) -> Optional[Dict]:
        if len(messages) < 5:
            return None
        user_messages = [m["content"] for m in messages if m["role"] == "user"]
        all_text = " ".join(user_messages).lower()
        significant_markers = [
            "built", "created", "decided", "learned", "discovered",
            "problem", "solution", "plan", "goal"
        ]
        if any(marker in all_text for marker in significant_markers):
            words = [w for w in all_text.split() if len(w) > 6][:5]
            summary = f"Conversation on {datetime.now().strftime('%Y-%m-%d')}: "
            summary += f"Discussed topics involving {', '.join(set(words))}."
            return {
                "content": summary,
                "tags": ["conversation-summary", "auto-generated"],
                "metadata": {"type": "summary", "message_count": len(messages)}
            }
        return None
