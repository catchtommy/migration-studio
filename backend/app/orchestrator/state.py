from typing import TypedDict


class StageShas(TypedDict):
    start: str
    end: str


class MigrationState(TypedDict, total=False):
    run_id: str
    domain: str
    operation: str
    workspace: str
    feedback: dict[str, str]  # stage -> reviewer comments awaiting that stage's next attempt
    stage_shas: dict[str, StageShas]  # latest attempt's git range per agent stage
    route: str | None  # set by gates: where to go next
