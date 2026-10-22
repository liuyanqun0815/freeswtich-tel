CDR_STREAM = "cdr.events"


def call_key(call_id: str) -> str:
    return f"call:{call_id}"


def leg_key(uuid: str) -> str:
    return f"leg:{uuid}"


def sipcred_key(domain: str, ext: str) -> str:
    return f"sipcred:{domain}:{ext}"


ESL_JOBS = "esl:jobs"
