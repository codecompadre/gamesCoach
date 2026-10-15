from copy import deepcopy
import json

import pytest

from gamescoach.context import fit_context


def context(**changes):
    return {
        "current_date": "2026-10-15",
        "reference_time": "2026-10-15T12:00:00-04:00",
        "athlete": {"goals": "CrossFit Open Quarterfinals", "limitations": "Wrist discomfort"},
        "optional_coaching_notes": "Keep the athlete's training repeatable.",
        "saved_entries": [],
        "dialogue": [{"role": "user", "content": "How should I improve my aerobic capacity for the Open?"}],
        "shared_conversation": [],
        "methodology_brief": [{"filename": "guide.pdf", "pdf_page": 17, "text": "Mechanics, consistency, then intensity."}],
        "methodology_excerpts": [{"guide_id": "0123456789abcdef", "filename": "guide.pdf", "pdf_page": 18, "text": "Apply intensity relative to the athlete."}],
        **changes,
    }


def serialized(value):
    return json.dumps(value, ensure_ascii=False)


def test_small_context_is_an_exact_independent_copy_without_selection_metadata():
    original = context()
    fitted, report = fit_context(original, limit=10000)
    assert fitted == original
    assert report == {}
    assert "context_selection" not in fitted
    fitted["athlete"]["goals"] = "changed"
    fitted["methodology_excerpts"][0]["text"] = "changed"
    assert original["athlete"]["goals"] == "CrossFit Open Quarterfinals"
    assert original["methodology_excerpts"][0]["text"] != "changed"


def test_old_athlete_constraints_and_topic_facts_survive_long_history_verbatim():
    injury = "My right wrist hurts during handstand push-ups; avoid loaded extension."
    goal = "My goal is to reach CrossFit Open Quarterfinals by improving aerobic capacity."
    dialogue = [
        {"role": "user", "content": "Ordinary training note. " * 600 + injury + " Other training notes. " * 600},
        {"role": "assistant", "content": "Coach explanation. " * 700},
        {"role": "user", "content": goal},
        {"role": "assistant", "content": "Acknowledge that goal. " * 700},
    ]
    for _ in range(30):
        dialogue.extend([
            {"role": "user", "content": "Discuss this workout. " * 500},
            {"role": "assistant", "content": "Practical training reply. " * 700},
        ])
    latest = "How can I improve aerobic capacity next week?"
    dialogue.append({"role": "user", "content": latest})
    fitted, report = fit_context(context(dialogue=dialogue), limit=30000)
    assert len(serialized(fitted)) <= 30000
    assert fitted["dialogue"][-1]["content"] == latest
    assert any(injury in item["content"] and item["source_message_index"] == 0 for item in fitted["earlier_dialogue"])
    assert any(goal in item["content"] and item["source_message_index"] == 2 for item in fitted["earlier_dialogue"])
    assert report["mode"] == "selected"
    assert report["dialogue_messages_total"] == len(dialogue)


def test_huge_references_keep_ids_and_relevant_source_text_without_changing_records():
    entries = [
        {
            "id": f"abcdef12345{index}", "kind": "document",
            "created_at": "2026-10-14T12:00:00+00:00", "demo": index == 4,
            "request": "Imported weekly workouts",
            "final_plan": "Monday strength. " * 5000 + f" Aerobic session {index}: row 4 x 4 minutes with easy recovery. " + "Accessory notes. " * 5000,
        }
        for index in range(5)
    ]
    original = context(saved_entries=entries)
    before = deepcopy(original)
    fitted, report = fit_context(original, limit=20000)
    assert len(serialized(fitted)) <= 20000
    assert original == before
    assert [entry["id"] for entry in fitted["saved_entries"]] == [entry["id"] for entry in entries]
    for index, entry in enumerate(fitted["saved_entries"]):
        assert entry["kind"] == "document"
        assert entry["created_at"] == entries[index]["created_at"]
        assert entry["demo"] == entries[index]["demo"]
        assert entry["excerpted"] is True
        assert f"Aerobic session {index}: row 4 x 4 minutes with easy recovery." in entry["final_plan"]
    assert report["saved_entries_excerpted"] == 5
    assert fitted["methodology_excerpts"] == original["methodology_excerpts"]


