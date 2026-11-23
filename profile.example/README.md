# Career knowledge base — <Your Full Name>

> **This is a template.** Copy this folder to `profile/` and fill it in:
>
> ```bash
> cp -R profile.example profile
> ```
>
> `profile/` is **git-ignored**: your career facts, contact details and application answers stay on
> your machine and are never committed. Never put real data in `profile.example/`; it is the public
> template that ships with the repository.

The knowledge base is the **single source of truth** for tailoring resumes, cover letters and
interview answers. Every fact in it should be **true and defensible in an interview**.

## How the resume agents use it

- **`resume-writer`** (`agents/resume-writer.md`) reads `profile/README.md` first, then every
  file listed below. It takes the header (name, email, LinkedIn) and the PDF name pattern from
  `01-identity.md`, follows `10-cv-structure.md` as the layout template, and treats `06-cv-rules.md`
  as hard rules that override the agent prompt. It never invents anything that is not written here:
  a requirement the profile cannot support is reported as a **gap** instead.
- **`resume-pdf`** (`agents/resume-pdf.md`) renders the approved `resume.md` into the one-page PDF
  with `scripts/render_resume.py`. The name in the PDF comes from the resume's `# Name` heading.
- If `profile/` does not exist, the agents stop and ask you to copy `profile.example/` to `profile/`.
- The instructions live in `agents/`; Claude Code, Codex, OpenCode and pi use thin wrappers that point
  to them. See [docs/ai-agents.md](../docs/ai-agents.md) for how to use them in each tool.

Optional: if you use a persistent-memory tool (for example Engram), mirror these files under the topic
keys `profile/*`. The files always win if the memory and the files disagree.

## Files

| File | What it holds |
|---|---|
| [01-identity.md](01-identity.md) | Contact, title and seniority, headlines, languages, PDF name pattern |
| [02-experience.md](02-experience.md) | Each job: scope, achievements, evidence level |
| [02a-flagship-project.md](02a-flagship-project.md) | Optional deep-dive on your best project: problem, decisions, outcome (copy it per project) |
| [03-skills.md](03-skills.md) | Skills with honest depth |
| [04-projects.md](04-projects.md) | Client and personal projects |
| [05-education.md](05-education.md) | Education, certifications and languages |
| [06-cv-rules.md](06-cv-rules.md) | Your hard rules and preferences for CVs (titles, year counts, wording) |
| [07-stories-and-answers.md](07-stories-and-answers.md) | Strengths, behavioral stories, reusable interview answers |
| [08-gaps.md](08-gaps.md) | Honest gaps and how to handle them |
| [09-job-search.md](09-job-search.md) | Targets, interview history, open items like salary |
| [10-cv-structure.md](10-cv-structure.md) | One-page CV template: page, fonts, colors, sections, length budget |
| [11-application-answers.md](11-application-answers.md) | Answers already given to application questions, with tags to find and reuse them |
| [open-questions.md](open-questions.md) | Conflicts and things to confirm |

## Evidence levels

Mark each fact so you (and the agent) know how much weight it carries:

- **✅ confirmed** — you confirmed it yourself.
- **📄 documented** — taken from an existing CV or portfolio; not re-confirmed.
- **⚠️ check** — conflicting, possibly overstated, or specific to one vacancy.

## Filling it in

1. Start with `01-identity.md`, `02-experience.md`, `03-skills.md` and `05-education.md`.
2. Write the rules that are yours alone in `06-cv-rules.md` (exact titles, year counts per skill,
   claims you never want on a resume).
3. Delete the placeholder lines you do not need, and keep the tables and headings: the agents rely on them.
4. Add the true numbers you know to `02-experience.md`; leave unknowns in "Still missing" rather than guessing.
