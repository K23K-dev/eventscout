"""Chat endpoints: stream each turn's progress and answer, and keep each owner's history."""

import asyncio
import logging
from collections.abc import AsyncGenerator, AsyncIterable, Coroutine
from contextlib import AsyncExitStack
from dataclasses import dataclass
from datetime import datetime
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastapi.sse import EventSourceResponse, ServerSentEvent
from openai import AsyncOpenAI
from pinecone import AsyncIndex, AsyncPinecone
from psycopg import AsyncConnection
from psycopg import Error as DatabaseError

from app.assistant.graph import MAX_CARDS, Services, Turn, build_graph, run_turn, turn_result
from app.assistant.intent import SearchState
from app.assistant.models import (
    ConversationDetail,
    ConversationSummary,
    TurnAnswer,
    TurnRequest,
    TurnView,
)
from app.assistant.store import ConversationStore, TurnInProgress
from app.auth import InvalidToken, KeysUnavailable, TokenVerifier
from app.database import connect_database
from app.events.models import CATALOG_TIMEZONE, EventResponse
from app.events.repository import EventRepository
from app.settings import Settings

logger = logging.getLogger(__name__)

# With EVENTSCOUT_ALLOW_CHAT_WITHOUT_LOGIN (local scripts only), guests chat as this one owner.
LOCAL_OWNER = UUID("00000000-0000-0000-0000-00000000c0de")
_FAILED = "Something went wrong answering that. Please try again."


