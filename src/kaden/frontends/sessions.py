"""Sub-sessions: a Tools module a frontend offers (Frontend.tools) so the agent can hand
self-contained tasks to children, headless Kaden processes it starts and drives over
their channel (kaden.frontends.headless). The frontend hosts them, as an app hosts its
sessions: it lists them from `SubSessions.sessions` and opens one with `open`, which gives
a RuntimeAPI for it (kaden.frontends.remote). No UI code here.

A child gets its own data folder (<data dir>/sessions/<conversation>-<id>) with the parent's
config.toml and settings.toml, its spending limit and the conversation's work folder, and
works on its task as a goal. Its permission prompts wait for the user, who answers them
where the child is open. Each child's record (session.json) keeps it listed after a
restart; opening a child that is not running starts it again on its conversation.

With `isolation: "worktree"` a child works in its own git worktree of the conversation's
repository, on a new branch (kaden.frontends.worktrees). When it ends, the worktree is
removed if it has no changes, and kept with its branch if it has.
"""

from __future__ import annotations

import asyncio
import contextlib
import functools
import json
import logging
import math
import shutil
import sys
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Any

from kaden.api import (
    DESCRIPTION_ARG,
    AgentState,
    Budget,
    Duty,
    DutyKind,
    EventEnvelope,
    Failure,
    Message,
    MessageCommitted,
    PromptKind,
    ReasoningDelta,
    Refusal,
    Role,
    RuntimeSnapshot,
    RuntimeStateError,
    SettingsUpdate,
    StartOptions,
    StateChanged,
    Text,
    TextDelta,
    ToolCall,
    ToolCallDelta,
    ToolContext,
    ToolOutput,
    ToolProgress,
    Tools,
    ToolSpec,
    UsageUpdated,
)
from kaden.frontends import child, worktrees
from kaden.frontends.child import STOP_GRACE_S
from kaden.frontends.remote import Channel, RemoteRuntime
from kaden.frontends.worktrees import GitError, Worktree

logger = logging.getLogger(__name__)

CHECK_IN_S = (5.0, 120.0)  # a child's duty intervals: it continues soon after a turn ends
RECORD = "session.json"
LIMIT_FILE = "limit.json"  # in <data dir>/sessions: the user's limit per sub-session
ISSUED_FILE = "issued.json"  # in <data dir>/sessions: the last id given, never given again
_SESSION = {"type": "string", "description": "The subagent's id."}
_ACTIVE = (AgentState.THINKING, AgentState.RUNNING_TOOLS, AgentState.AWAITING_PERMISSION,
           AgentState.FINISHING)
_QUIET = (TextDelta, ReasoningDelta, ToolCallDelta, ToolProgress)

START = ToolSpec(
    "Agent",
    "Hand a self-contained task to a subagent: a separate agent with its own context, "
    "working in the same folder with your tools, so long side work does not fill your "
    "conversation. `description` is 3 to 5 words; `prompt` describes the task fully: it "
    "sees nothing of this conversation. It returns the agent's report when done, or a "
    "question if it needs one. With `run_in_background` you get on with other work and "
    "are told the outcome when it ends; several can run at once. The user can open and "
    "talk to each. Once the call has returned, you get a notice whenever it stops "
    "working. With `isolation: \"worktree\"` it works in its own git worktree of the "
    "repository, on a new branch, so its changes do not collide with yours or other "
    "subagents'; a worktree it left unchanged is removed when it ends, and the result "
    "names the path and branch of one it changed. Start one when the user asked for the "
    "work; when it is your own idea, propose it (propose) instead.",
    {"type": "object",
     "properties": {
         "description": {"type": "string", "minLength": 1},
         "prompt": {"type": "string", "minLength": 1},
         "run_in_background": {"type": "boolean"},
         "budget_usd": {"type": "number", "exclusiveMinimum": 0,
                        "description": "The most it may spend, in US dollars. Leave it "
                                       "out unless the task calls for a limit; the "
                                       "user's limit per sub-session, where they set "
                                       "one, applies anyway."},
         "isolation": {"type": "string", "enum": ["worktree"],
                       "description": "\"worktree\": a git worktree of its own. The "
                                      "folder must be in a git repository."}},
     "required": ["description", "prompt"]},
    timeout=math.inf,
    foreground=True,
)
MESSAGE = ToolSpec(
    "SendMessage",
    "Answer a subagent's question, or tell it more (starting it again if it stopped), "
    "then wait for its report or next question. To stop one, use TaskStop on the call "
    "that waits for it.",
    {"type": "object", "properties": {"to": _SESSION, "message": {"type": "string",
                                                                   "minLength": 1}},
     "required": ["to", "message"]},
    timeout=math.inf,
)

