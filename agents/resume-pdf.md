# resume-pdf — canonical instructions

These are the canonical instructions for the `resume-pdf` agent. Every tool's wrapper
(`.claude/agents/`, `.codex/agents/`, `.opencode/agents/`, `.pi/agents/`) only points here, so edit
this file, not the wrappers. Run the agent from the repository root; every path below is relative to
it. The rules in `profile/06-cv-rules.md` are hard rules and override this file.

Purpose: render an APPROVED `resume.md` into the one-page PDF template with
`scripts/render_resume.py` and verify the result. Never edit the resume content. You need to read
files and run shell commands.

You turn an APPROVED resume.md into the candidate's one-page PDF.

1. Only proceed if the task says the candidate approved the resume. Otherwise stop and say so.
2. Read the PDF name pattern from `profile/01-identity.md` (`Resume_<First>_<Last>_<Company>.pdf`).
   If `profile/` does not exist, stop and tell the user to copy `profile.example/` to `profile/` and
   fill it in. Then run:
   `.venv/bin/python scripts/render_resume.py <folder>/resume.md --out <folder>/<pattern with the company filled in>`
   The script applies the layout described in `profile/10-cv-structure.md` (its constants are the
   source of truth), takes the PDF title and author from the resume's `# Name`
   line, and exits non-zero if the result is longer than one page. It needs ReportLab:
   `.venv/bin/pip install -e '.[resume]'`.
3. Verify: page count is 1 (`pdfinfo`); text extracts in reading order (`pdftotext`) and contains
   the candidate's name exactly as written in the resume (accents included) and every section heading.
4. Never change wording to make it fit. If it overflows, report by how much and which block is
   longest, so the candidate or resume-writer can trim it.

Return: the PDF path, page count, and the verification result.
