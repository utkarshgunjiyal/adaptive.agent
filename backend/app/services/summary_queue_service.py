from app.services.thread_summary_service import update_thread_summary


async def enqueue_thread_summary_job(
    user_id: str,
    thread_id: str,
    from_seq: int,
    to_seq: int,
) -> None:
    print(
        f"[SUMMARY_JOB_STARTED] user_id={user_id}, "
        f"thread_id={thread_id}, from_seq={from_seq}, to_seq={to_seq}"
    )

    await update_thread_summary(
        user_id=user_id,
        thread_id=thread_id,
        from_seq=from_seq,
        to_seq=to_seq,
    )

    print(
        f"[SUMMARY_JOB_DONE] user_id={user_id}, "
        f"thread_id={thread_id}, from_seq={from_seq}, to_seq={to_seq}"
    )