def test_saved_chat_is_not_duplicated_as_whole_final_plan_and_messages():
    final = "Discuss aerobic recovery. " * 6000
    entry = {
        "id": "abcdef123456", "kind": "chat", "created_at": "2026-10-14T12:00:00+00:00", "demo": False,
        "request": "Help me improve aerobic capacity",
        "final_plan": final,
        "messages": [
            {"role": "user", "content": "My wrist pain affects front-rack positions."},
            {"role": "assistant", "content": final},
        ],
    }
    fitted, _ = fit_context(context(saved_entries=[entry]), limit=12000)
    selected = fitted["saved_entries"][0]
    assert selected["id"] == entry["id"]
    assert "final_plan" not in selected
    assert selected["excerpted"] is True
    assert [message["source_message_index"] for message in selected["messages"]] == [0, 1]
    assert selected["messages"][0]["content"] == entry["messages"][0]["content"]


def test_saved_chat_message_positions_survive_refitting_after_a_large_draft():
    messages = [
        {"role": "user" if index % 2 == 0 else "assistant", "content": (
            ("My wrists hurt and my schedule changed. " if index in (12, 14) else "Training record. ") * 1000
        )}
        for index in range(16)
    ]
    entry = {
        "id": "abcdef123456", "kind": "chat", "created_at": "2026-10-14T12:00:00+00:00",
        "demo": False, "request": "Review CrossFit training", "final_plan": messages[-1]["content"], "messages": messages,
    }
    prepared, _ = fit_context(context(saved_entries=[entry]), limit=20000)
    positions = [message["source_message_index"] for message in prepared["saved_entries"][0]["messages"]]
    assert positions == [12, 13, 14, 15]
    prepared["shared_conversation"] = [{"provider": "ChatGPT", "stage": "chat-proposal", "content": "CrossFit draft. " * 10000}]
    fitted, _ = fit_context(prepared, limit=21000)
    assert [message["source_message_index"] for message in fitted["saved_entries"][0]["messages"]] == positions
    assert len(serialized(fitted)) <= 21000


def test_large_draft_and_review_both_survive_with_roles_and_stage_markers():
    turns = [
        {"provider": "ChatGPT", "stage": "chat-proposal", "content": "Initial aerobic recommendation. " * 6000},
        {"provider": "Gemini", "stage": "chat-review", "content": "Review the wrist limitation and aerobic prescription. " * 6000},
    ]
    original = context(shared_conversation=turns)
    fitted, _ = fit_context(original, limit=18000)
    assert len(serialized(fitted)) <= 18000
    assert [(turn["provider"], turn["stage"]) for turn in fitted["shared_conversation"]] == [(turn["provider"], turn["stage"]) for turn in turns]
    assert all(turn["excerpted"] and turn["content"] for turn in fitted["shared_conversation"])
    assert [turn["source_turn_index"] for turn in fitted["shared_conversation"]] == [0, 1]
    assert original["shared_conversation"] == turns


def test_second_fit_keeps_retained_old_facts_and_complete_pdf_passages():
    original = context(dialogue=[
        {"role": "user", "content": "My wrist pain means I avoid loaded extension."},
        {"role": "assistant", "content": "Choose a tolerable position. " * 2000},
    ] + [
        {"role": role, "content": "Discuss aerobic progress. " * 1000}
        for role in ["user", "assistant"] * 15
    ] + [{"role": "user", "content": "How can I improve aerobic capacity?"}])
    initial, _ = fit_context(original, limit=20000)
    assert initial["earlier_dialogue"]
    initial["shared_conversation"] = [
        {"provider": "ChatGPT", "stage": "chat-proposal", "content": "Draft. " * 30000},
        {"provider": "Gemini", "stage": "chat-review", "content": "Review. " * 30000},
    ]
    second, _ = fit_context(initial, limit=30000)
    assert second["context_selection"]["dialogue_messages_total"] == len(original["dialogue"])
    assert second["context_selection"]["saved_entries_total"] == initial["context_selection"]["saved_entries_total"]
    assert second["context_selection"]["methodology_excerpts_total"] == initial["context_selection"]["methodology_excerpts_total"]
    assert second["context_selection"]["shared_turns_total"] == 2
    assert second["context_selection"]["dialogue_messages_included"] == len(second["dialogue"])
    assert second["earlier_dialogue"][:len(initial["earlier_dialogue"])] == initial["earlier_dialogue"]
    assert second["methodology_excerpts"] == original["methodology_excerpts"]
    assert second["dialogue"][-1]["content"] == original["dialogue"][-1]["content"]
    assert second["athlete"] == original["athlete"]
    assert second["optional_coaching_notes"] == original["optional_coaching_notes"]
    assert second["methodology_brief"] == original["methodology_brief"]


