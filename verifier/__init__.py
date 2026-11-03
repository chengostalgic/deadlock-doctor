"""Pure independent NanoAgent entry points; no solver, IO, network, or LLM judge."""

from .audit import audit, inspect


def verdict(valid, message, **details):
    return {"valid": valid, "score": 1.0 if valid else 0.0, "message": message, "details": details}


def verify_input(tool, value):
    if tool != "resolve_deadlock":
        return verdict(False, "unknown tool")
    try:
        inspect(value)
    except ValueError as error:
        return verdict(False, str(error))
    return verdict(True, "valid exclusive, non-reentrant mutex snapshot within 12 threads and 24 locks")


def check_result(given, made):
    if made is None:
        return verdict(False, "missing deadlock result")
    try:
        audit(given, made)
    except (ValueError, IndexError, KeyError) as error:
        return verdict(False, str(error))
    return verdict(
        True,
        "snapshot, cycles, recovery, and exhaustive minimum-cost tie-break are correct",
        status=made.status.value,
        cycles=len(made.cycles),
    )


def verify_output(tool, input_value, output_value, workspace):
    return check_result(input_value, output_value) if tool == "resolve_deadlock" else verdict(False, "unknown tool")


def verify_task(task, arguments, output, reference, workspace):
    return check_result(arguments, output) if task == "fix-deadlock" else verdict(False, "unknown task")
