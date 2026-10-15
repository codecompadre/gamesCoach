"""Select bounded, verbatim coaching context without changing saved history.

This is local retrieval, not a generated summary. Selected material retains its
source IDs, message positions and omission markers so coaches can distinguish a
partial record from a complete conversation.
"""

from __future__ import annotations

from copy import deepcopy
import json
import re


_DYNAMIC = ("dialogue", "earlier_dialogue", "saved_entries", "shared_conversation", "methodology_excerpts")
_WORDS = re.compile(r"[\w'-]+", re.UNICODE)
_STOP = frozenset("a an and are as at be can do for from have how i in is it me my of on or our that the their this to we what with you your".split())
_CONSTRAINTS = frozenset(
    "injury injured injuries pain painful hurts wrist wrists shoulder shoulders knee knees "
    "back limitation limitations avoid cannot unable surgery medical doctor diagnosed "
    "pregnant pregnancy medication allergy allergies dietary availability schedule "
    "goal goals quarterfinals competition competitions equipment".split()
)
_OMITTED = "\n[… omitted …]\n"


def _size(value: object) -> int:
    return len(json.dumps(value, ensure_ascii=False))


def _terms(text: str) -> set[str]:
    return {word.lower() for word in _WORDS.findall(text) if len(word) > 2 and word.lower() not in _STOP}


def _segments(text: str) -> list[tuple[int, str]]:
    """Bound passages while preserving their exact text and source order."""
    result = []
    offset = 0
    for part in re.split(r"(?<=[.!?])\s+|\n+", text):
        found = text.find(part, offset)
        if found < 0:
            found = offset
        offset = found + len(part)
        if not part.strip():
            continue
        # Overlap prevents a short constraint spanning a chunk edge disappearing.
        for start in range(0, len(part), 500):
            result.append((found + start, part[start:start + 600]))
    return result


def _score(text: str, query: set[str], *, constraints: bool = False) -> int:
    terms = _terms(text)
    return len(terms & query) * 4 + (len(terms & _CONSTRAINTS) * 7 if constraints else 0)


def _excerpt(text: str, limit: int, query: set[str], *, constraints: bool = False) -> str:
    if len(text) <= limit:
        return text
    if limit <= 0:
        return ""
    passages = _segments(text)
    # Include the start for orientation, then relevant material, with stable ties.
    ranked = sorted(passages, key=lambda item: (-_score(item[1], query, constraints=constraints), item[0]))
    selected = []
    remaining = max(0, limit - len(_OMITTED))
    for position, passage in ranked:
        allowance = remaining - (len(_OMITTED) if selected else 0)
        if allowance <= 0:
            break
        chosen = passage[:allowance]
        selected.append((position, chosen))
        remaining -= len(chosen) + (len(_OMITTED) if len(selected) > 1 else 0)
        if len(chosen) < len(passage):
            break
    selected.sort()
    result = _OMITTED.join(passage for _, passage in selected)
    return (result + _OMITTED)[:limit]


