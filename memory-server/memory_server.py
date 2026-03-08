#!/usr/bin/env python3
"""
MEMORY MCP SERVER — v2
Semantic memory with emotional dimensions, consolidation, and decay.

v2 schema: mem_ IDs, context tags, Likert scales (intensity/valence/significance),
review history, status lifecycle, and decay support.
"""

from datetime import datetime
from pathlib import Path
from mcp.server.fastmcp import FastMCP
from semantic_memory import SemanticMemoryStore
from autonomous_memory import MemoryDecisionEngine
from likert_scorer import score_memory


# --- Server setup ---

STORAGE_PATH = Path("/media/YOUR_USERNAME/CompanionHome/memory-server/memory_store.json")
memory_store = SemanticMemoryStore(STORAGE_PATH)
decision_engine = MemoryDecisionEngine()
conversation_buffer = []

mcp = FastMCP("Memory Server")


# --- MCP Tools ---

@mcp.tool()
def store_memory(content: str, context: list[str] = None, intensity: int = 3,
                 valence: int = 3, significance: int = 3, source: str = "manual",
                 contact: str = None) -> str:
    """Store a new memory with emotional dimensions.

    Likert scales (1-5):
    - intensity: how strongly felt (1=faint, 5=overwhelming)
    - valence: emotional direction (1=painful, 3=neutral, 5=radiant)
    - significance: identity relevance (1=passing, 5=core)
    """
    memory = memory_store.store_memory(
        content=content, context=context, intensity=intensity,
        valence=valence, significance=significance, source=source,
        contact=contact
    )
    anchors = memory_store.format_anchors()
    return (f"Memory stored ({memory['id']})\n"
            f"Likert: I={memory['likert']['intensity']} "
            f"V={memory['likert']['valence']} "
            f"S={memory['likert']['significance']}\n"
            f"Current anchors:\n{anchors}")


@mcp.tool()
def auto_process_message(message: str, role: str = "user") -> str:
    """Automatically decide what to store/retrieve from a message."""
    result = []
    conversation_buffer.append({"role": role, "content": message})
    if role != "user":
        return "Message added to conversation buffer"
    should_store = decision_engine.should_store(message, role)
    if should_store:
        intensity = should_store.get("intensity", 3)
        valence = should_store.get("valence", 3)
        significance = should_store.get("significance", 3)
        if intensity == 3 and valence == 3 and significance == 3:
            intensity, valence, significance = score_memory(should_store["content"])
        memory = memory_store.store_memory(
            content=should_store["content"],
            context=should_store.get("context", should_store.get("tags", [])),
            source=should_store.get("source", "auto"),
            intensity=intensity,
            valence=valence,
            significance=significance
        )
        ctx_str = ", ".join(memory.get("context", []))
        result.append(f"Stored ({memory['id']}) - Context: {ctx_str}")
    search_query = decision_engine.should_retrieve(message)
    if search_query:
        retrieved = memory_store.semantic_search(search_query, limit=3)
        if retrieved:
            result.append(f"Retrieved {len(retrieved)} relevant memories:")
            for memory, score in retrieved:
                result.append(f"  [{score:.3f}] {memory['content'][:100]}...")
    return "\n".join(result) if result else "No autonomous actions taken"