def test_mandatory_fields_alone_fail_with_useful_guidance_without_mutating():
    original = context(optional_coaching_notes="Training constraint. " * 6000)
    before = deepcopy(original)
    with pytest.raises(ValueError, match="Shorten the profile or coaching notes"):
        fit_context(original, limit=20000)
    assert original == before


def test_latest_question_and_full_fixed_fields_are_retained_despite_json_escaping():
    latest = '今日の有酸素トレーニングは？ "\\\n' * 100
    original = context(
        dialogue=[{"role": "assistant", "content": 'Quote: "\\\n\t' * 12000}, {"role": "user", "content": latest}],
        shared_conversation=[{"provider": "Gemini", "stage": "chat-review", "content": '\\"\n\x01' * 20000}],
    )
    fitted, report = fit_context(original, limit=14000)
    assert len(serialized(fitted)) <= 14000
    assert fitted["dialogue"][-1]["content"] == latest
    assert fitted["athlete"] == original["athlete"]
    assert fitted["optional_coaching_notes"] == original["optional_coaching_notes"]
    assert fitted["methodology_brief"] == original["methodology_brief"]
    assert fitted["context_selection"] == report
    assert all(isinstance(value, (int, str)) for value in report.values())


def test_combined_large_inputs_fit_with_full_notes_brief_and_latest_question():
    original = context(
        optional_coaching_notes="Athlete-specific recovery preferences. " * 420,
        methodology_brief=[{"filename": "guide.pdf", "pdf_page": index + 1, "text": "Preserve movement quality. " * 200} for index in range(3)],
        methodology_excerpts=[{"guide_id": "0123456789abcdef", "filename": "guide.pdf", "pdf_page": index + 50, "text": "Relative aerobic intensity. " * 55} for index in range(6)],
        dialogue=[{"role": role, "content": "Aerobic training discussion. " * 800} for role in ["user", "assistant"] * 20] + [{"role": "user", "content": "My full question: " + "How can I improve aerobic capacity? " * 160}],
        saved_entries=[{"id": f"abcdef12345{index}", "kind": "document", "created_at": "2026-10-14T12:00:00+00:00", "demo": False, "request": "My training week", "final_plan": "Aerobic workout details. " * 3000} for index in range(5)],
    )
    before = deepcopy(original)
    prepared, _ = fit_context(original, limit=65000)
    assert len(serialized(prepared)) <= 65000
    assert prepared["methodology_excerpts"] == original["methodology_excerpts"]
    assert prepared["optional_coaching_notes"] == original["optional_coaching_notes"]
    assert prepared["methodology_brief"] == original["methodology_brief"]
    assert prepared["dialogue"][-1]["content"] == original["dialogue"][-1]["content"]
    assert [entry["id"] for entry in prepared["saved_entries"]] == [entry["id"] for entry in original["saved_entries"]]
    prepared["shared_conversation"] = [
        {"provider": "ChatGPT", "stage": "chat-proposal", "content": "Aerobic draft. " * 10000},
        {"provider": "Gemini", "stage": "chat-review", "content": "Aerobic review. " * 10000},
    ]
    final, _ = fit_context(prepared, limit=99000)
    assert len(serialized(final)) <= 99000
    assert len(final["shared_conversation"]) == 2
    assert final["methodology_excerpts"] == original["methodology_excerpts"]
    assert original == before


@pytest.mark.parametrize("limit", [0, -1, True, 2.0])
def test_invalid_limits_fail_locally(limit):
    with pytest.raises(ValueError, match="positive integer"):
        fit_context(context(), limit=limit)