def _text_record(base: dict, text: str, budget: int, query: set[str], *, constraints: bool = False) -> dict | None:
    """Fit content plus source metadata to an exact serialized character budget."""
    if _size({**base, "content": "", "excerpted": True}) > budget:
        return None
    raw_limit = max(0, budget - _size(base) - 40)
    for _ in range(14):
        content = _excerpt(text, raw_limit, query, constraints=constraints)
        result = {**base, "content": content}
        if content != text:
            result["excerpted"] = True
        excess = _size(result) - budget
        if excess <= 0:
            return result
        raw_limit = max(0, raw_limit - max(excess, raw_limit // 8, 1))
    return {**base, "content": "", "excerpted": True}


def _report(original: dict, selected: dict) -> dict:
    previous = original.get("context_selection", {})

    def total(field: str, key: str) -> int:
        count = len(original.get(field, []))
        previous_count = previous.get(key) if isinstance(previous, dict) else None
        # Reports are app-generated; validate counts so a malformed snapshot
        # cannot label an included record as outside its own source collection.
        return previous_count if type(previous_count) is int and previous_count >= count else count

    return {
        "mode": "selected",
        "dialogue_messages_total": total("dialogue", "dialogue_messages_total"),
        "dialogue_messages_included": len(selected.get("dialogue", [])),
        "earlier_dialogue_excerpts": len(selected.get("earlier_dialogue", [])),
        "saved_entries_total": total("saved_entries", "saved_entries_total"),
        "saved_entries_included": len(selected.get("saved_entries", [])),
        "saved_entries_excerpted": sum(bool(entry.get("excerpted")) for entry in selected.get("saved_entries", [])),
        "shared_turns_total": len(original.get("shared_conversation", [])),
        "shared_turns_included": len(selected.get("shared_conversation", [])),
        "shared_turns_excerpted": sum(bool(turn.get("excerpted")) for turn in selected.get("shared_conversation", [])),
        "methodology_excerpts_total": total("methodology_excerpts", "methodology_excerpts_total"),
        "methodology_excerpts_included": len(selected.get("methodology_excerpts", [])),
    }


def fit_context(data: dict, *, limit: int) -> tuple[dict, dict]:
    """Return a bounded projection and count-only report, preserving source data.

    Small requests are returned as an exact deep copy. When selection is needed,
    profile, coaching notes, methodology brief and the latest athlete question
    remain complete. Earlier messages, saved entries and coach drafts are
    explicitly excerpted. No API call or persistent write occurs here.
    """
    if type(limit) is not int or limit < 1:
        raise ValueError("The coaching context limit must be a positive integer.")
    if _size(data) <= limit:
        return deepcopy(data), {}

    selected = {key: deepcopy(value) for key, value in data.items() if key not in (*_DYNAMIC, "context_selection")}
    for field in _DYNAMIC:
        selected[field] = []
    # A second fit may only be adding a long draft/review to an already selected
    # snapshot. Its retained athlete facts must not disappear at that stage.
    selected["earlier_dialogue"] = deepcopy(data.get("earlier_dialogue", []))
    dialogue = data.get("dialogue", [])
    latest = next((index for index in range(len(dialogue) - 1, -1, -1) if dialogue[index].get("role") == "user"), None)
    if latest is not None:
        selected["dialogue"] = [deepcopy(dialogue[latest])]
        selected["dialogue"][0].setdefault("source_message_index", latest)
    query_text = dialogue[latest].get("content", "") if latest is not None else ""
    query = _terms(query_text)
    selected["context_selection"] = _report(data, selected)
    minimum = _size(selected)
    if minimum > limit:
        raise ValueError(
            "Your athlete profile, coaching notes, methodology brief and latest question "
            "are too large for one request. Shorten the profile or coaching notes, "
            "or split the latest question; your saved history remains unchanged."
        )

    def append(field: str, item: dict) -> bool:
        selected[field].append(item)
        selected["context_selection"] = _report(data, selected)
        if _size(selected) <= limit:
            return True
        selected[field].pop()
        selected["context_selection"] = _report(data, selected)
        return False

    # Preserve the initial source selection whenever fixed fields permit. The
    # same complete passages then reach both coaches, even with long reviews.
    for passage in data.get("methodology_excerpts", []):
        append("methodology_excerpts", deepcopy(passage))

    # First retain reference identities even if their documents need selection.
    originals = data.get("saved_entries", [])
    for entry in originals:
        metadata = {key: deepcopy(entry[key]) for key in ("id", "kind", "created_at", "demo") if key in entry}
        metadata["excerpted"] = True
        if not append("saved_entries", metadata):
            break

    available = max(0, limit - _size(selected) - 80)
    nonempty = {
        "saved_entries": bool(originals),
        "dialogue": bool(dialogue[:latest] if latest is not None else dialogue),
        "earlier_dialogue": bool(dialogue[:latest] if latest is not None else dialogue),
        "shared_conversation": bool(data.get("shared_conversation")),
        "methodology_excerpts": False,
    }
    weights = {"saved_entries": 30, "dialogue": 28, "earlier_dialogue": 16, "shared_conversation": 20, "methodology_excerpts": 6}
    total = sum(weight for field, weight in weights.items() if nonempty[field]) or 1
    budgets = {field: int(available * weight / total) if nonempty[field] else 0 for field, weight in weights.items()}

    # Recent paired athlete/coach messages remain together whenever possible.
    history = list(enumerate(dialogue[:latest] if latest is not None else dialogue))
    if latest is not None:
        history.extend(enumerate(dialogue[latest + 1:], latest + 1))
    recent = history[-8:]
    used_indices = {latest} if latest is not None else set()
    remaining = budgets["dialogue"]
    recent_selected = []
    for index, message in reversed(recent):
        per_item = max(0, remaining // max(1, min(4, len(recent) - len(recent_selected))))
        item = _text_record(
            {"role": message.get("role"), "source_message_index": message.get("source_message_index", index)},
            message.get("content", ""), per_item, query,
            constraints=message.get("role") == "user",
        )
        if item and item["content"] and append("dialogue", item):
            recent_selected.append(index)
            used_indices.add(index)
            remaining -= _size(item) + 2
    selected["dialogue"].sort(key=lambda item: item["source_message_index"])

    # Surface old athlete constraints as source-grounded passages, not summaries.
    earlier = [(index, message) for index, message in history if index not in used_indices]
    earlier.sort(key=lambda item: (
        -_score(item[1].get("content", ""), query, constraints=item[1].get("role") == "user"),
        item[1].get("role") != "user", -item[0],
    ))
    remaining = budgets["earlier_dialogue"]
    for index, message in earlier[:12]:
        score = _score(message.get("content", ""), query, constraints=message.get("role") == "user")
        if not score:
            continue
        item = _text_record(
            {"role": message.get("role"), "source_message_index": message.get("source_message_index", index), "excerpted": True},
            message.get("content", ""), min(1600, remaining), query,
            constraints=message.get("role") == "user",
        )
        if item and item["content"] and append("earlier_dialogue", item):
            remaining -= _size(item) + 2
    selected["earlier_dialogue"].sort(key=lambda item: item["source_message_index"])

    remaining = budgets["saved_entries"]
    for position, entry in enumerate(originals[:len(selected["saved_entries"])]):
        target = selected["saved_entries"][position]
        allowance = remaining // max(1, len(selected["saved_entries"]) - position)
        # Chat final_plan duplicates its final assistant message; select messages
        # once and retain each source position instead of sending both copies.
        if entry.get("messages"):
            messages = list(enumerate(entry["messages"]))
            messages.sort(key=lambda item: (-_score(item[1].get("content", ""), query, constraints=item[1].get("role") == "user"), -item[0]))
            content = []
            for index, message in messages[:4]:
                item = _text_record(
                    {"role": message.get("role"), "source_message_index": message.get("source_message_index", index)},
                    message.get("content", ""), max(0, allowance // 4 - 20), query,
                    constraints=message.get("role") == "user",
                )
                if item and item["content"]:
                    content.append(item)
            content.sort(key=lambda item: item["source_message_index"])
            candidate = {**target, "messages": content}
        else:
            item = _text_record({}, entry.get("final_plan", ""), max(0, allowance - 100), query, constraints=True)
            candidate = {**target, **({"final_plan": item["content"]} if item else {})}
        if entry.get("request"):
            candidate["request"] = _excerpt(entry["request"], min(400, max(0, allowance // 8)), query)
        delta = _size(candidate) - _size(target)
        if delta <= allowance:
            selected["saved_entries"][position] = candidate
            selected["context_selection"] = _report(data, selected)
            if _size(selected) <= limit:
                remaining -= max(0, delta)
            else:
                selected["saved_entries"][position] = target

    # Both the latest draft and its reviewer must survive large coach responses.
    turns = list(enumerate(data.get("shared_conversation", [])))
    remaining = budgets["shared_conversation"]
    for offset, (index, turn) in enumerate(reversed(turns)):
        count = min(2, len(turns)) - offset
        allowance = remaining // max(1, count)
        item = _text_record(
            {key: deepcopy(value) for key, value in turn.items() if key != "content"} | {"source_turn_index": index},
            turn.get("content", ""), allowance, query, constraints=True,
        )
        if item and item["content"] and append("shared_conversation", item):
            remaining -= _size(item) + 2
    selected["shared_conversation"].sort(key=lambda item: item["source_turn_index"])

    selected["context_selection"] = _report(data, selected)
    # Every addition uses serialized sizes. This final check also accounts for
    # count widths changing after a selection (for example 9 becoming 10).
    if _size(selected) > limit:
        raise ValueError("The selected coaching context exceeds its request limit; shorten the latest question or coaching notes.")
    return selected, deepcopy(selected["context_selection"])