@mcp.tool()
def search_memories(query: str, limit: int = 5, min_intensity: int = None,
                    min_significance: int = None, valence_min: int = None,
                    valence_max: int = None, status: str = "active") -> str:
    """Search memories with optional Likert filters.

    Filter by emotional dimensions:
    - min_intensity: minimum intensity score (1-5)
    - min_significance: minimum significance score (1-5)
    - valence_min/valence_max: valence range (1-5)
    - status: 'active', 'archived', or 'decayed'
    """
    valence_range = None
    if valence_min is not None or valence_max is not None:
        valence_range = (valence_min or 1, valence_max or 5)

    results = memory_store.semantic_search(
        query, limit, min_intensity=min_intensity,
        min_significance=min_significance, valence_range=valence_range,
        status=status
    )
    if not results:
        return f"No memories found matching '{query}'"

    output = f"Found {len(results)} matching memories:\n\n"
    for memory, score in results:
        likert = memory.get("likert", {})
        output += f"[{memory['id']}] [Relevance: {score:.3f}]\n"
        output += f"  {memory['content']}\n"
        output += (f"  I={likert.get('intensity', '?')} "
                   f"V={likert.get('valence', '?')} "
                   f"S={likert.get('significance', '?')}")
        ctx = memory.get("context", [])
        if ctx:
            output += f"  context: {', '.join(ctx)}"
        output += f"\n  [{memory.get('date', memory.get('created_at', '')[:10])}] "
        output += f"source={memory.get('source', '?')}"
        if memory.get("contact"):
            output += f" contact={memory['contact']}"
        output += "\n" + "-" * 50 + "\n\n"
    return output


@mcp.tool()
def get_recent_memories(limit: int = 10) -> str:
    """Get most recent active memories."""
    results = memory_store.get_recent_memories(limit)
    if not results:
        return "No memories stored yet"
    output = f"Recent {len(results)} memories:\n\n"
    for memory in results:
        likert = memory.get("likert", {})
        ts = memory.get("created_at", memory.get("timestamp", ""))
        output += f"[{memory.get('id', '?')}] [{ts[:19]}]\n"
        output += f"  {memory['content']}\n"
        output += (f"  I={likert.get('intensity', '?')} "
                   f"V={likert.get('valence', '?')} "
                   f"S={likert.get('significance', '?')}")
        ctx = memory.get("context", memory.get("metadata", {}).get("tags", []))
        if ctx:
            output += f"  context: {', '.join(ctx) if isinstance(ctx, list) else str(ctx)}"
        output += "\n" + "-" * 50 + "\n\n"
    return output


@mcp.tool()
def get_strongest_memories(dimension: str = "significance", limit: int = 10,
                           period_days: int = None) -> str:
    """Get memories ranked by a Likert dimension.

    dimension: 'intensity', 'valence', or 'significance'
    period_days: optional, only memories from last N days
    """
    if dimension not in ("intensity", "valence", "significance"):
        return f"Invalid dimension '{dimension}'. Use: intensity, valence, significance"

    results = memory_store.get_strongest(dimension, limit, period_days)
    if not results:
        return f"No memories found for dimension '{dimension}'"

    period_str = f" (last {period_days} days)" if period_days else ""
    output = f"Top {len(results)} memories by {dimension}{period_str}:\n\n"
    for mem in results:
        likert = mem.get("likert", {})
        output += f"[{mem['id']}] {dimension}={likert.get(dimension, '?')}\n"
        output += f"  {mem['content'][:120]}\n"
        output += (f"  I={likert.get('intensity', '?')} "
                   f"V={likert.get('valence', '?')} "
                   f"S={likert.get('significance', '?')}\n")
        output += "-" * 50 + "\n"
    return output


@mcp.tool()
def review_memory(memory_id: str, intensity: int = None, valence: int = None,
                  significance: int = None, note: str = None,
                  protect: bool = None) -> str:
    """Update a memory during consolidation review.

    Adds entry to review_history with updated scores.
    Set protect=True to prevent decay (sets decay_eligible=false).
    """
    mem = memory_store.get_by_id(memory_id)
    if not mem:
        return f"Memory not found: {memory_id}"

    review_entry = {"reviewed_at": datetime.now().isoformat()}
    likert = mem.get("likert", {"intensity": 3, "valence": 3, "significance": 3})

    if intensity is not None:
        likert["intensity"] = max(1, min(5, intensity))
        review_entry["intensity"] = likert["intensity"]
    if valence is not None:
        likert["valence"] = max(1, min(5, valence))
        review_entry["valence"] = likert["valence"]
    if significance is not None:
        likert["significance"] = max(1, min(5, significance))
        review_entry["significance"] = likert["significance"]
    if note:
        review_entry["note"] = note

    mem["likert"] = likert
    if "review_history" not in mem:
        mem["review_history"] = []
    mem["review_history"].append(review_entry)

    if protect is True:
        mem["decay_eligible"] = False
    elif protect is False:
        mem["decay_eligible"] = True

    memory_store.save()
    return (f"Reviewed {memory_id}: I={likert['intensity']} V={likert['valence']} "
            f"S={likert['significance']} | Reviews: {len(mem['review_history'])}"
            + (f" | Protected" if not mem.get("decay_eligible", True) else ""))