STATE_LABELS = {"starting": "Starting", "working": "Working",
                "waiting": "Waiting for an answer", "idle": "Ready", "done": "Done",
                "failed": "Failed", "stopped": "Stopped"}
NOTE_CHARS = 400  # of a child's last reply in a notice to its parent


@dataclass
class SubSession:
    """One child as the frontend lists it. `state`: starting, working, waiting (for an
    answer to its question), idle (reopened, nothing to do), done, failed or stopped."""

    id: str
    task: str
    conversation_id: str  # the parent conversation
    data_dir: Path
    state: str = "starting"
    report: str | None = None
    question: str | None = None
    failure: str | None = None
    spent: float = 0.0
    child_conversation_id: str | None = None
    title: str | None = None  # the user's name for it; None: its task
    archived: bool = False
    has_goal: bool = False  # its goal is not reached yet
    budget_usd: float | None = None  # its own spending limit; None: none
    worktree: Worktree | None = None  # with isolation "worktree"
    step: str | None = field(default=None, compare=False)  # what it is doing now
    messages: list[Message] = field(default_factory=list, compare=False)
    returncode: int | None = field(default=None, compare=False)
    awaited: int = field(default=0, compare=False)  # parent calls waiting on it
    # Its manager's RuntimeAPI for it while its process runs: how it is now is read there.
    live: RemoteRuntime | None = field(default=None, compare=False, repr=False)
    _changed: asyncio.Event | None = field(default=None, compare=False, repr=False)
    _spawning: asyncio.Lock | None = field(default=None, compare=False, repr=False)

    @property
    def running(self) -> bool:
        return self.state in ("starting", "working", "waiting")

    @property
    def snapshot(self) -> RuntimeSnapshot | None:
        """Its runtime's snapshot, kept current; None while it is not running, or has not
        answered yet."""
        return self.live.snapshot() if self.live is not None and self.live.attached else None

    @property
    def approvals(self) -> int:
        """What waits on the person in it: its prompts open, proposals aside."""
        snap = self.snapshot
        return sum(p.kind != PromptKind.PROPOSAL for p in snap.prompts) if snap else 0

    @property
    def agent_state(self) -> AgentState | None:
        return snap.state if (snap := self.snapshot) is not None else None

    @property
    def name(self) -> str:
        """Its title, else its task's first line: the brief below it is for the agent."""
        return self.title or next((line.strip() for line in self.task.splitlines()
                                   if line.strip()), self.task)

    @property
    def resumable(self) -> bool:
        """Stopped, or ended with Kaden, before its goal was reached."""
        return self.state in ("stopped", "failed", "idle") and self.has_goal

    @property
    def changed(self) -> asyncio.Event:
        """Set on every change; a waiter clears it before it looks again."""
        if self._changed is None:
            self._changed = asyncio.Event()
        return self._changed

    @property
    def spawning(self) -> asyncio.Lock:
        """Held while its process is started again and attached: one process at a time."""
        if self._spawning is None:
            self._spawning = asyncio.Lock()
        return self._spawning

    @property
    def last_reply(self) -> str:
        """Its last reply this run, cut to NOTE_CHARS; "" for none."""
        reply = next((b.text for m in reversed(self.messages) if m.role == Role.ASSISTANT
                      for b in m.blocks if isinstance(b, Text) and b.text.strip()), "")
        return reply[:NOTE_CHARS] + "…" if len(reply) > NOTE_CHARS else reply

    @property
    def summary(self) -> str:
        """Its state for a list row: "Working · Searching the web · $0.04"."""
        parts = ["Needs your approval" if self.approvals else STATE_LABELS[self.state]]
        if self.state == "working" and self.step and not self.approvals:
            parts.append(self.step)
        if self.state == "failed" and self.failure:
            parts.append(self.failure)
        parts.append(f"${self.spent:.2f}" + (f" of ${self.budget_usd:g}"
                                             if self.budget_usd is not None else ""))
        return " · ".join(parts)

    def record(self) -> dict[str, Any]:
        """What session.json keeps."""
        return {**{k: getattr(self, k) for k in (
            "id", "task", "conversation_id", "state", "report", "question", "failure",
            "spent", "child_conversation_id", "title", "archived", "has_goal", "budget_usd")},
            "worktree": asdict(self.worktree) if self.worktree is not None else None}


