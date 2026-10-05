"""Conversations and their turns; every query is scoped to the owner."""

from typing import Any
from uuid import UUID

from psycopg import AsyncConnection
from psycopg.errors import UniqueViolation
from psycopg.types.json import Jsonb

from app.assistant.graph import TurnResult
from app.assistant.intent import SearchState


class TurnInProgress(Exception):
    """Another turn in the conversation is still being answered."""


class ConversationStore:
    def __init__(self, connection: AsyncConnection[dict[str, Any]], owner: UUID) -> None:
        self._connection = connection
        self._owner = owner

    async def create_conversation(self, title: str) -> UUID:
        cursor = await self._connection.execute(
            "INSERT INTO eventscout.conversations (owner_id, title) VALUES (%s, %s) RETURNING id",
            (self._owner, title),
        )
        row = await cursor.fetchone()
        assert row is not None
        return UUID(str(row["id"]))

    async def conversation(self, conversation_id: UUID) -> dict[str, Any] | None:
        cursor = await self._connection.execute(
            """SELECT id, title, updated_at FROM eventscout.conversations
               WHERE id = %s AND owner_id = %s""",
            (conversation_id, self._owner),
        )
        return await cursor.fetchone()

    async def conversations(self) -> list[dict[str, Any]]:
        cursor = await self._connection.execute(
            """SELECT id, title, updated_at FROM eventscout.conversations
               WHERE owner_id = %s ORDER BY updated_at DESC LIMIT 50""",
            (self._owner,),
        )
        return await cursor.fetchall()

    async def turns(self, conversation_id: UUID) -> list[dict[str, Any]]:
        cursor = await self._connection.execute(
            """SELECT t.* FROM eventscout.turns t
               JOIN eventscout.conversations c ON c.id = t.conversation_id
               WHERE t.conversation_id = %s AND c.owner_id = %s ORDER BY t.created_at""",
            (conversation_id, self._owner),
        )
        return await cursor.fetchall()

    async def delete(self, conversation_id: UUID) -> bool:
        cursor = await self._connection.execute(
            "DELETE FROM eventscout.conversations WHERE id = %s AND owner_id = %s",
            (conversation_id, self._owner),
        )
        return cursor.rowcount > 0

    async def previous(self, conversation_id: UUID) -> SearchState | None:
        """The search in force after the latest answered turn, which follow-ups build on."""
        cursor = await self._connection.execute(
            """SELECT search FROM eventscout.turns
               WHERE conversation_id = %s AND status = 'done'
               ORDER BY created_at DESC LIMIT 1""",
            (conversation_id,),
        )
        row = await cursor.fetchone()
        return SearchState.model_validate(row["search"]) if row and row["search"] else None

    async def start_turn(self, conversation_id: UUID, message: str) -> UUID:
        """Record a running turn; abandoned turns older than two minutes stop blocking."""
        try:
            async with self._connection.transaction():
                await self._connection.execute(
                    """UPDATE eventscout.turns
                       SET status = 'failed', error = 'abandoned', finished_at = clock_timestamp()
                       WHERE conversation_id = %s AND status = 'running'
                         AND created_at < clock_timestamp() - interval '2 minutes'""",
                    (conversation_id,),
                )
                cursor = await self._connection.execute(
                    """INSERT INTO eventscout.turns (conversation_id, request_id, message)
                       VALUES (%s, gen_random_uuid(), %s) RETURNING id""",
                    (conversation_id, message),
                )
                row = await cursor.fetchone()
        except UniqueViolation as exc:
            raise TurnInProgress from exc
        assert row is not None
        return UUID(str(row["id"]))

    async def finish_turn(self, turn_id: UUID, result: TurnResult) -> None:
        async with self._connection.transaction():
            cursor = await self._connection.execute(
                """UPDATE eventscout.turns SET
                       status = 'done', search = %s, cards = %s, reply = %s,
                       clarification = %s, note = %s, broader = %s, finished_at = clock_timestamp()
                   WHERE id = %s RETURNING conversation_id""",
                (
                    Jsonb(result.search.model_dump(mode="json")) if result.search else None,
                    [event.id for event in result.cards],
                    result.reply,
                    result.clarification,
                    result.note,
                    result.broader,
                    turn_id,
                ),
            )
            row = await cursor.fetchone()
            assert row is not None
            await self._connection.execute(
                "UPDATE eventscout.conversations SET updated_at = clock_timestamp() WHERE id = %s",
                (row["conversation_id"],),
            )

    async def fail_turn(self, turn_id: UUID, error: str) -> None:
        await self._connection.execute(
            """UPDATE eventscout.turns
               SET status = 'failed', error = %s, finished_at = clock_timestamp()
               WHERE id = %s AND status = 'running'""",
            (error[:500], turn_id),
        )
