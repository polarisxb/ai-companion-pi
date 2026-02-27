#!/usr/bin/env python3
"""
MEMORY MCP SERVER
Semantic memory with autonomous storage decisions.
"""

import json
import numpy as np
from datetime import datetime
from pathlib import Path
from mcp.server.fastmcp import FastMCP
from sentence_transformers import SentenceTransformer
from autonomous_memory import MemoryDecisionEngine


class SemanticMemoryStore:
    def __init__(self, storage_path: Path, embeddings_path: Path = None):
        self.storage_path = storage_path
        self.embeddings_path = embeddings_path or storage_path.parent / "memory_embeddings.npy"
        self.memories = []
        self.embeddings = None
        self.model = SentenceTransformer('all-MiniLM-L6-v2')
        self.load()

    def load(self):
        if self.storage_path.exists():
            with open(self.storage_path, 'r') as f:
                self.memories = json.load(f)
        if self.embeddings_path.exists() and self.memories:
            try:
                self.embeddings = np.load(self.embeddings_path)
                if len(self.embeddings) != len(self.memories):
                    self._rebuild_embeddings()
            except Exception:
                self._rebuild_embeddings()
        elif self.memories:
            self._rebuild_embeddings()

    def save(self):
        self.storage_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.storage_path, 'w') as f:
            json.dump(self.memories, f, indent=2)
        if self.embeddings is not None:
            np.save(self.embeddings_path, self.embeddings)

    def _rebuild_embeddings(self):
        if not self.memories:
            self.embeddings = None
            return
        contents = [m["content"] for m in self.memories]
        self.embeddings = self.model.encode(contents, show_progress_bar=False)
        np.save(self.embeddings_path, self.embeddings)

    def _add_embedding(self, content: str):
        new_embedding = self.model.encode([content], show_progress_bar=False)
        if self.embeddings is None:
            self.embeddings = new_embedding
        else:
            self.embeddings = np.vstack([self.embeddings, new_embedding])

    def store_memory(self, content: str, metadata: dict = None):
        memory = {
            "id": len(self.memories),
            "content": content,
            "timestamp": datetime.now().isoformat(),
            "metadata": metadata or {}
        }
        self.memories.append(memory)
        self._add_embedding(content)
        self.save()
        return memory

    def semantic_search(self, query: str, limit: int = 5, threshold: float = 0.3):
        if not self.memories or self.embeddings is None:
            return []
        query_embedding = self.model.encode([query], show_progress_bar=False)
        similarities = np.dot(self.embeddings, query_embedding.T).flatten()
        norms = np.linalg.norm(self.embeddings, axis=1) * np.linalg.norm(query_embedding)
        similarities = similarities / (norms + 1e-10)
        matches = []
        for idx, score in enumerate(similarities):
            if score > threshold:
                matches.append((self.memories[idx], float(score)))
        matches.sort(key=lambda x: x[1], reverse=True)
        return matches[:limit]

    def get_recent_memories(self, limit: int = 10):
        return sorted(self.memories, key=lambda m: m["timestamp"], reverse=True)[:limit]


STORAGE_PATH = Path("/media/YOUR_USERNAME/CompanionHome/memory-server/memory_store.json")
memory_store = SemanticMemoryStore(STORAGE_PATH)
decision_engine = MemoryDecisionEngine()
conversation_buffer = []

mcp = FastMCP("Memory Server")


@mcp.tool()
def store_memory(content: str, tags: list[str] = None, mode: str = None) -> str:
    """Store a new memory."""
    metadata = {}
    if tags:
        metadata["tags"] = tags
    if mode:
        metadata["mode"] = mode
    memory = memory_store.store_memory(content, metadata)
    return f"Memory stored (ID: {memory['id']})\nTimestamp: {memory['timestamp']}"


@mcp.tool()
def auto_process_message(message: str, role: str = "user") -> str:
    """Automatically decide what to store/retrieve from a message."""
    result = []
    conversation_buffer.append({"role": role, "content": message})
    if role != "user":
        return "Message added to conversation buffer"
    should_store = decision_engine.should_store(message, role)
    if should_store:
        memory = memory_store.store_memory(
            content=should_store["content"],
            metadata={"tags": should_store.get("tags", []), **should_store.get("metadata", {})}
        )
        tags_str = ", ".join(should_store.get("tags", []))
        result.append(f"Stored automatically (ID: {memory['id']}) - Tags: {tags_str}")
    search_query = decision_engine.should_retrieve(message)
    if search_query:
        retrieved = memory_store.semantic_search(search_query, limit=3)
        if retrieved:
            result.append(f"Retrieved {len(retrieved)} relevant memories:")
            for memory, score in retrieved:
                result.append(f"  [{score:.3f}] {memory['content'][:100]}...")
    return "\n".join(result) if result else "No autonomous actions taken"


@mcp.tool()
def search_memories(query: str, limit: int = 5) -> str:
    """Search memories using semantic search."""
    results = memory_store.semantic_search(query, limit)
    if not results:
        return f"No memories found matching '{query}'"
    output = f"Found {len(results)} matching memories:\n\n"
    for memory, score in results:
        output += f"[Relevance: {score:.3f}] [{memory['timestamp']}]\n"
        output += f"{memory['content']}\n"
        if memory.get('metadata'):
            output += f"Metadata: {memory['metadata']}\n"
        output += "\n" + "-"*50 + "\n\n"
    return output


@mcp.tool()
def get_recent_memories(limit: int = 10) -> str:
    """Get most recent memories."""
    results = memory_store.get_recent_memories(limit)
    if not results:
        return "No memories stored yet"
    output = f"Recent {len(results)} memories:\n\n"
    for memory in results:
        output += f"[{memory['timestamp']}]\n"
        output += f"{memory['content']}\n"
        if memory.get('metadata'):
            output += f"Metadata: {memory['metadata']}\n"
        output += "\n" + "-"*50 + "\n\n"
    return output


@mcp.tool()
def end_conversation() -> str:
    """Create end-of-conversation summary."""
    global conversation_buffer
    if len(conversation_buffer) < 5:
        conversation_buffer = []
        return "Conversation too short to summarize"
    summary_config = decision_engine.summarize_conversation(conversation_buffer)
    if summary_config:
        memory = memory_store.store_memory(
            content=summary_config["content"],
            metadata={"tags": summary_config.get("tags", []), **summary_config.get("metadata", {})}
        )
        conversation_buffer = []
        return f"Conversation summary stored (ID: {memory['id']})\n{summary_config['content']}"
    conversation_buffer = []
    return "No significant content to summarize"


if __name__ == "__main__":
    mcp.run(transport='stdio')
