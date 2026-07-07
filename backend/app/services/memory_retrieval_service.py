from app.services.message_service import get_recent_messages
from app.services.thread_summary_service import get_thread_summary
from app.schemas.request_plan import RequestPlan
from app.schemas.context_policy import ContextPolicy
from app.schemas.context_evidence import ContextEvidence
from app.schemas.memory_context import MemoryContext


async def retrieve_memory(
    user_id: str,
    thread_id: str,
    question: str,
    request_plan: RequestPlan,
    context_policy: ContextPolicy,
) -> MemoryContext:
    memory = MemoryContext()

    if context_policy.recent_messages_limit > 0:
        recent_messages = await get_recent_messages(
            user_id=user_id,
            thread_id=thread_id,
            limit=context_policy.recent_messages_limit,
        )

        memory.recent_messages = [
            ContextEvidence(
                source="recent_message",
                header=f"[Recent Message | {msg['role']} | Seq {msg['seq']}]",
                content=msg["content"],
                score=1.0,
                metadata={"seq": msg["seq"], "role": msg["role"]},
            )
            for msg in recent_messages
        ]

    if context_policy.thread_summary:
        summary_doc = await get_thread_summary(
            user_id=user_id,
            thread_id=thread_id,
        )

        if summary_doc and summary_doc.get("summary"):
            memory.thread_summary = [
                ContextEvidence(
                    source="thread_summary",
                    header="[Thread Summary]",
                    content=summary_doc["summary"],
                    score=1.0,
                    metadata={
                        "last_summarized_seq": summary_doc.get(
                            "last_summarized_seq", 0
                        )
                    },
                )
            ]

    return memory