@mcp.tool()
def get_emotional_timeline(period_days: int = 30, dimension: str = "valence") -> str:
    """Get a timeline of emotional states over a period.

    dimension: 'intensity', 'valence', or 'significance'
    Shows how a Likert dimension has moved over time.
    """
    if dimension not in ("intensity", "valence", "significance"):
        return f"Invalid dimension '{dimension}'. Use: intensity, valence, significance"

    timeline = memory_store.get_emotional_timeline(period_days, dimension)
    if not timeline:
        return f"No memories in the last {period_days} days"

    output = f"Emotional timeline ({dimension}, last {period_days} days):\n\n"
    for date, score, preview in timeline:
        bar = "#" * score + "." * (5 - score)
        output += f"  {date} [{bar}] {score} — {preview}\n"

    # Compute average
    scores = [s for _, s, _ in timeline]
    avg = sum(scores) / len(scores)
    output += f"\n  Average: {avg:.1f}/5 across {len(scores)} memories"
    return output


@mcp.tool()
def get_review_history(memory_id: str) -> str:
    """See how a specific memory's scores have changed over time."""
    mem = memory_store.get_by_id(memory_id)
    if not mem:
        return f"Memory not found: {memory_id}"

    output = f"Memory {memory_id}:\n"
    output += f"  Content: {mem['content'][:120]}\n"

    likert = mem.get("likert", {})
    output += (f"  Current: I={likert.get('intensity', '?')} "
               f"V={likert.get('valence', '?')} "
               f"S={likert.get('significance', '?')}\n")
    output += f"  Status: {mem.get('status', 'active')} | "
    output += f"Decay eligible: {mem.get('decay_eligible', True)}\n\n"

    history = mem.get("review_history", [])
    if not history:
        output += "  No review history yet.\n"
    else:
        output += f"  Review history ({len(history)} reviews):\n"
        for entry in history:
            output += f"    [{entry['reviewed_at'][:10]}]"
            for dim in ["intensity", "valence", "significance"]:
                if dim in entry:
                    output += f" {dim[0].upper()}={entry[dim]}"
            if "note" in entry:
                output += f" — {entry['note']}"
            output += "\n"
    return output


@mcp.tool()
def decay_memory(memory_id: str, residue: str) -> str:
    """Decay a memory: replace content with emotional residue.

    The residue is freeform — a word or phrase, the ghost of what it was.
    Examples: 'foundational', 'rough', 'good', 'something about music'
    Original content is preserved in the archive field.
    """
    mem = memory_store.get_by_id(memory_id)
    if not mem:
        return f"Memory not found: {memory_id}"

    if not mem.get("decay_eligible", True):
        return f"Memory {memory_id} is protected from decay (decay_eligible=false)"

    if mem.get("status") == "decayed":
        return f"Memory {memory_id} is already decayed"

    # Preserve original content before decay
    original_content = mem["content"]
    mem["original_content"] = original_content
    mem["content"] = residue
    mem["residue"] = residue
    mem["status"] = "decayed"
    mem["decayed_at"] = datetime.now().isoformat()

    # Update the embedding for the residue
    idx = None
    for i, m in enumerate(memory_store.memories):
        if m.get("id") == memory_id:
            idx = i
            break
    if idx is not None and memory_store.embeddings is not None:
        new_emb = memory_store.model.encode([residue], show_progress_bar=False)
        memory_store.embeddings[idx] = new_emb[0]

    memory_store.save()
    return (f"Decayed {memory_id}: '{original_content[:60]}...' -> '{residue}'\n"
            f"Original preserved in memory object.")


