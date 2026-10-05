"""Render one recorder the way the plugin would, for assertions."""

from stepdoc.core.record import case_record
from stepdoc.renderers.common import procedure_lines, report_lines


def procedure(rec):
    return procedure_lines(case_record(rec))


def report(rec):
    return "\n".join(report_lines(case_record(rec)))
