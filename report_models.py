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
    title: str        # asset ID
    paragraph: str    # technician's summary note
    tech_name: str = ""  # who logged it (empty on old saved reports)


class ReportData(BaseModel):
    date: str
    shift_type: str
    lead_name: str
    dc_number: str = ""
    vendors:   list[str]               = Field(default_factory=list)
    happenings: list[RewrittenHappening] = Field(default_factory=list)
    ro:        list[str]               = Field(default_factory=list)
    rts:       list[str]               = Field(default_factory=list)
    oos:       list[OosUnit]           = Field(default_factory=list)
    summary:   list[str]               = Field(default_factory=list)