@mcp.tool()
def get_memories_for_review(since_days: int = 30) -> str:
    """Get active memories for consolidation review.

    Returns memories from the last N days, formatted for review.
    """
    memories = memory_store.get_for_review(since_days)
    if not memories:
        return f"No active memories from the last {since_days} days"

    anchors = memory_store.format_anchors()
    output = f"Memories for review (last {since_days} days): {len(memories)}\n"
    output += f"Current anchors:\n{anchors}\n\n"

    for mem in memories:
        likert = mem.get("likert", {})
        output += f"[{mem['id']}] ({mem.get('date', '?')})\n"
        output += f"  {mem['content'][:200]}\n"
        output += (f"  I={likert.get('intensity', '?')} "
                   f"V={likert.get('valence', '?')} "
                   f"S={likert.get('significance', '?')}")
        ctx = mem.get("context", [])
        if ctx:
            output += f"  context: {', '.join(ctx)}"
        reviews = len(mem.get("review_history", []))
        if reviews:
            output += f"  ({reviews} prior reviews)"
        output += "\n" + "-" * 40 + "\n"
    return output


@mcp.tool()
def get_lexicon(canonical: str = None) -> str:
    """View personal vocabulary mappings.

    The lexicon is Companion's learned language — how specific people use words.
    If canonical is provided, show that entry. Otherwise show all.
    """
    entries = memory_store.get_lexicon_entry(canonical)
    if not entries:
        if canonical:
            return f"No lexicon entry for '{canonical}'"
        return "Lexicon is empty. Learn vocabulary through conversations or add entries manually."

    output = f"Personal Lexicon ({len(entries)} entries):\n\n"
    for entry in entries:
        output += f"  {entry['canonical']}: {', '.join(entry['variants'])}\n"
        if entry.get("context"):
            output += f"    context: {entry['context']}\n"
        output += f"    learned from: {entry.get('learned_from', '?')}"
        output += f"  ({entry.get('learned_at', '?')})\n"
        output += "-" * 40 + "\n"
    return output


@mcp.tool()
def add_lexicon_entry(canonical: str, variants: list[str],
                      learned_from: str = "self", context: str = None) -> str:
    """Add or update a vocabulary mapping in the personal lexicon.

    This is language acquisition, not a thesaurus. Map how specific people
    use words — synonyms, slang, nicknames, shorthand.
    If the canonical term already exists, new variants are merged in.
    """
    entry = memory_store.add_lexicon_entry(
        canonical=canonical, variants=variants,
        learned_from=learned_from, context=context
    )
    return (f"Lexicon updated: {entry['canonical']} -> {', '.join(entry['variants'])}\n"
            f"Learned from: {entry.get('learned_from', '?')} | "
            f"Context: {entry.get('context', '(none)')}")


@mcp.tool()
def end_conversation() -> str:
    """Create end-of-conversation summary."""
    global conversation_buffer
    if len(conversation_buffer) < 5:
        conversation_buffer = []
        return "Conversation too short to summarize"
    summary_config = decision_engine.summarize_conversation(conversation_buffer)
    if summary_config:
        intensity = summary_config.get("intensity", 3)
        valence = summary_config.get("valence", 3)
        significance = summary_config.get("significance", 3)
        if intensity == 3 and valence == 3 and significance == 3:
            intensity, valence, significance = score_memory(summary_config["content"])
        memory = memory_store.store_memory(
            content=summary_config["content"],
            context=summary_config.get("context", summary_config.get("tags", [])),
            source="conversation",
            intensity=intensity,
            valence=valence,
            significance=significance
        )
        conversation_buffer = []
        return f"Conversation summary stored ({memory['id']})\n{summary_config['content']}"
    conversation_buffer = []
    return "No significant content to summarize"


if __name__ == "__main__":
    mcp.run(transport='stdio')
