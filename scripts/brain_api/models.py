"""Request bodies for the write routes.

These are what the generated OpenAPI document shows, so each field says what it
means. They are deliberately tolerant in the ways the page and the tests rely on:

  * a missing body is an empty one — every route then refuses on its merits
    ("missing id"), which is the reason worth showing a person;
  * `null` for a text field means empty, as it always did;
  * a junk entry inside a list of objects is skipped rather than failing the
    whole call (the page builds these lists from free-form UI state).

A field of the wrong TYPE is refused (400), never stored as its repr. Length
caps and the closed sets are enforced in the handlers, not here: the error
strings they produce are part of the contract.
"""

from __future__ import annotations

from typing import Annotated, Any, Optional

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, create_model

import graph


def _none_to_empty(v):
    return "" if v is None else v


def _dict_or_none(v):
    return v if isinstance(v, dict) else None


def _str_or_none(v):
    return v if isinstance(v, str) else None


#: Text where `null` means "". Absent and empty are the same thing for these.
Text = Annotated[str, BeforeValidator(_none_to_empty)]
#: A list entry that is skipped, not refused, when it is not an object / string.
Loose = Annotated[Optional[Any], BeforeValidator(_dict_or_none)]
LooseStr = Annotated[Optional[str], BeforeValidator(_str_or_none)]


def _loose(model):
    return Annotated[Optional[model], BeforeValidator(_dict_or_none)]


class Body(BaseModel):
    model_config = ConfigDict(extra="ignore")


def T(description: str = "", default: str = ""):
    return Field(default, description=description or None)


# ------------------------------------------------------------------ tasks
class Link(Body):
    id: Text = T("The node to link to.")
    rel: Text = T("One of the relations `/api/meta` lists.")


class SetDue(Body):
    id: Text = T("The node id.")
    due: Text = T("ISO timestamp. Empty clears it.")


class AddTask(Body):
    title: Text = T("Required.")
    type: Text = T("`task` (default) or `subtask`.")
    body: Text = ""
    due: Text = T("ISO date or timestamp.")
    realm: Text = T("Defaults to the configured realm.")
    assignee: Text = ""
    priority: Text = ""
    links: list[_loose(Link)] = Field(
        default_factory=list,
        description="Relations to create from the new task. Up to 20; one with an "
                    "unknown relation is reported under `rejected`, not applied.")


class Complete(Body):
    id: Text = T("The node id.")
    note: Text = T("Kept on the node as the completion note.")


class SetStatus(Body):
    id: Text = T("The node id.")
    status: Text = T("One of the statuses `/api/meta` lists.")
    waiting_on: Optional[str] = Field(
        None, description="Who or what it waits on. Omit to leave it as it is.")


class SetAssignee(Body):
    id: Text = T("The node id.")
    assignee: Text = T("Empty un-assigns.")


class UpdateNode(Body):
    """Every field but `id` is optional; one that is sent is written, in one write."""
    id: Text = T("The node id.")
    title: Optional[str] = Field(None, description="Cannot be blanked.")
    body: Optional[str] = None
    priority: Optional[str] = Field(None, description="P1–P4, or empty for none.")
    due: Optional[str] = Field(None, description="YYYY-MM-DD or YYYY-MM-DD HH:MM; "
                                                 "empty clears it.")
    realm: Optional[str] = Field(None, description="Empty keeps the current realm.")


# ----------------------------------------------------------------- people
class Assign(Body):
    id: Text = T("The task id.")
    person_id: Text = T("A person node. Empty un-assigns and drops the edge.")


class MergePerson(Body):
    canonical: Text = T("The person node that stays.")
    duplicate: Text = T("The person node folded into it.")


class AddAlias(Body):
    id: Text = T("The person node.")
    alias: Text = T("Another spelling they answer to.")


# ------------------------------------------------------------------ graph
class Edge(Body):
    src: Text = ""
    dst: Text = ""
    rel: Text = T("One of the relations `/api/meta` lists.")


# ------------------------------------------------------------- settings
class Settings(Body):
    """Only the keys sent are written. An empty string REMOVES a key, falling
    back to the environment variable or the built-in default."""
    tz: Optional[str] = Field(None, description="IANA timezone name.")
    realm: Optional[str] = Field(None, description="Lowercase letters, digits, dashes.")
    owner: Optional[str] = Field(None, description="Who 'me' is.")
    backend: Optional[str] = Field(None, description="sqlite | neo4j | auto.")
    autosync: Any = Field(None, description="true | false; null or empty resets it.")
    consolidate: Any = Field(None, description="true | false; null or empty resets it.")
    ui_sync: Any = Field(None, description="true | false; null or empty resets it.")
    map_poll_seconds: Any = Field(None, description="1–600.")
    dash_refresh_seconds: Any = Field(None, description="5–3600.")


class Neo4jConfig(Body):
    url: Text = T("The neo4j HTTP endpoint, e.g. http://host:7474. Empty removes "
                  "the master.")
    db: Text = T("Default `neo4j`.")
    user: Text = T("Default `neo4j`.")
    password: Text = T("Blank keeps the stored one.")


class Empty(Body):
    pass


class Maintain(Body):
    confirm: Any = Field(None, description="Must be true. Without it the call returns "
                                           "ok:false and needs_confirmation:true.")
    keep_days: Any = Field(2, description="Leave the last N days of captures loose.")


