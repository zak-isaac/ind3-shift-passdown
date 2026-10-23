"""Pydantic models for the passdown report data pipeline."""
from __future__ import annotations
from pydantic import BaseModel, Field


class HappeningInput(BaseModel):
    title: str
    raw_note: str


class OosUnit(BaseModel):
    unit: str
    issue: str = ""
    next_steps: str = ""


class RewrittenHappening(BaseModel):
    title: str        # asset ID -- MUST stay a clean/bare asset ID string.
    # ai_rewriter.build_input() and main.py's asset_groups merge logic both
    # key/format directly off `title` as a plain asset ID ("ASSET_ID /
    # remark1 | remark2"); embedding anything else into it (e.g. a cause
    # string) corrupts the AI input AND breaks same-asset grouping. Use the
    # dedicated `cause` field below instead.
    cause: str = ""   # short cause/issue text, shown as "title : cause :"
    paragraph: str    # single-tech summary (used when sub_points is empty)
    wo_number: str = ""  # WO number for the report line, empty for manual entries
    tech_name: str = ""  # who logged it (single-tech)
    # Multi-tech WOs: each tech's remark as its own sub-point
    # List of (tech_name, rewritten_remark) -- empty for single-tech WOs
    sub_points: list[tuple[str, str]] = Field(default_factory=list)


class ReportData(BaseModel):
    date: str
    shift_type: str
    lead_name: str
    dc_number: str = ""
    vendors:   list[str]               = Field(default_factory=list)
    happenings: list[RewrittenHappening] = Field(default_factory=list)
    pm_count:  int                     = 0
    ro:        list[str]               = Field(default_factory=list)
    rts:       list[str]               = Field(default_factory=list)
    oos:       list[OosUnit]           = Field(default_factory=list)
    summary:   list[str]               = Field(default_factory=list)
    shift_notes: str                   = ""
