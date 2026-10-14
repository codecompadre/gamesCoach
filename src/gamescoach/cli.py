"""Terminal commands and an interactive console for GamesCoach."""

from __future__ import annotations

import argparse
from dataclasses import replace
from datetime import date
import json
import os
from pathlib import Path
from typing import Sequence

from rich.console import Console
from rich.markdown import Markdown
from rich.markup import escape
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from gamescoach.chat import ChatCoach, ChatContextError
from gamescoach.config import GEMINI_KEY_NAMES, OPENAI_KEY_NAMES, credential_binding
from gamescoach.engine import create_session
from gamescoach.models import AthleteProfile, CoachingSession, CoachTurn
from gamescoach.providers import ProviderError
from gamescoach.storage import Store, StoreError


KIND_LABELS = {
    "workout": "CrossFit workout",
    "training": "CrossFit training plan",
    "nutrition": "CrossFit nutrition and recovery goals",
    "competition": "CrossFit competition preparation",
    "chat": "CrossFit coach chat",
}
DEFAULT_REQUESTS = {
    "workout": "Create a balanced CrossFit workout with warm-up, scaling, and cooldown.",
    "training": "Create a progressive CrossFit training plan with recovery and measurable goals.",
    "nutrition": "Suggest practical nutrition goals that support CrossFit training and recovery.",
    "competition": "Create a CrossFit competition preparation plan with pacing, skills, and tapering.",
}
PROFILE_LABELS = {
    "name": "Name",
    "experience": "CrossFit experience",
    "goals": "Goals",
    "training_days_per_week": "Training days per week",
    "session_minutes": "Minutes per session",
    "equipment": "Available equipment",
    "limitations": "Injuries, limitations, or movements to avoid",
    "competition_date": "Competition date (YYYY-MM-DD)",
    "age": "Age (optional)",
    "bodyweight_kg": "Bodyweight in kg (optional)",
    "dietary_preferences": "Dietary preferences or restrictions",
}


def _bounded_integer(minimum: int, maximum: int):
    def parse(value: str) -> int:
        try:
            parsed = int(value)
        except ValueError as exc:
            raise argparse.ArgumentTypeError("must be a whole number") from exc
        if not minimum <= parsed <= maximum:
            raise argparse.ArgumentTypeError(f"must be between {minimum} and {maximum}")
        return parsed

    return parse


def _nonempty(value: str) -> str:
    value = value.strip()
    if not value:
        raise argparse.ArgumentTypeError("must contain some text")
    return value


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="gamescoach",
        description="CrossFit athlete coaching with ChatGPT and Gemini: workouts, competition, nutrition, and recovery. Run without a command for the console.",
    )
    parser.add_argument(
        "--demo", action="store_true", help="use static sample plans without calling either API"
    )
    commands = parser.add_subparsers(dest="command")

    plan = commands.add_parser("plan", help="ask the two coaches to create a plan")
    plan.add_argument("--kind", choices=tuple(DEFAULT_REQUESTS), default="workout")
    plan.add_argument("--request", required=True, type=_nonempty, help="what you want the coaches to plan")
    plan.add_argument("--rounds", type=_bounded_integer(1, 3), default=2, help="discussion rounds (1–3; default: 2)")
    plan.add_argument("--demo", dest="plan_demo", action="store_true", help="use a static sample without API calls")
    plan.add_argument("--show-debate", action="store_true", help="print the coaches' discussion before the final plan")

    chat = commands.add_parser("chat", help="talk with your CrossFit coach using your profile and saved entries")
    chat.add_argument("--demo", dest="chat_demo", action="store_true", help="use static sample replies without API calls")
    chat.add_argument("--rounds", type=_bounded_integer(1, 3), default=1, help="discussion rounds per reply (1–3; default: 1)")
    chat.add_argument("--show-debate", action="store_true", help="print the coaches' discussion before each final reply")
    chat.add_argument("--resume", metavar="ID", help="continue a saved chat from history")
    chat.add_argument("--context-limit", type=_bounded_integer(0, 5), default=5, help="recent saved entries for new chats (0–5; default: 5)")

    profile = commands.add_parser("profile", help="edit, show, or import your athlete profile")
    profile.add_argument("action", nargs="?", choices=("edit", "show"), default="edit")
    profile.add_argument("--file", type=Path, help="import an athlete profile from a JSON file")

    history = commands.add_parser("history", help="list saved plans and chats")
    history.add_argument("--limit", type=_bounded_integer(1, 100), default=10)

    show = commands.add_parser("show", help="read a saved plan or complete chat")
    show.add_argument("id", help="saved session ID")
    show.add_argument("--transcript", action="store_true", help="include the coaches' discussion")

    export = commands.add_parser("export", help="export a saved plan or complete chat as Markdown")
    export.add_argument("id", help="saved session ID")
    export.add_argument("--output", required=True, type=Path, help="destination Markdown file")
    export.add_argument("--force", action="store_true", help="replace an existing destination file")

    commands.add_parser("doctor", help="check API key presence without showing values or making requests")
    return parser