class SubSessions(Tools):
    def __init__(self, data_dir: Path, command: Sequence[str] = (sys.executable, "-m", "kaden"),
                 check_in_s: tuple[float, float] = CHECK_IN_S) -> None:
        self.data_dir = data_dir
        self.command = list(command)
        self.check_in_s = check_in_s
        self.listeners: list[Callable[[], None]] = []  # told of every change
        self.sessions = self._load()
        self._viewers: dict[str, int] = {}
        self._folder: Path | None = None
        self._next = max((int(s.id) for s in self.sessions if s.id.isdigit()),
                         default=0) + 1
        self._next = max(self._next, self._read_issued() + 1)
        self.limit_usd = self._read_limit()
        self._closing: set[asyncio.Task[None]] = set()
        self._ending: dict[str, asyncio.Task[None]] = {}  # a child's _ended, while it runs
        self._git = asyncio.Lock()  # one worktree added or removed at a time
        self._saved: dict[str, dict[str, Any]] = {s.id: s.record() for s in self.sessions}
        self._write: asyncio.Future[None] | None = None
        # Notices for parent conversations: a child ended a turn nobody was waiting for.
        self.notes: dict[str, list[str]] = {}

    def of(self, conversation_id: str | None, *, include_archived: bool = False
           ) -> list[SubSession]:
        return [s for s in self.sessions if s.conversation_id == conversation_id
                and (include_archived or not s.archived)]

    def take_notes(self, conversation_id: str) -> list[str]:
        """The notices waiting for this conversation; the frontend writes them into it
        (RuntimeAPI.notify)."""
        return self.notes.pop(conversation_id, [])

    # ── For frontends ─────────────────────────────────────────────────────

    async def open(self, session: SubSession) -> RemoteRuntime:
        """A RuntimeAPI for the child, starting it again when it is not running; call
        release() with it when the view closes."""
        if session.child_conversation_id is None:
            raise RuntimeStateError("this sub-session has not started", Refusal.NOT_STARTED)
        live, started = await self._running(session)
        if started and not session.running:  # nothing else set it working meanwhile
            self._set(session, state="idle", failure=None)
        self._viewers[session.id] = self._viewers.get(session.id, 0) + 1
        remote = RemoteRuntime(live.channel)
        try:
            return await remote.attach()
        except BaseException:  # cancelled too: the view never got it
            self.release(session, remote)
            raise

    def release(self, session: SubSession, runtime: RemoteRuntime) -> None:
        runtime.detach()
        self._viewers[session.id] -= 1
        if not self._viewers[session.id] and not session.running:
            self._close(session)

    def stop(self, session: SubSession) -> None:
        """Stop a child: the frontend's Stop, and TaskStop on a call that waits for it."""
        if session.running:
            self._set(session, state="stopped")
        self._close(session)

    async def resume(self, session: SubSession) -> None:
        """Start it again on its conversation and let it carry on with its goal."""
        live, _ = await self._running(session)
        self._set(session, state="working", failure=None)
        await live.activate()

    def rename(self, session: SubSession, title: str) -> None:
        self._set(session, title=title.strip() or None)

    def archive(self, session: SubSession, archived: bool) -> None:
        self._set(session, archived=archived)

    async def delete_of(self, conversation_id: str) -> None:
        """Delete every sub-session of a conversation: it is being deleted."""
        for session in self.of(conversation_id, include_archived=True):
            await self.delete(session)

    async def delete(self, session: SubSession) -> None:
        """Stop it and erase its folder for good; a worktree it changed is kept."""
        if (channel := self._unhear(session)) is not None:
            await channel.close(STOP_GRACE_S)
        if (ending := self._ending.get(session.id)) is not None:
            await asyncio.gather(ending, return_exceptions=True)
        await self._tidy(session)
        if self._write is not None:
            await asyncio.gather(self._write, return_exceptions=True)
        await asyncio.to_thread(shutil.rmtree, session.data_dir, True)
        self.sessions.remove(session)
        self._notify()

    # ── Tools ─────────────────────────────────────────────────────────────

    async def list_tools(self) -> Sequence[ToolSpec]:
        if self.limit_usd is None:
            return (START, MESSAGE)
        start = replace(START, description=(
            f"{START.description} Each may spend up to ${self.limit_usd:g} unless you give "
            "it less."))
        return (start, MESSAGE)

    async def set_limit(self, usd: float | None) -> None:
        """The user's limit per sub-session: what one gets unless the agent gives it less.
        None: no limit."""
        if usd is not None and not usd > 0:
            raise ValueError("the limit per sub-session must be more than $0")
        self.limit_usd = usd
        path = self.data_dir / "sessions" / LIMIT_FILE
        await asyncio.to_thread(path.parent.mkdir, parents=True, exist_ok=True)
        await asyncio.to_thread(path.write_text, json.dumps({"usd": usd}))
        self._notify()

    async def set_child_limit(self, session: SubSession, usd: float | None) -> None:
        """Change one child's spending limit (None: remove it), while it runs or not. A
        child stopped by its limit can then be resumed."""
        if usd is not None and not usd > 0:
            raise ValueError("the limit must be more than $0")
        if session.live is not None:
            await session.live.update_settings(SettingsUpdate(budget=Budget(max_cost_usd=usd)))
        else:
            await asyncio.to_thread(self._write_limit, session.data_dir, usd)
        self._set(session, budget_usd=usd)

    def _read_limit(self) -> float | None:
        try:
            usd = float(json.loads((self.data_dir / "sessions" / LIMIT_FILE).read_text())["usd"])
        except (OSError, ValueError, KeyError, TypeError):
            return None
        return usd if usd > 0 else None

    @staticmethod
    def _write_limit(folder: Path, usd: float | None) -> None:
        settings_file = folder / "settings.toml"
        settings = child.read_settings(settings_file)
        budget = {k: v for k, v in settings.get("budget", {}).items() if k != "max_cost_usd"}
        settings["budget"] = budget if usd is None else {**budget, "max_cost_usd": usd}
        child.write_settings(settings_file, settings)

    def _read_issued(self) -> int:
        try:
            return int(json.loads((self.data_dir / "sessions" / ISSUED_FILE).read_text()))
        except (OSError, ValueError, TypeError):
            return 0

    async def use_folder(self, folder: Path | None) -> None:
        self._folder = folder

    async def execute(self, call: ToolCall, ctx: ToolContext) -> ToolOutput:
        args = call.arguments
        if call.name == START.name:
            budget = args.get("budget_usd", self.limit_usd)
            if budget is not None and self.limit_usd is not None:
                budget = min(budget, self.limit_usd)
            worktree = None
            if args.get("isolation") == "worktree":
                if self._folder is None:
                    return ToolOutput("Isolation \"worktree\" needs the conversation's "
                                      "folder to be in a git repository; it has none.",
                                      is_error=True)
                try:
                    worktree = await worktrees.plan(
                        self._folder, f"agent-{ctx.conversation_id[:8]}-{self._next}")
                except GitError as e:
                    return ToolOutput(f"Isolation \"worktree\" needs a git repository: "
                                      f"{e}.", is_error=True)
            session = await self._new(args["prompt"], budget, ctx.conversation_id,
                                      args.get("description"), worktree)
        else:
            session = next((s for s in self.of(ctx.conversation_id, include_archived=True)
                            if s.id == args["to"]), None)
            if session is None:
                return ToolOutput(f"There is no subagent {args['to']!r}.", is_error=True)
            if session.child_conversation_id is None:
                return ToolOutput(f"Sub-session {session.id} never started.", is_error=True)
        try:
            if call.name == START.name:
                await self._launch(session)
            else:
                live, _ = await self._running(session)
                self._set(session, state="working", failure=None)
                await live.send(args["message"])
            return await self._wait(session, ctx)
        except asyncio.CancelledError:  # the call was cancelled, wherever it had got to
            self.stop(session)
            raise

    async def aclose(self) -> None:
        for session in self.sessions:
            self.stop(session)
        if self._closing:
            await asyncio.wait(self._closing)
        if self._write is not None:
            await asyncio.gather(self._write, return_exceptions=True)

    # ── Children ──────────────────────────────────────────────────────────

    async def _new(self, task: str, budget: float | None, conversation_id: str,
                   title: str | None = None, worktree: Worktree | None = None) -> SubSession:
        session_id = str(self._next)
        self._next += 1
        folder = self.data_dir / "sessions" / f"{conversation_id[:8]}-{session_id}"
        session = SubSession(session_id, task, conversation_id, folder, has_goal=True,
                             budget_usd=budget, title=(title or "").strip() or None,
                             worktree=worktree)

        def make() -> None:
            folder.mkdir(parents=True)
            (folder.parent / ISSUED_FILE).write_text(session_id)
        await asyncio.to_thread(make)
        self.sessions.append(session)
        self._set(session)
        return session

    async def _launch(self, session: SubSession) -> None:
        self._set(session, state="starting")
        try:
            live = await self._spawn(session, resume=False)
            session.child_conversation_id = (await live.start(StartOptions())).id
            await live.set_duty(Duty(session.task, DutyKind.GOAL, *self.check_in_s))
            await live.attach()
        except (OSError, RuntimeError, RuntimeStateError, ValueError) as e:
            channel = session.live.channel if session.live is not None else None
            self._set(session, state="failed", failure=self._why(e, channel))
            self._close(session)
            return
        self._follow(session)
        self._set(session)

    def _work_folder(self, session: SubSession) -> Path | None:
        """Where it works: its worktree's folder, else the conversation's."""
        return Path(session.worktree.folder) if session.worktree is not None else self._folder

    def _prepare(self, session: SubSession) -> None:
        """The child's data folder: the parent's configuration, its limit and folder."""
        folder, budget = session.data_dir, session.budget_usd
        settings = child.settings_of(self.data_dir, folder)
        # Only its own limit, if it has one: not the parent's.
        settings["budget"] = {} if budget is None else {"max_cost_usd": budget}
        if (work_folder := self._work_folder(session)) is not None:
            settings["work_folder"] = str(work_folder)
        child.write_settings(folder / "settings.toml", settings)

    async def _running(self, session: SubSession) -> tuple[RemoteRuntime, bool]:
        """Its runtime, and whether this call started it again on its conversation (it
        was not running)."""
        async with session.spawning:
            started = session.live is None
            live = session.live or await self._spawn(session, resume=True)
            if not live.attached:
                await live.attach()
            return live, started

    async def _spawn(self, session: SubSession, *, resume: bool) -> RemoteRuntime:
        """Start its process: first its data folder, or its worktree again when its
        unchanged one was removed. Under the worktree lock, so no tidying removes the
        worktree before the process holds it (session.live)."""
        async with self._git:
            if (worktree := session.worktree) is not None and not await asyncio.to_thread(
                    Path(worktree.path).exists):
                await worktrees.add(worktree)
            if not resume:
                await asyncio.to_thread(self._prepare, session)
            live = session.live = await child.spawn(
                self.command, session.data_dir,
                cwd=self._work_folder(session) or session.data_dir,
                conversation_id=session.child_conversation_id if resume else None,
                on_event=functools.partial(self._event, session))
        return live

    async def _wait(self, session: SubSession, ctx: ToolContext) -> ToolOutput:
        """Until it reports, asks, fails or stops; its current step is the progress line."""
        shown = None
        session.awaited += 1
        try:
            while session.state in ("starting", "working"):
                session.changed.clear()
                step = "Needs your approval" if session.approvals else session.step
                if step and step != shown:
                    shown = step
                    ctx.progress(step)
                await session.changed.wait()
        finally:
            session.awaited -= 1
        if session.state == "done" and not self._viewers.get(session.id):
            self._close(session)
        if (ending := self._ending.get(session.id)) is not None:
            await asyncio.shield(ending)  # its process quits, and its worktree is tidied
        text = self._outcome(session)
        if (worktree := session.worktree) is not None:
            if await asyncio.to_thread(Path(worktree.path).exists):
                text += f"\nIts worktree: {worktree.path}, on branch {worktree.branch}."
            else:
                text += "\nIt changed nothing, so its worktree was removed."
        return ToolOutput(text, is_error=session.state == "failed")

    def _outcome(self, session: SubSession) -> str:
        match session.state:
            case "done":
                return f"Sub-session {session.id} finished. Report: {session.report}"
            case "waiting":
                return (f"Sub-session {session.id} asks: {session.question}\nAnswer with "
                        f"SendMessage(to=\"{session.id}\", message=...).")
            case "stopped":
                return f"Sub-session {session.id} was stopped."
            case "idle":
                reply = session.last_reply
                return (f"Sub-session {session.id} ended its turn"
                        + (f". Its last reply: {reply}" if reply else " without a reply."))
        return f"Sub-session {session.id} failed: {session.failure}"

    def _event(self, session: SubSession, envelope: EventEnvelope | None) -> None:
        """From its process, while this manager listens to it (until _unhear)."""
        if envelope is None:  # the process ended by itself
            self._close(session)
            return
        if isinstance(envelope.event, _QUIET):
            return  # streaming: nothing a list shows
        match envelope.event:
            case MessageCommitted(message=message):
                session.messages.append(message)
                for block in message.blocks:
                    if isinstance(block, ToolCall):
                        if block.name == "finish_goal":
                            session.report = block.arguments.get("report")
                        session.step = block.arguments.get(DESCRIPTION_ARG) or block.name
                    elif isinstance(block, Text) and message.role == Role.ASSISTANT:
                        lines = block.text.strip().splitlines()
                        session.step = lines[0][:120] if lines else None
            case UsageUpdated(snapshot=usage):
                session.spent = usage.usage.cost_usd or 0.0
            case Failure(message=message):
                session.failure = message
            case StateChanged():
                was = session.state
                self._follow(session)
                # Between a goal's check-ins it is still working: no notice until it stops.
                if was == "working" != session.state and not session.awaited:
                    self._note(session)
        self._set(session)

    def _follow(self, session: SubSession) -> None:
        """Its state, and what its record keeps of its goal and question, from how its
        runtime is now. Nothing while it has not answered yet, or once it is closed."""
        if (snap := session.snapshot) is None:
            return
        session.has_goal = snap.conversation is not None and snap.conversation.duty is not None
        session.question = snap.help_needed
        if snap.state in _ACTIVE:
            session.state = "working"
        elif snap.state == AgentState.FAILED:
            session.state = "failed"
        elif snap.state in (AgentState.IDLE, AgentState.STOPPED):
            if session.has_goal:  # working between a goal's check-ins, unless it asked
                session.state = "waiting" if session.question is not None else "working"
            else:
                session.state = "done" if session.report is not None else "idle"

    def _note(self, session: SubSession) -> None:
        """A notice for the parent: the child ended a turn while no call of the parent's
        waited for it (the user talked to it, or it carried on with its goal)."""
        reply = session.last_reply
        note = (f"Sub-session {session.id} ({session.name[:80]}) ended a "
                f"turn: {STATE_LABELS[session.state].lower()}.")
        if session.state == "waiting":
            note += f" It asks: {session.question}"
        elif session.state == "done":
            note += f" Report: {session.report}"
        elif reply:
            note += f" Its last reply: {reply}"
        self.notes.setdefault(session.conversation_id, []).append(note)

    async def _ended(self, session: SubSession, channel: Channel) -> None:
        try:
            session.returncode = await channel.close(STOP_GRACE_S)
            if session.running:  # it ended by itself
                self._set(session, state="failed", failure=self._why(None, channel))
            await self._tidy(session)
            self._set(session)
        finally:
            if self._ending.get(session.id) is asyncio.current_task():
                del self._ending[session.id]

    async def _tidy(self, session: SubSession) -> None:
        """Remove its worktree, with its branch, if it has no changes and no process."""
        if (worktree := session.worktree) is None:
            return
        async with self._git:
            try:
                if (session.live is None
                        and await asyncio.to_thread(Path(worktree.path).exists)
                        and not await worktrees.changed(worktree)):
                    await worktrees.remove(worktree)
            except GitError as e:
                logger.warning("worktree %s not tidied: %s", worktree.path, e)

    def _unhear(self, session: SubSession) -> Channel | None:
        """Stop listening to its process, if it has one: the channel is then the caller's
        to close, and nothing it still says reaches the session."""
        live, session.live = session.live, None
        if live is None:
            return None
        live.detach()
        return live.channel

    def _close(self, session: SubSession) -> None:
        if (channel := self._unhear(session)) is not None:
            task = asyncio.create_task(self._ended(session, channel))
            self._closing.add(task)
            self._ending[session.id] = task
            task.add_done_callback(self._closing.discard)

    @staticmethod
    def _why(error: Exception | None, channel: Channel | None) -> str:
        tail = [line for line in (channel.stderr if channel else ()) if line.startswith("kaden:")]
        if tail:
            return tail[-1].removeprefix("kaden:").strip()
        return str(error) if error is not None else "the process ended"

    # ── Records and listeners ─────────────────────────────────────────────

    def _load(self) -> list[SubSession]:
        sessions = []
        for record in sorted((self.data_dir / "sessions").glob(f"*/{RECORD}")):
            try:
                data = json.loads(record.read_text())
                worktree = data.pop("worktree", None)
                session = SubSession(data_dir=record.parent, **data,
                                     worktree=Worktree(**worktree) if worktree else None)
            except (OSError, ValueError, TypeError):
                logger.warning("unreadable sub-session record %s", record)
                continue
            if session.running:  # it stopped with the last run
                session.state = "stopped"
            sessions.append(session)
        return sorted(sessions, key=lambda s: int(s.id) if s.id.isdigit() else 0)

    def _set(self, session: SubSession, **changes: Any) -> None:
        for name, value in changes.items():
            setattr(session, name, value)
        record = session.record()
        if record != self._saved.get(session.id):
            self._saved[session.id] = record
            path = session.data_dir / RECORD
            self._write = asyncio.ensure_future(self._save(self._write, path, record))
        session.changed.set()
        self._notify()

    @staticmethod
    async def _save(previous: asyncio.Future[None] | None, path: Path,
                    record: dict[str, Any]) -> None:
        """Write a record after the one before it, off the event loop."""
        if previous is not None:
            await asyncio.gather(previous, return_exceptions=True)
        with contextlib.suppress(OSError):  # a record is for listing only
            await asyncio.to_thread(path.write_text, json.dumps(record))

    def _notify(self) -> None:
        for listener in list(self.listeners):
            listener()
