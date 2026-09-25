from src.action_intents import classify_tool_intent, message_needs_tools


def test_calendar_entry_request_promotes_to_agent():
    assert message_needs_tools("Can you add an entry to my calendar?")
    intent = classify_tool_intent("Can you add an entry to my calendar?")
    assert intent.needs_tools
    assert intent.category == "calendar"


def test_calendar_imperative_variants_promote_to_agent():
    assert message_needs_tools("add lunch with Sam to my calendar tomorrow at noon")
    assert message_needs_tools("schedule a call with Mina next Friday")
    assert message_needs_tools("put dentist appointment on my calendar")
    assert message_needs_tools("Alright. Recreate that same appointment")
    assert message_needs_tools("Okay delete that doctor appointment from the calendar")
    assert message_needs_tools("have another go at adding a test entry to the calendar")
    assert message_needs_tools(
        "Okay so you should be able to create that calendar event for tomorrow at 1:30 p.m. right for me to go to the hardware store"
    )
    assert message_needs_tools(
        "make it an appointment at 12pm for me to visit the doctor it's tomorrow the 2nd of June 2026"
    )


def test_calendar_read_requests_promote_to_agent():
    assert message_needs_tools("What upcoming events do I have?")
    assert message_needs_tools("Can you show my next appointments?")
    assert message_needs_tools("Do I have upcoming Taekwondo classes this week?")
    assert message_needs_tools("What's on my calendar tomorrow?")
    assert message_needs_tools("When is my next meeting?")


def test_note_todo_and_reminder_actions_promote_to_agent():
    assert message_needs_tools("add milk to my todo list")
    assert message_needs_tools("take a note that the server needs checking")
    assert message_needs_tools("set a reminder to call Pat at 4pm")


def test_email_and_ui_actions_promote_to_agent():
    assert message_needs_tools("reply to that email")
    assert message_needs_tools("mark those emails as read")
    assert message_needs_tools("open my calendar")
    assert message_needs_tools("turn off web search")


def test_research_action_promotes_to_agent():
    assert message_needs_tools("research cost effective local models")
    assert message_needs_tools("can you look into GPU hosting options")


def test_explicit_web_search_promotes_to_agent():
    assert message_needs_tools("use web search and find a recipe for chocolate chip cookies")
    assert message_needs_tools("do a web search for the best chocolate chip cookies")
    assert message_needs_tools("search the web for current RTX 3090 prices")
    assert classify_tool_intent("use web search and find a recipe").category == "web"


def test_workspace_agent_requests_promote_to_shell_workspace():
    prompts = [
        "fix the bug in this repo",
        "run the tests for this project",
        "debug the server logs",
        "run terminal-bench on this task",
        "inspect the traceback and patch the code",
    ]
    for prompt in prompts:
        intent = classify_tool_intent(prompt)
        assert intent.needs_tools
        assert intent.category == "workspace"


def test_explanatory_calendar_questions_stay_plain_chat():
    assert not message_needs_tools("How do I add an entry to my calendar?")
    assert not message_needs_tools("What about the built-in Odysseus calendar, is that linked to email?")
    assert not message_needs_tools("Can you explain how calendar reminders work?")
    intent = classify_tool_intent("How do I add an entry to my calendar?")
    assert not intent.needs_tools
    assert intent.reason == "explanatory feature question"


def test_router_reports_non_calendar_categories():
    assert classify_tool_intent("reply to that email").category == "email"
    assert classify_tool_intent("open my calendar").category == "ui"
    assert classify_tool_intent("research cost effective local models").category == "research"


# Real, added 2026-09-25: found completely missing while investigating
# a real Home Assistant question ("What's the indoor temperature?")
# that silently failed to trigger tool use in plain chat mode. This
# entire category didn't exist before this.
def test_home_assistant_lookup_and_control_requests_promote_to_agent():
    assert message_needs_tools("What's the indoor temperature?")
    assert message_needs_tools("Is the thermostat on?")
    assert message_needs_tools("Turn the temperature down")
    assert message_needs_tools("Set the thermostat to 70")
    assert message_needs_tools("Turn off the living room light")
    assert message_needs_tools("Is the garage door open?")
    intent = classify_tool_intent("What's the indoor temperature?")
    assert intent.category == "home-assistant"


def test_home_assistant_does_not_match_unrelated_weather_or_figurative_door():
    # Real, caught live while building this: "door" alone is too
    # ambiguous/figurative to safely match ("the door to opportunity").
    assert not message_needs_tools("What is the temperature outside today?")
    assert not message_needs_tools(
        "Is the door to opportunity still open for this role?"
    )


# Real, added 2026-09-25: natural system-diagnostic questions ("Is the
# whisper service running?", "How much disk space do I have left?")
# were falling through entirely -- the pre-existing "server/process
# debugging request" pattern only covered imperative/"can you"
# phrasing, not a plain question.
def test_system_diagnostic_questions_promote_to_agent():
    assert message_needs_tools("How much disk space do I have left?")
    assert message_needs_tools("Is the whisper service running?")
    assert message_needs_tools("Is odysseus running?")
    assert message_needs_tools("How much memory is free?")
    intent = classify_tool_intent("Is the whisper service running?")
    assert intent.category == "workspace"


def test_system_diagnostic_pattern_does_not_match_stock_price_questions():
    # Real bug caught and fixed while building this: an earlier draft
    # included bare "up"/"down" as state words, which incorrectly
    # matched real, unrelated stock-price-direction questions.
    assert not message_needs_tools("Is Bitcoin going up?")
    assert not message_needs_tools("Is Bitcoin up?")
    assert not message_needs_tools("Is KTOS down?")