def _render_turn(console: Console, turn: CoachTurn) -> None:
    console.print()
    console.print(Text(f"{turn.provider} · {turn.stage}", style="bold cyan"))
    console.print(Markdown(turn.content))


def _render_chat_messages(console: Console, session: CoachingSession) -> None:
    for message in session.messages:
        console.print()
        console.print(Text("You" if message.role == "user" else "Coach", style="bold cyan" if message.role == "user" else "bold green"))
        if message.role == "user":
            console.print(Text(message.content))
        else:
            console.print(Markdown(message.content))


def _render_session(console: Console, session: CoachingSession, *, transcript: bool = False, saved: bool = True) -> None:
    if session.demo:
        console.print("[yellow]DEMO: static illustrative sample; no live API requests or personal customization.[/yellow]")
    if transcript:
        for turn in session.turns:
            _render_turn(console, turn)
    if session.kind == "chat":
        _render_chat_messages(console, session)
    else:
        console.print()
        console.print(Text(KIND_LABELS.get(session.kind, session.kind), style="bold green"))
        console.print(Markdown(session.final_plan))
    console.print()
    if saved:
        console.print(Text(f"Saved session: {session.id}", style="dim"))
    else:
        console.print("[yellow]The generated plan could not be saved. You can copy it from this terminal.[/yellow]")


def _run_plan(
    console: Console,
    store: Store,
    *,
    kind: str,
    request: str,
    rounds: int = 2,
    demo: bool = False,
    show_debate: bool = False,
) -> None:
    profile = store.load_profile()
    if demo:
        console.print("[yellow]Running the static demo. ChatGPT and Gemini APIs will not be called.[/yellow]")
    else:
        if profile is None:
            console.print("[dim]No athlete profile saved. The coaches will use only your request; run 'gamescoach profile' to add context.[/dim]")
        console.print("[dim]ChatGPT and Gemini are drafting, reviewing, and refining your plan…[/dim]")
    session = create_session(
        kind=kind,
        request=request,
        profile=profile or AthleteProfile(),
        rounds=rounds,
        demo=demo,
        on_turn=(lambda turn: _render_turn(console, turn)) if show_debate else None,
    )
    try:
        store.save_session(session)
    except StoreError:
        _render_session(console, session, saved=False)
        raise
    _render_session(console, session)


def _show_profile(console: Console, profile: AthleteProfile | None) -> None:
    if profile is None:
        console.print("No profile saved. Run 'gamescoach profile' to create one.")
        return
    table = Table(title="Athlete profile", show_header=False, expand=False)
    table.add_column(style="cyan")
    table.add_column()
    for key, value in profile.to_dict().items():
        table.add_row(PROFILE_LABELS.get(key, key), Text(str(value) if value is not None and value != "" else "—"))
    console.print(table)


def _prompt_value(console: Console, label: str, current: object, *, optional: bool = True) -> str:
    hint = f" [{escape(str(current))}]" if current is not None and current != "" else " [optional]" if optional else ""
    entered = console.input(f"{escape(label)}{hint}: ").strip()
    if entered == "-" and optional:
        return ""
    if not entered:
        return str(current) if current is not None else ""
    return entered


