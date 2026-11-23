---
description: Renders an APPROVED resume.md into the one-page PDF with scripts/render_resume.py and verifies it. Use it only after the user approved the resume. Never edits the resume content.
mode: subagent
permissions:
  - action: edit
    resource: "*"
    effect: deny
  - action: shell
    resource: "*"
    effect: allow
---

Read `agents/resume-pdf.md` from the repository root and follow it exactly. Those are the canonical instructions for this agent; the rules in `profile/06-cv-rules.md` override them.
