"""Tools that run as jobs. Each service defines its tools and registers them in its AppConfig.ready()."""
from __future__ import annotations

from dataclasses import dataclass, field

from django.utils.module_loading import import_string


@dataclass(frozen=True, kw_only=True)
class Tool:
    app: str  # URL namespace of the service, also stored in Job.app
    key: str
    label: str  # menu entry
    title: str
    description: str
    runner: str  # dotted path of runner(job, directory, log) -> [{name, label}]
    form_class: type | None = None
    queue: str = "rnagrx_quick"
    params: tuple = field(default=())  # form fields stored in Job.params
    files: tuple = field(default=())  # FASTA upload fields, saved as input/<field>.fa
    blocked: str = ""  # reason the tool is temporarily unavailable
    result_template: str = ""  # optional summary shown above the result files

    @property
    def enabled(self) -> bool:
        return self.form_class is not None and not self.blocked

    def run(self, job, directory, log) -> list[dict]:
        return import_string(self.runner)(job, directory, log)


_TOOLS: dict[tuple[str, str], Tool] = {}


def register(*tools: Tool) -> None:
    for tool in tools:
        _TOOLS[(tool.app, tool.key)] = tool


def get_tool(app: str, key: str) -> Tool | None:
    return _TOOLS.get((app, key))


def tools_of(app: str) -> dict[str, Tool]:
    """The tools of one service, in registration order."""
    return {key: t for (a, key), t in _TOOLS.items() if a == app}