def _prompt_number(
    console: Console,
    label: str,
    current: int | float | None,
    *,
    floating: bool = False,
    minimum: int = 1,
    maximum: int | None = None,
) -> int | float | None:
    while True:
        value = _prompt_value(console, label, current)
        if not value:
            return None
        try:
            parsed = float(value) if floating else int(value)
            if parsed < minimum or (maximum is not None and parsed > maximum):
                raise ValueError
            # Reject infinities and NaN without depending on model validation.
            if floating and not (0 < parsed < float("inf")):
                raise ValueError
            return parsed
        except ValueError:
            constraint = f" between {minimum} and {maximum}" if maximum is not None else f" of at least {minimum}"
            console.print(f"[red]Enter a {'number' if floating else 'whole number'}{constraint}, or '-' to clear.[/red]")


def _prompt_date(console: Console, current: str) -> str:
    while True:
        value = _prompt_value(console, PROFILE_LABELS["competition_date"], current)
        if not value:
            return ""
        try:
            if date.fromisoformat(value).isoformat() != value:
                raise ValueError
            return value
        except ValueError:
            console.print("[red]Enter a date using YYYY-MM-DD, or '-' to clear.[/red]")


def _edit_profile(console: Console, store: Store) -> None:
    current = store.load_profile() or AthleteProfile()
    values = current.to_dict()
    console.print("[bold]Athlete profile[/bold]")
    console.print("Press Enter to keep a value. Type '-' to clear an optional field. Personal stats are optional.")
    for key in ("name", "experience", "goals"):
        values[key] = _prompt_value(console, PROFILE_LABELS[key], values.get(key), optional=False)
    values["training_days_per_week"] = _prompt_number(console, PROFILE_LABELS["training_days_per_week"], current.training_days_per_week, maximum=7)
    values["session_minutes"] = _prompt_number(console, PROFILE_LABELS["session_minutes"], current.session_minutes, minimum=5, maximum=300)
    for key in ("equipment", "limitations"):
        values[key] = _prompt_value(console, PROFILE_LABELS[key], values.get(key))
    values["competition_date"] = _prompt_date(console, current.competition_date)
    values["age"] = _prompt_number(console, PROFILE_LABELS["age"], current.age, maximum=120)
    values["bodyweight_kg"] = _prompt_number(console, PROFILE_LABELS["bodyweight_kg"], current.bodyweight_kg, floating=True, maximum=600)
    values["dietary_preferences"] = _prompt_value(console, PROFILE_LABELS["dietary_preferences"], current.dietary_preferences)
    profile = AthleteProfile.from_dict(values)
    store.save_profile(profile)
    console.print("[green]Athlete profile saved locally.[/green]")
    _show_profile(console, profile)


