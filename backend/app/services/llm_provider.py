async def generate_answer(context: dict) -> str:
    question = context["question"]
    request_plan = context["request_plan"]
    context_policy = context["context_policy"]
    evidence = context.get("evidence", [])

    return (
        f"You said: {question}\n\n"
        f"Intent: {request_plan['intent']}\n"
        f"Operation: {request_plan['operation']}\n"
        f"Evidence blocks used: {len(evidence)}\n"
        f"Priority order: {context_policy['priority']}"
    )