class AssistantClients:
    """OpenAI and Pinecone clients shared by every request, opened on first use."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._stack = AsyncExitStack()
        self._lock = asyncio.Lock()
        self._clients: tuple[AsyncOpenAI, AsyncIndex] | None = None
        self._tasks: set[asyncio.Task[None]] = set()
        self.writing = asyncio.Semaphore(2)  # Answers this server writes at once.

    async def get(self) -> tuple[AsyncOpenAI, AsyncIndex]:
        async with self._lock:
            if self._clients is None:
                openai_key = self._settings.openai_api_key
                pinecone_key = self._settings.pinecone_api_key
                assert openai_key is not None and pinecone_key is not None
                async with AsyncExitStack() as stack:
                    openai = await stack.enter_async_context(
                        AsyncOpenAI(api_key=openai_key.get_secret_value())
                    )
                    pinecone = await stack.enter_async_context(
                        AsyncPinecone(api_key=pinecone_key.get_secret_value())
                    )
                    index = await stack.enter_async_context(
                        await pinecone.index(self._settings.pinecone_index)
                    )
                    self._stack = stack.pop_all()
                self._clients = openai, index
            return self._clients

    def spawn(self, work: Coroutine[Any, Any, None]) -> None:
        """Run work to completion in the background, even after its request ends."""
        task = asyncio.create_task(work)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def close(self) -> None:
        for task in self._tasks:
            task.cancel()
        await self._stack.aclose()


@dataclass
class StartedTurn:
    owner: UUID
    conversation_id: UUID
    turn_id: UUID
    message: str
    previous: SearchState | None
    shown: list[EventResponse]
    replayed: bool = False
    replay: TurnAnswer | None = None


def create_assistant_router(settings: Settings, clients: AssistantClients) -> APIRouter:
    router = APIRouter(prefix="/api")
    # A Supabase access token from Google sign-in.
    bearer = HTTPBearer(auto_error=False)
    verifier = TokenVerifier(settings.supabase_url) if settings.supabase_url else None

    async def current_owner(
        credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
    ) -> UUID:
        """The signed-in account, whose ID owns its conversations."""
        challenge = {"WWW-Authenticate": "Bearer"}
        if credentials is None:
            if settings.allow_chat_without_login:
                return LOCAL_OWNER
            raise HTTPException(
                status_code=401, detail="Sign in to use AI search.", headers=challenge
            )
        if verifier is None:
            raise HTTPException(status_code=503, detail="Sign-in isn't configured.")
        try:
            return await verifier.user_id(credentials.credentials)
        except InvalidToken:
            raise HTTPException(
                status_code=401, detail="Sign in again to use AI search.", headers=challenge
            ) from None
        except KeysUnavailable:
            logger.exception("Could not fetch the Supabase signing keys")
            raise HTTPException(
                status_code=503, detail="Sign-in is temporarily unavailable."
            ) from None

    async def database() -> AsyncGenerator[AsyncConnection[dict[str, Any]]]:
        async with AsyncExitStack() as stack:
            try:
                connection = await stack.enter_async_context(connect_database(settings))
            except (RuntimeError, DatabaseError):
                raise HTTPException(
                    status_code=503, detail="The event catalog is temporarily unavailable."
                ) from None
            yield connection

    Owner = Annotated[UUID, Depends(current_owner)]
    Connection = Annotated[AsyncConnection[dict[str, Any]], Depends(database)]

    async def start_turn(request: TurnRequest, owner: Owner, connection: Connection) -> StartedTurn:
        """Everything that can fail with a status code, before streaming begins."""
        store = ConversationStore(connection, owner)
        repository = EventRepository(connection)
        if (stored := await store.find_turn(request.request_id)) is not None:
            if stored["status"] == "running":
                raise HTTPException(status_code=409, detail="That message is still being answered.")
            replay = None
            if stored["status"] == "done":
                replay = TurnAnswer(
                    reply=stored["reply"],
                    cards=await repository.get_by_ids(list(stored["cards"])),
                    clarification=stored["clarification"],
                    note=stored["note"],
                )
            return StartedTurn(
                owner,
                stored["conversation_id"],
                stored["id"],
                stored["message"],
                None,
                [],
                replayed=True,
                replay=replay,
            )
        if settings.missing("openai_api_key", "pinecone_api_key"):
            raise HTTPException(status_code=503, detail="AI search isn't configured.")
        try:
            await clients.get()
        except Exception:
            logger.exception("Could not open the AI search clients")
            raise HTTPException(
                status_code=503, detail="AI search is temporarily unavailable."
            ) from None
        if request.conversation_id is None:
            conversation_id = await store.create_conversation(request.message[:80])
        elif await store.conversation(request.conversation_id) is None:
            raise HTTPException(status_code=404, detail="Conversation not found.")
        else:
            conversation_id = request.conversation_id
        previous, shown = await store.previous(conversation_id)
        try:
            turn_id = await store.start_turn(conversation_id, request.request_id, request.message)
        except TurnInProgress:
            raise HTTPException(
                status_code=409, detail="Wait for the current answer to finish."
            ) from None
        return StartedTurn(
            owner,
            conversation_id,
            turn_id,
            request.message,
            previous,
            await repository.get_by_ids(shown),
        )

    async def write_answer(
        started: StartedTurn, events: asyncio.Queue[ServerSentEvent | None]
    ) -> None:
        """Answer and save the turn even if the client leaves; the stream only relays events."""
        today = datetime.now(CATALOG_TIMEZONE).date()
        try:
            async with connect_database(settings) as connection:
                store = ConversationStore(connection, started.owner)
                try:
                    openai, index = await clients.get()
                    repository = EventRepository(connection)
                    services = Services(
                        connection, repository, openai, index, settings.openai_model
                    )
                    turn: Turn = {}
                    async with clients.writing:
                        steps = run_turn(
                            build_graph(services),
                            started.message,
                            today,
                            started.previous,
                            started.shown,
                        )
                        async for node, turn in steps:
                            if node == "parse" and not turn.get("clarification"):
                                stage = "writing" if turn.get("points_at") else "searching"
                                events.put_nowait(
                                    ServerSentEvent(event="status", data={"stage": stage})
                                )
                            elif node == "retrieve":
                                candidates = turn.get("evidence", [])[:MAX_CARDS]
                                events.put_nowait(
                                    ServerSentEvent(event="results", data={"events": candidates})
                                )
                                events.put_nowait(
                                    ServerSentEvent(event="status", data={"stage": "writing"})
                                )
                            elif node == "validate" and "reply" not in turn and turn.get("broader"):
                                broader = {"stage": "searching", "broader": turn["broader"]}
                                events.put_nowait(ServerSentEvent(event="status", data=broader))
                    result = turn_result(turn, started.previous)
                    shown = result.cards if result.searched else started.shown
                    await store.finish_turn(started.turn_id, result, [e.id for e in shown])
                except Exception as exc:
                    logger.exception("Chat turn failed")
                    await store.fail_turn(started.turn_id, type(exc).__name__)
                    events.put_nowait(ServerSentEvent(event="error", data={"message": _FAILED}))
                    return
                answer = TurnAnswer(
                    reply=result.reply,
                    cards=result.cards,
                    clarification=result.clarification,
                    note=result.note,
                )
                events.put_nowait(ServerSentEvent(event="answer", data=answer))
                events.put_nowait(ServerSentEvent(event="done", data={}))
        except Exception:
            # Without a database the turn can't be saved; it expires after two minutes.
            logger.exception("Chat turn failed before it could be saved")
            events.put_nowait(ServerSentEvent(event="error", data={"message": _FAILED}))
        finally:
            events.put_nowait(None)

    @router.post("/turns", response_class=EventSourceResponse)
    async def ask(
        started: Annotated[StartedTurn, Depends(start_turn)],
    ) -> AsyncIterable[ServerSentEvent]:
        """Answer one message as Server-Sent Events.

        In order: `turn` (conversation and turn IDs); `status` updates (understanding,
        searching, writing); `results` with candidate events as soon as the search ends;
        then `answer` and `done`, or `error`. The answer is saved even if the client leaves,
        and repeating a request_id replays the saved outcome.
        """
        ids = {"conversation_id": started.conversation_id, "turn_id": started.turn_id}
        yield ServerSentEvent(event="turn", data=ids)
        if started.replayed:
            if started.replay is None:
                yield ServerSentEvent(event="error", data={"message": _FAILED})
                return
            yield ServerSentEvent(event="answer", data=started.replay)
            yield ServerSentEvent(event="done", data={})
            return
        yield ServerSentEvent(event="status", data={"stage": "understanding"})
        events: asyncio.Queue[ServerSentEvent | None] = asyncio.Queue()
        clients.spawn(write_answer(started, events))
        while (event := await events.get()) is not None:
            yield event

    @router.get("/conversations")
    async def list_conversations(owner: Owner, connection: Connection) -> list[ConversationSummary]:
        """The owner's 50 most recently active conversations."""
        rows = await ConversationStore(connection, owner).conversations()
        return [ConversationSummary.model_validate(row) for row in rows]

    @router.get("/conversations/{conversation_id}")
    async def get_conversation(
        conversation_id: UUID, owner: Owner, connection: Connection
    ) -> ConversationDetail:
        """A conversation's turns with the events each one showed."""
        store = ConversationStore(connection, owner)
        conversation = await store.conversation(conversation_id)
        if conversation is None:
            raise HTTPException(status_code=404, detail="Conversation not found.")
        rows = await store.turns(conversation_id)
        ids = list(dict.fromkeys(card for row in rows for card in row["cards"]))
        repository = EventRepository(connection)
        events = {event.id: event for event in await repository.get_by_ids(ids)}
        turns = [
            TurnView(
                id=row["id"],
                message=row["message"],
                status=row["status"],
                created_at=row["created_at"],
                reply=row["reply"],
                cards=[events[card] for card in row["cards"] if card in events],
                clarification=row["clarification"],
                note=row["note"],
            )
            for row in rows
        ]
        return ConversationDetail.model_validate({**conversation, "turns": turns})

    @router.delete("/conversations/{conversation_id}", status_code=204)
    async def delete_conversation(
        conversation_id: UUID, owner: Owner, connection: Connection
    ) -> None:
        """Delete a conversation and all of its turns."""
        if not await ConversationStore(connection, owner).delete(conversation_id):
            raise HTTPException(status_code=404, detail="Conversation not found.")

    return router