def _import_profile(console: Console, store: Store, path: Path) -> None:
    try:
        data = json.loads(path.expanduser().read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Profile file is not valid JSON: {exc.msg} (line {exc.lineno}).") from exc
    if not isinstance(data, dict):
        raise ValueError("Profile JSON must be an object containing athlete profile fields.")
    profile = AthleteProfile.from_dict(data)
    store.save_profile(profile)
    console.print("[green]Athlete profile imported and saved locally.[/green]")
    _show_profile(console, profile)


def _history(console: Console, store: Store, limit: int = 10) -> None:
    sessions = store.list_sessions(limit=limit)
    if not sessions:
        console.print("No saved plans or chats yet. Try 'gamescoach chat --demo' or 'gamescoach plan --demo --request \"Create a workout\"'.")
        return
    table = Table(title="Saved plans and chats")
    table.add_column("Session ID", style="cyan", no_wrap=True)
    table.add_column("Created", no_wrap=True)
    table.add_column("Kind")
    table.add_column("Mode", no_wrap=True)
    table.add_column("Request", max_width=60, overflow="ellipsis")
    for session in sessions:
        table.add_row(
            Text(session.id),
            Text(session.created_at),
            Text(session.kind),
            "Demo" if session.demo else "Live",
            Text(session.request),
        )
    console.print(table)


def _export(console: Console, session: CoachingSession, path: Path, force: bool) -> None:
    path = path.expanduser()
    contents = (
        f"# GamesCoach: {KIND_LABELS.get(session.kind, session.kind)}\n\n"
        f"Session: {session.id}\n\nCreated: {session.created_at}\n\n"
        f"Mode: {'Static demo (not personally customized)' if session.demo else 'Live coaching'}\n\n"
    )
    if session.kind == "chat":
        contents += "## Conversation\n\n"
        contents += "\n\n".join(
            f"### {'You' if message.role == 'user' else 'Coach'}\n\n{message.content}"
            for message in session.messages
        ) + "\n"
    else:
        contents += f"## Request\n\n{session.request}\n\n## Plan\n\n{session.final_plan}\n"
    try:
        with path.open("w" if force else "x", encoding="utf-8") as output:
            output.write(contents)
    except FileExistsError as exc:
        raise ValueError(f"Destination already exists: {path}. Use --force to replace it.") from exc
    console.print(Text(f"Exported {'chat' if session.kind == 'chat' else 'plan'} to {path}", style="green"))


def _doctor(console: Console) -> int:
    providers = (
        ("ChatGPT", OPENAI_KEY_NAMES, "OPENAI_MODEL", "gpt-5-mini"),
        ("Gemini", GEMINI_KEY_NAMES, "GEMINI_MODEL", "gemini-2.5-flash"),
    )
    table = Table(title="API configuration (presence only)")
    table.add_column("Coach")
    table.add_column("API key")
    table.add_column("Model")
    ready = True
    for label, names, model_var, default in providers:
        binding = credential_binding(names)
        configured = binding is not None
        ready = ready and configured
        status = f"Configured: {binding[0]}" if configured else "Missing"
        model = os.environ.get(model_var, default) or "(empty override)"
        table.add_row(label, Text(status, style="green" if configured else "yellow"), Text(model))
    console.print(table)
    console.print("[dim]This checks environment variable presence only; API connectivity, credentials, and model access were not tested.[/dim]")
    if not ready:
        console.print("Set OPENAI_API_KEY and GEMINI_API_KEY in your environment to enable live coaching. The --demo option works without keys.")
    console.print("[dim]Live coaching sends your saved profile and request to both providers.[/dim]")
    notes_path = os.environ.get("GAMESCOACH_COACH_NOTES_FILE")
    if notes_path:
        console.print(Text(f"Optional CrossFit coach notes configured: {notes_path}"))
    else:
        console.print("[dim]Optional CrossFit coach notes: not configured (GAMESCOACH_COACH_NOTES_FILE).[/dim]")
    return 0 if ready else 1


def _error(console: Console, exc: BaseException) -> None:
    console.print(f"[red]Error:[/red] {escape(str(exc))}", highlight=False)


def _chat_help(console: Console, *, from_menu: bool) -> None:
    destination = "return to the menu" if from_menu else "return to the shell"
    console.print(f"Type a CrossFit coaching question. /quit (or / quit), /back, and /exit {destination}.")
    console.print("/help  Show commands    /history  List saved plans and chats    /profile  Read your saved profile")
    console.print("/read ID  Read and add a saved entry to coaching context    /new  Start a fresh conversation")
    console.print("/clearrefs  Remove saved entries from coaching context; your profile and current chat remain")


def _chat_context(console: Console, context: list[CoachingSession]) -> None:
    if not context:
        console.print("[dim]No saved entries loaded as coaching context. Use /read ID to add one.[/dim]")
        return
    console.print("[dim]Saved entries loaded as coaching context:[/dim]")
    for session in context:
        mode = "static demo" if session.demo else "live"
        console.print(Text(f"  {session.id} · {session.kind} · {mode}"))


def _slash_command(message: str) -> tuple[str, str] | None:
    if not message.startswith("/"):
        return None
    parts = message[1:].split(None, 1)
    return (parts[0].lower(), parts[1].strip() if len(parts) > 1 else "") if parts else ("", "")


def _save_chat_references(console: Console, store: Store, coach: ChatCoach) -> None:
    if coach.session is None:
        return
    references = [entry.id for entry in coach.context]
    if references == coach.session.context_ids:
        return
    snapshot = replace(coach.session, context_ids=references)
    coach.session = snapshot
    try:
        store.save_chat(snapshot)
    except StoreError as exc:
        _error(console, exc)
        console.print("[yellow]These context changes are unsaved. They remain active in memory; previously saved replies are preserved.[/yellow]")
    else:
        console.print(Text(f"Saved chat context: {snapshot.id}", style="dim"))


def _chat(
    console: Console,
    store: Store,
    *,
    demo: bool = False,
    rounds: int = 1,
    show_debate: bool = False,
    resume: str | None = None,
    from_menu: bool = False,
    context_limit: int = 5,
) -> None:
    profile = store.load_profile() or AthleteProfile()
    session = store.get_session(resume) if resume else None
    context = []
    if session is not None:
        for context_id in session.context_ids:
            if context_id == resume:
                continue
            try:
                context.append(store.get_session(context_id))
            except StoreError as exc:
                console.print(Text(f"Previously loaded context {context_id} is unavailable: {exc}", style="yellow"))
    if session is None and context_limit:
        recent = [entry for entry in store.list_sessions(limit=context_limit + 1) if entry.id != resume][:context_limit]
        known = {entry.id for entry in context}
        context.extend(entry for entry in recent if entry.id not in known)
    try:
        coach = ChatCoach(profile, context, rounds=rounds, demo=demo, session=session)
    except ChatContextError as exc:
        if not from_menu or not context:
            raise
        _error(console, exc)
        console.print("[yellow]Starting chat without saved entries because they could not be loaded together. Use /read ID to add individual entries.[/yellow]")
        coach = ChatCoach(profile, [], rounds=rounds, demo=demo, session=session)
    console.print(Panel("[bold]CrossFit coach chat[/bold]\nTalk about workouts, training, competition, nutrition, and recovery.", border_style="cyan"))
    if demo:
        console.print("[yellow]DEMO: static sample replies; no API calls or personal customization.[/yellow]")
    else:
        console.print("[dim]Live chat sends your athlete profile, conversation, and selected saved entries to BOTH ChatGPT and Gemini.[/dim]")
    console.print(Text(f"Athlete profile loaded: {profile.name}. Use /profile to review it."))
    _chat_context(console, coach.context)
    _chat_help(console, from_menu=from_menu)
    if session is not None:
        console.print(Text(f"Resuming chat: {session.id}", style="bold"))
        _render_chat_messages(console, session)

    while True:
        try:
            message = console.input("\n[bold cyan]You> [/bold cyan]").strip()
        except (EOFError, KeyboardInterrupt):
            console.print("\nReturning to the menu." if from_menu else "\nChat ended. Returning to the shell.")
            return
        if not message:
            continue
        command = _slash_command(message)
        if command is not None:
            name, argument = command
            if name in ("quit", "back", "exit") and not argument:
                console.print("Returning to the menu." if from_menu else "Chat ended. Returning to the shell.")
                return
            try:
                if name == "help" and not argument:
                    _chat_help(console, from_menu=from_menu)
                elif name == "history" and not argument:
                    _history(console, store)
                elif name == "profile" and not argument:
                    _show_profile(console, store.load_profile())
                elif name == "read" and argument:
                    entry = store.get_session(argument)
                    if coach.session is not None and entry.id == coach.session.id:
                        console.print("This chat is already the active conversation.")
                    else:
                        coach.add_context(entry)
                        console.print(Text(f"Added coaching context: {entry.id} · {entry.kind}", style="green"))
                        _save_chat_references(console, store, coach)
                    _render_session(console, entry)
                elif name == "new" and not argument:
                    coach = ChatCoach(profile, list(coach.context), rounds=rounds, demo=demo)
                    console.print("Started a new chat. Your athlete profile and selected saved entries are still loaded; previous chats remain in history.")
                elif name == "clearrefs" and not argument:
                    coach.clear_context()
                    console.print("Removed saved entries from coaching context. Your athlete profile and current conversation are still loaded.")
                    _save_chat_references(console, store, coach)
                else:
                    console.print("Unknown chat command. Use /help, or /read ID to load an entry.")
            except (ProviderError, StoreError, ValueError, OSError) as exc:
                _error(console, exc)
            continue

        try:
            if not demo:
                console.print("[dim]ChatGPT and Gemini are reviewing your question…[/dim]")
            completed = coach.reply(
                message,
                on_turn=(lambda turn: _render_turn(console, turn)) if show_debate else None,
            )
        except KeyboardInterrupt:
            console.print("\nReturning to the menu." if from_menu else "\nChat cancelled. Returning to the shell.")
            return
        except (ProviderError, StoreError, ValueError, OSError) as exc:
            _error(console, exc)
            console.print("[dim]Your previous conversation is unchanged. You can retry or use /quit.[/dim]")
            continue

        console.print()
        console.print(Text("Coach", style="bold green"))
        console.print(Markdown(completed.final_plan))
        try:
            store.save_chat(completed)
        except StoreError as exc:
            _error(console, exc)
            console.print("[yellow]This reply is unsaved. You can continue in memory or copy it from this terminal.[/yellow]")
        else:
            console.print(Text(f"Saved chat: {completed.id}", style="dim"))


def _interactive(console: Console, store: Store, *, demo: bool) -> int:
    console.print(Panel("[bold]GamesCoach[/bold]\nChatGPT + Gemini · Your personal CrossFit athlete coach\nWorkouts, competition preparation, nutrition, and recovery for CrossFit.", border_style="cyan"))
    if demo:
        console.print("[yellow]Demo mode: static illustrative samples, no API calls.[/yellow]")
    console.print("[dim]Plans are guidance. Scale for your experience, recovery, and limitations.[/dim]")
    while True:
        console.print()
        console.print("[bold]1[/bold] Workout  [bold]2[/bold] Training plan  [bold]3[/bold] Nutrition  [bold]4[/bold] Competition")
        console.print("[bold]5[/bold] Athlete profile  [bold]6[/bold] History  [bold]7[/bold] API configuration  [bold]8[/bold] Chat  [bold]q[/bold] Quit")
        try:
            choice = console.input("Choose: ").strip().lower()
        except EOFError:
            console.print()
            return 0
        if choice in ("q", "quit", "exit") or _slash_command(choice) in (("quit", ""), ("exit", "")):
            return 0
        try:
            if choice in ("1", "2", "3", "4"):
                kind = tuple(DEFAULT_REQUESTS)[int(choice) - 1]
                request = _prompt_value(console, "What would you like to create?", DEFAULT_REQUESTS[kind], optional=False)
                _run_plan(console, store, kind=kind, request=request, demo=demo, show_debate=True)
            elif choice in ("5", "profile"):
                _edit_profile(console, store)
            elif choice in ("6", "history"):
                _history(console, store)
                session_id = console.input("Session ID to open (Enter to return): ").strip()
                if session_id:
                    _render_session(console, store.get_session(session_id))
            elif choice in ("7", "doctor"):
                _doctor(console)
            elif choice in ("8", "chat"):
                _chat(console, store, demo=demo, from_menu=True)
            elif choice:
                console.print("Choose 1–8 or q to quit.")
        except EOFError:
            console.print("\nInput ended. Returning to the shell.")
            return 0
        except (ProviderError, StoreError, ValueError, OSError) as exc:
            _error(console, exc)


def main(argv: Sequence[str] | None = None) -> int:
    """Run the command line application and return a shell exit status."""
    parser = _parser()
    args = parser.parse_args(argv)
    console = Console(highlight=False)
    try:
        if args.command == "doctor":
            return _doctor(console)
        store = Store()
        if args.command is None:
            return _interactive(console, store, demo=args.demo)
        if args.command == "plan":
            _run_plan(console, store, kind=args.kind, request=args.request, rounds=args.rounds, demo=args.demo or args.plan_demo, show_debate=args.show_debate)
        elif args.command == "chat":
            _chat(console, store, demo=args.demo or args.chat_demo, rounds=args.rounds, show_debate=args.show_debate, resume=args.resume, context_limit=args.context_limit)
        elif args.command == "profile":
            if args.file:
                if args.action == "show":
                    raise ValueError("Use 'profile --file PATH' to import, or 'profile show' to display the saved profile.")
                _import_profile(console, store, args.file)
            elif args.action == "show":
                _show_profile(console, store.load_profile())
            else:
                _edit_profile(console, store)
        elif args.command == "history":
            _history(console, store, args.limit)
        elif args.command == "show":
            _render_session(console, store.get_session(args.id), transcript=args.transcript)
        elif args.command == "export":
            _export(console, store.get_session(args.id), args.output, args.force)
        return 0
    except KeyboardInterrupt:
        console.print("\n[dim]Cancelled.[/dim]")
        return 130
    except EOFError:
        console.print("\nInput ended before the profile was saved.")
        return 1
    except (ProviderError, StoreError, ValueError, OSError) as exc:
        _error(console, exc)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
