# AI agents for resumes

job-finder ships two optional agents that turn your own career notes into a tailored one-page
resume. Nothing else in the app needs them.

| Agent | Use it when | What it does |
|---|---|---|
| `resume-writer` | You have a job description and want a resume for it | Reads `profile/`, writes `resumes/<company>-<role>/resume.md` and `match-report.md`. Never makes the PDF. |
| `resume-pdf` | You reviewed and **approved** a `resume.md` | Renders it to a one-page PDF with `scripts/render_resume.py` and checks the page count and text. Never edits the resume. |

The agents use only facts written in your `profile/`. A requirement your profile cannot support is
reported as a **gap** in the match report; it is never added to the resume. Your results depend on how
complete and honest your `profile/` is.

## Workflow

1. **Fill your profile once.** `cp -R profile.example profile`, then edit the files (see
   `profile.example/README.md`). `profile/` is git-ignored and stays on your machine. Put your own hard
   rules (exact titles, year counts, wording) in `profile/06-cv-rules.md`; they override the agent
   instructions.
2. **Paste a job description** to `resume-writer` (the job page's **COPY FOR RESUME** button copies an
   offer as text ready to paste).
3. `resume-writer` writes `resumes/<company>-<role>/resume.md` and `match-report.md`.
4. **Review and approve** the resume. Edit it by hand or ask the writer to adjust it. Read the gaps
   and the amortized items in the match report.
5. Tell `resume-pdf` the resume is approved. It writes the PDF next to it, named with the pattern in
   `profile/01-identity.md`.

## Requirements

- Run your AI tool **from the repository root**: the agents use relative paths.
- A filled-in `profile/` (the agents stop and ask you to create it otherwise).
- For `resume-pdf`: the `resume` extra (ReportLab), installed with `.venv/bin/pip install -e '.[resume]'`
  (the `dev` extra already includes it).
- Optional: Poppler (`pdfinfo`, `pdftotext`) so `resume-pdf` can verify the page count and the text.

## Usage per tool

Each tool reads its own folder in this repository. The exact wording of an invocation can differ
between versions; check the tool's documentation if a command below does not work.

### Claude Code

Agents live in `.claude/agents/` and are picked up from there. Start `claude` in the repository root
and ask for one by name in plain language (you can also type `@` to pick it from the suggestions):

```text
Use the resume-writer agent with this job description: <paste it here>
```
```text
Use the resume-pdf agent on resumes/acme-backend-engineer/resume.md. I approved it.
```

Docs: <https://code.claude.com/docs/en/sub-agents>

### Codex

Custom agents live in `.codex/agents/` as TOML files. Start Codex in the repository root and ask it to
use the agent:

```text
Spawn the resume-writer agent with this job description: <paste it here>
```

Docs: <https://learn.chatgpt.com/docs/agent-configuration/subagents>

### OpenCode

Subagents live in `.opencode/agents/`. Start `opencode` in the repository root and ask the main agent
to use one:

```text
Use the resume-writer subagent with this job description: <paste it here>
```
```text
Use the resume-pdf subagent on resumes/acme-backend-engineer/resume.md. I approved it.
```

The wrappers set simple permissions: `resume-writer` can edit files but not run shell commands;
`resume-pdf` can run shell commands but not edit files. Docs: <https://opencode.ai/docs/agents/>

### pi

pi has no built-in subagents: `.pi/agents/` is read by pi's **subagent extension**, and only for
project agents. Install that extension (see pi's extension docs and examples) and let it use project
agents (its `agentScope` set to `"project"` or `"both"`). Then start pi in the repository root and ask
for the `resume-writer` or `resume-pdf` agent with your job description or approved resume path.
Without the extension, you can still ask pi to read `agents/resume-writer.md` and follow it.

## How it is organized

- `agents/resume-writer.md` and `agents/resume-pdf.md` are the **canonical instructions**: plain
  Markdown with the method, rules and Markdown contract.
- `.claude/agents/`, `.codex/agents/`, `.opencode/agents/` and `.pi/agents/` hold **thin wrappers**: a
  few lines of tool-specific configuration (name, description, allowed tools) that tell the tool to read
  the canonical file and follow it.

To change what an agent does, **edit `agents/*.md`**; the wrappers only point to it.
`tests/test_agents.py` checks that every wrapper exists, parses and points to an existing canonical file.

### Using another tool

Create that tool's agent or custom-instructions file and make it say: "Read `agents/<name>.md` from the
repository root and follow it exactly; `profile/06-cv-rules.md` rules override it." Give it permission
to read and write files (`resume-writer`) or to read files and run shell commands (`resume-pdf`).

## Privacy

Your profile and resumes stay in git-ignored folders on your machine. Their content is sent only to the
AI provider that the tool you chose uses, as with any prompt you give it.
