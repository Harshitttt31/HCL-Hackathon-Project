"""The explicit allowlist of tools. The planner and the LLM can only name tools that appear here."""
from __future__ import annotations

from app.tools import eligibility, rules, search, student
from app.tools.base import ToolRegistry, ToolSpec
from app.tools.calculator import CalcResult


def build_registry() -> ToolRegistry:
    r = ToolRegistry()
    r.register(ToolSpec("get_student_profile", "Programme, batch, semester, recorded CGPA and active backlogs of the logged-in student.",
                        student.EmptyInput, student.StudentProfileOutput, student.get_student_profile, personal=True))
    r.register(ToolSpec("get_attendance", "Classes held/attended and attendance percentage (computed) for one course or all courses of the logged-in student.",
                        student.CourseCodeInput, student.AttendanceOutput, student.get_attendance, personal=True))
    r.register(ToolSpec("get_results", "Exam results and current status per course for the logged-in student.",
                        student.CourseCodeInput, student.ResultsOutput, student.get_results, personal=True))
    r.register(ToolSpec("get_course", "Catalogue entry for a course code (public).",
                        student.GetCourseInput, student.CourseOutput, student.get_course))
    r.register(ToolSpec("get_rule", "Resolve a rule parameter from the rule registry using the source precedence policy.",
                        rules.GetRuleInput, rules.RuleResolution, rules.get_rule))
    r.register(ToolSpec("calculate_attendance", "attendance_pct = classes_attended / classes_held * 100.",
                        student.CalculateAttendanceInput, CalcResult, student.calculate_attendance_tool))
    r.register(ToolSpec("calculate_attendance_projection", "Classes still to attend (or that can be missed) to reach an attendance threshold.",
                        student.AttendanceProjectionInput, student.AttendanceProjectionOutput, student.calculate_attendance_projection, personal=True))
    r.register(ToolSpec("calculate_cgpa", "Credit-weighted CGPA recomputed from results with the grade scale in the rule registry, compared with the record.",
                        student.EmptyInput, student.CgpaOutput, student.calculate_cgpa_tool, personal=True))
    r.register(ToolSpec("check_exam_eligibility", "Deterministic eligibility for a regular or supplementary exam of a course.",
                        eligibility.ExamEligibilityInput, eligibility.EligibilityOutput, eligibility.check_exam_eligibility,
                        personal=True, aliases=("calculate_eligibility",)))
    r.register(ToolSpec("check_placement_eligibility", "Deterministic placement eligibility, optionally assuming some backlogs are cleared.",
                        eligibility.PlacementInput, eligibility.EligibilityOutput, eligibility.check_placement_eligibility, personal=True))
    r.register(ToolSpec("check_attendance_band", "What-if: eligibility band for a hypothetical attendance percentage.",
                        eligibility.AttendanceBandInput, eligibility.EligibilityOutput, eligibility.check_attendance_band))
    r.register(ToolSpec("search_documents", "Hybrid search over ingested documents (similarity only; not authority).",
                        search.SearchDocumentsInput, search.SearchDocumentsOutput, search.search_documents))
    r.register(ToolSpec("get_source_metadata", "Source Register entry for a document id.",
                        search.GetSourceMetadataInput, search.SourceMetadataOutput, search.get_source_metadata))
    r.register(ToolSpec("list_applicable_sources", "Documents that govern the student's programme and batch on the as-of date.",
                        search.ApplicableSourcesInput, search.ApplicableSourcesOutput, search.list_applicable_sources, personal=True))
    return r
