"""Shared styling for the "PM's Needing to be Completed" table.

Single source of truth across every place this table renders: the live
MST dashboard, the Outlook passdown email, PDF exports, and DOCX exports.
Previously each of those four spots reimplemented its own "highlight the
row yellow if due==today" logic independently -- and they'd drifted
apart in subtle ways (e.g. whether the due-date text also turned red).

That highlight was removed entirely (2026-10) because the PMs tracked
here are routine, daily-recurring maintenance items -- "due today" is
the *normal* state for nearly every row, not an exception worth an
alarm color. Highlighting 100% of a table the same "urgent" color trains
people to ignore it (classic alert fatigue), and as a bonus, email
clients with aggressive dark-mode color remapping (looking at you,
Outlook) mangle a big solid block of saturated yellow into a muddy,
confusing mess.

So: no due-date alarm color anywhere, ever. Just a clean, plain zebra
stripe. If a genuine "this PM is actually overdue" alert is wanted later,
it needs real frequency data from the source system first (the Cetaris
export has none today) -- see repo decision log for the investigation.
"""

PM_HEADER_HEX   = "1a56db"   # navy -- no leading '#' (docx-shading friendly)
PM_ROW_EVEN_HEX = "f9fafb"   # pale gray
PM_ROW_ODD_HEX  = "ffffff"   # white


def pm_zebra_hex(index: int) -> str:
    """Plain alternating row color for the PM table, no leading '#'.

    Prefix with '#' for inline CSS; use as-is for python-docx cell shading.
    """
    return PM_ROW_EVEN_HEX if index % 2 == 0 else PM_ROW_ODD_HEX