class Capture(Body):
    enabled: Any = Field(None, description="true records, false stops recording.")


class UiPrefs(Body):
    prefs: Annotated[Optional[dict[str, Any]], BeforeValidator(_dict_or_none)] = Field(
        None, description="The whole blob. Replaces what is stored.")


class Backup(Body):
    label: Text = T("Default `manual`.")


# ---------------------------------------------------------------- trigger
class TriggerGate(Body):
    scope: Text = T("`slack:<channel>`, `slack:<channel>:<thread_ts>` or `jira:<ISSUE-KEY>`.")
    enabled: Any = Field(None, description="true opens the conversation to other people's "
                                           "mentions, false closes it again.")


_obj = Annotated[Optional[dict[str, Any]], BeforeValidator(_dict_or_none)]


class TriggerConfig(Body):
    """A patch: only the keys sent are changed. Secrets are write-only."""
    enabled: Any = Field(None, description="The master switch for every trigger.")
    signature: Any = Field(None, description="The line every reply ends with; `{owner}` "
                                             "is the owner's name. Also the loop guard.")
    refusal: Any = Field(None, description="The notice a refused mention gets.")
    claude: _obj = Field(None, description="`bin`, `mcp_config`, `strict_mcp_config`, "
                                           "`timeout_s`, `max_concurrent`, `extra_args`, `cwd`.")
    slack: _obj = Field(None, description="`enabled`, `owner_user_id`, `channels`, "
                                          "`thread_lookback_days`, and the write-only "
                                          "`app_token`, `bot_token`, `user_token`.")
    jira: _obj = Field(None, description="`enabled`, `base_url`, `email`, `owner_account_id`, "
                                         "`marker`, `poll_seconds`, `jql`, `max_catchup_hours`, "
                                         "and the write-only `token`.")
    clear: Optional[list[LooseStr]] = Field(None, description="Secrets to remove, e.g. "
                                                              "`[\"slack.user_token\"]`.")


class TriggerCheck(Body):
    only: Text = T("`claude`, `slack` or `jira`; empty checks everything switched on.")
    offline: Any = Field(False, description="true skips the calls to Slack and Jira.")


class TriggerAction(Body):
    request_id: Text = T("The request the action was taken for.")
    kind: Text = T("What was done: `reply`, `jira-transition`, `slack-message`, ...")
    target: Text = T("Where: a permalink, an issue key, a channel.")
    detail: Text = T("What, in a sentence a person could audit.")


# -------------------------------------------------------------- contracts
_sections = Annotated[Optional[dict[str, Any]], BeforeValidator(_dict_or_none)]


class CreateRule(Body):
    title: Text = T("Required.")
    source: Text = T("Default `user`.")
    section: Text = ""
    sections: _sections = Field(
        None, description="Contract sections by field; `the_rule` and "
                          "`judged_against` are required. See `/api/meta`.")
    scenario: Text = T("When the rule applies, in the words a matcher reads.")
    non_negotiable: Any = False
    overrides: Text = ""
    realm: Text = ""
    id: Text = T("Refresh this rule rather than create one.")


_decl = Annotated[Optional[list[Any] | str], Field(
    None, description="A list of names, or one comma-separated string.")]

CreateSkill = create_model(
    "CreateSkill",
    __base__=Body,
    __doc__="A skill and, in `new_rules`, the rules it is given inline.",
    title=(Text, T("Required.")),
    name=(Text, T("Defaults to the title.")),
    description=(Text, ""),
    triggers=(Text, ""),
    realm=(Text, ""),
    id=(Text, T("Refresh this skill rather than create one.")),
    sections=(_sections, Field(None, description="The §5.1 sections by field. "
                                                 "See `/api/meta`.")),
    rules=(list[LooseStr], Field(default_factory=list,
                                 description="Ids of rules it honors.")),
    new_rules=(list[_loose(CreateRule)], Field(
        default_factory=list, description="Rules created FIRST, in this call.")),
    **{field: _decl for field, _label in graph.DECL_LABELS},
)


class CreateProcess(Body):
    title: Text = ""
    body: Text = ""
    trigger: Text = ""
    realm: Text = T("Lowercase letters, digits, dashes. Default: the configured one.")
    id: Text = T("Refresh this process rather than create one.")


class ExportSkills(Body):
    kind: Text = T("`learned` (default) or `plugin`.")
    dir: Text = T("Where to write. Default: <home>/skills-export.")


class IngestSkills(Body):
    dir: Text = ""


class ExportBundle(Body):
    skills: list[LooseStr] = Field(default_factory=list)
    rules: list[LooseStr] = Field(default_factory=list)
    processes: list[LooseStr] = Field(default_factory=list)


class UploadBundle(Body):
    filename: Text = ""
    data: Text = T("The zip, base64 (a data: URL is accepted).")


class ImportBundle(Body):
    token: Text = T("From upload-bundle.")
    select: Annotated[Optional[dict[str, Any]], BeforeValidator(_dict_or_none)] = Field(
        None, description="Item id to update | replace | ignore.")
    process_realm: Text = T("Realm the bundle's processes land in; default the "
                            "configured realm.")


class DeleteContracts(Body):
    ids: list[LooseStr] = Field(default_factory=list)
    force: Any = Field(False, description="Delete built-ins too.")
